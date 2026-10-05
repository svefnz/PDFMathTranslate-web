from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

import sys

# Ensure backend directory is in sys.path
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from backend.adapter import TranslationAdapter
except ImportError:
    from adapter import TranslationAdapter

try:
    from backend.formats import (
        LEGACY_OFFICE_EXTS,
        SUPPORTED_EXTENSIONS,
        describe_formats,
        get_handler,
        run_pipeline,
    )
except ImportError:
    from formats import (  # type: ignore[no-redef]
        LEGACY_OFFICE_EXTS,
        SUPPORTED_EXTENSIONS,
        describe_formats,
        get_handler,
        run_pipeline,
    )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf2zh-web")

app = FastAPI(title="PDFMathTranslate Web API", version="1.0.0")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
OUTPUT_DIR = BASE_DIR / "data" / "outputs"
FRONTEND_DIST = BASE_DIR / "dist"
#: Staging area for in-flight chunked uploads. Kept outside UPLOAD_DIR on
#: purpose so partial files can never be reached through /api/uploads/.
PARTS_DIR = BASE_DIR / "data" / "parts"

# --- large upload support ---------------------------------------------------
#: Cloudflare rejects request bodies over 100 MB on Free/Pro (200 MB Business),
#: which is what produced the "413 Payload Too Large" page on a 120 MB deck.
#: Anything larger than one chunk is now split client-side and reassembled by
#: /api/upload/complete, so the deployment works behind any proxy limit.
_MB = 1024 * 1024
#: Hard server-side ceiling for a single chunk request (also the single-shot
#: upload threshold: files at or below this go through /api/upload untouched).
MAX_CHUNK_BYTES = int(os.environ.get("MAX_CHUNK_MB", "64")) * _MB
#: What we advertise to the browser. Clamped below Cloudflare's 100 MB so the
#: client can never pick a chunk size the edge would reject.
CLIENT_CHUNK_BYTES = min(MAX_CHUNK_BYTES, 90 * _MB)
#: Ceiling for a reassembled file, to stop a chunk series from filling the disk.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "2048")) * _MB
#: The max plausible chunk count for the largest allowed file at the smallest
#: sane chunk size; only used to reject nonsense input.
MAX_CHUNK_COUNT = 100_000
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")

#: How long the segment pipeline may stay silent before it tells the UI it is
#: still waiting. Upstream retries a rate-limited engine silently for up to
#: ~25 minutes, which is indistinguishable from a crash without this.
HEARTBEAT_SECONDS = 10

#: Ceiling for the engine pre-flight (upstream does a live "Hello" translation
#: while building the translator). It is a blocking call with up to ~100 silent
#: retries, so it must never run on the event loop nor last unbounded.
ENGINE_PREFLIGHT_TIMEOUT_S = int(os.environ.get("ENGINE_PREFLIGHT_TIMEOUT_S", "120"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PARTS_DIR.mkdir(parents=True, exist_ok=True)

# Active tasks for cooperative cancellation: session_id -> asyncio.Task
active_tasks: dict[str, asyncio.Task] = {}


class TranslationRequest(BaseModel):
    file_id: str
    lang_in: str = "en"
    lang_out: str = "zh-CN"
    engine_type: str = "OpenAI"
    engine_config: dict[str, Any] = Field(default_factory=dict)
    thread_count: int | None = None
    pages: str | None = None
    advanced_settings: dict[str, Any] = Field(default_factory=dict)


# Authentication setup
SECRET_FILE = BASE_DIR / "data" / ".auth_secret"
if SECRET_FILE.exists():
    SERVER_SECRET = SECRET_FILE.read_text(encoding="utf-8").strip()
else:
    SERVER_SECRET = secrets.token_hex(32)
    try:
        SECRET_FILE.write_text(SERVER_SECRET, encoding="utf-8")
    except Exception:
        pass

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def load_auth_users() -> dict[str, str]:
    """
    Loads allowed users and passwords from:
    1. Environment variable AUTH_USERS (format: "user1:pass1,user2:pass2" or "user1,pass1;user2,pass2")
    2. File specified by AUTH_FILE or default "data/auth.txt"
    Returns a dict mapping username -> password.
    """
    users: dict[str, str] = {}
    env_users = os.environ.get("AUTH_USERS", "").strip()
    if env_users:
        entries = [e.strip() for e in env_users.replace(";", ",").split(",") if e.strip()]
        for entry in entries:
            if ":" in entry:
                u, p = entry.split(":", 1)
                users[u.strip()] = p.strip()
            elif "," in entry:
                u, p = entry.split(",", 1)
                users[u.strip()] = p.strip()

    auth_file_path = os.environ.get("AUTH_FILE", "").strip()
    auth_path = Path(auth_file_path) if auth_file_path else (BASE_DIR / "data" / "auth.txt")
    if auth_path.exists() and auth_path.is_file():
        try:
            lines = auth_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "," in line:
                    u, p = line.split(",", 1)
                    users[u.strip()] = p.strip()
                elif ":" in line:
                    u, p = line.split(":", 1)
                    users[u.strip()] = p.strip()
        except Exception as e:
            logger.error(f"Error reading auth file {auth_path}: {e}")

    return users


def create_token(username: str) -> str:
    expiry = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{username}:{expiry}"
    sig = hmac.new(SERVER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token: str | None) -> str | None:
    """Returns username if valid, None otherwise."""
    if not token:
        return None
    parts = token.strip().split(":")
    if len(parts) != 3:
        return None
    username, expiry_str, sig = parts
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    if time.time() > expiry:
        return None
    payload = f"{username}:{expiry}"
    expected_sig = hmac.new(SERVER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return username


def get_current_user(
    authorization: str | None = Header(None),
    token: str | None = Query(None),
) -> str | None:
    users = load_auth_users()
    if not users:
        # Auth is not enabled, allow all
        return None

    extracted_token = None
    if authorization and authorization.lower().startswith("bearer "):
        extracted_token = authorization[7:].strip()
    elif token:
        extracted_token = token.strip()

    username = verify_token(extracted_token)
    if not username or username not in users:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "PDFMathTranslate-web"}


@app.get("/api/formats")
async def get_formats():
    """Supported upload formats, so the UI never drifts from the backend."""
    return describe_formats()


@app.get("/api/auth/status")
async def get_auth_status(
    authorization: str | None = Header(None),
    token: str | None = Query(None),
):
    users = load_auth_users()
    if not users:
        return {"auth_required": False, "logged_in": True, "username": None}

    extracted_token = None
    if authorization and authorization.lower().startswith("bearer "):
        extracted_token = authorization[7:].strip()
    elif token:
        extracted_token = token.strip()

    username = verify_token(extracted_token)
    if username and username in users:
        return {"auth_required": True, "logged_in": True, "username": username}

    return {"auth_required": True, "logged_in": False, "username": None}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    users = load_auth_users()
    if not users:
        return {"token": "no_auth", "username": "admin"}

    expected_pwd = users.get(req.username.strip())
    if not expected_pwd or not hmac.compare_digest(expected_pwd, req.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(req.username.strip())
    return {"token": token, "username": req.username.strip()}


@app.post("/api/auth/logout")
async def logout():
    return {"status": "ok"}


@app.get("/api/config/engines")
async def get_engines():
    """Returns available translation engines and field definitions"""
    try:
        engines = TranslationAdapter.get_supported_engines()
        return {"engines": engines}
    except Exception as e:
        logger.error(f"Failed to get engines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class CheckOllamaRequest(BaseModel):
    host: str = "http://localhost:11434"


@app.post("/api/check/ollama")
async def check_ollama(req: CheckOllamaRequest):
    """Checks whether Ollama is reachable and returns installed models"""
    import httpx

    host = req.host.strip().rstrip("/") if req.host else "http://localhost:11434"
    if not host.startswith("http://") and not host.startswith("https://"):
        host = f"http://{host}"

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            res = await client.get(f"{host}/api/tags")
            if res.status_code == 200:
                data = res.json()
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return {"ok": True, "models": models}
            return {"ok": False, "error": f"Ollama 响应异常 (HTTP {res.status_code})"}
    except Exception as e:
        return {
            "ok": False,
            "error": f"无法连接到 Ollama 服务 ({type(e).__name__})。请确认已启动且地址正确 (Docker 容器请使用 http://host.docker.internal:11434)",
        }


def _validate_upload_name(filename: str | None) -> str:
    """Return the lowercase suffix, or raise a precise 400 for the caller."""
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    suffix = Path(filename).suffix.lower()
    if suffix in LEGACY_OFFICE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"暂不支持旧版 Office 二进制格式（{suffix}）。"
                f"请先在 Office / WPS 中另存为 {LEGACY_OFFICE_EXTS[suffix]} 后重新上传。"
            ),
        )
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件格式：{suffix or filename}。"
                f"当前支持：{'、'.join(SUPPORTED_EXTENSIONS)}"
            ),
        )
    return suffix


@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    _user: str | None = Depends(get_current_user),
):
    """Uploads a document (PDF or Office/text) and returns a unique file_id"""
    suffix = _validate_upload_name(file.filename)

    file_id = f"{uuid.uuid4().hex}_{file.filename}"
    target_path = UPLOAD_DIR / file_id

    try:
        with target_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="Failed to save file")

    return {
        "file_id": file_id,
        "filename": file.filename,
        "size": target_path.stat().st_size,
        "suffix": suffix,
        "kind": "pdf" if suffix == ".pdf" else "segments",
    }


@app.get("/api/upload/limits")
async def get_upload_limits():
    """Tells the browser how to slice large files.

    Advertised rather than hardcoded in the frontend so a deployment can tune
    it with ``MAX_CHUNK_MB`` without the two sides drifting apart.
    """
    return {
        "chunk_bytes": CLIENT_CHUNK_BYTES,
        "single_shot_threshold_bytes": CLIENT_CHUNK_BYTES,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_upload_mb": MAX_UPLOAD_BYTES // _MB,
    }


def _parts_dir(upload_id: str) -> Path:
    """Resolve the staging directory for one upload, rejecting anything odd.

    ``upload_id`` arrives from the client, so it is restricted to a fixed
    128-bit hex shape before being used as a path segment.
    """
    if not UPLOAD_ID_RE.match(upload_id):
        raise HTTPException(
            status_code=400,
            detail="upload_id 必须是 32 位十六进制字符串",
        )
    return PARTS_DIR / upload_id


def _validate_chunk_request(index: int, total: int) -> None:
    if total < 1 or total > MAX_CHUNK_COUNT:
        raise HTTPException(status_code=400, detail=f"分片总数不合法: {total}")
    if index < 0 or index >= total:
        raise HTTPException(status_code=400, detail=f"分片序号不合法: {index}/{total}")


@app.post("/api/upload/chunk")
async def upload_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    index: int = Form(...),
    total: int = Form(...),
    filename: str = Form(...),
    _user: str | None = Depends(get_current_user),
):
    """Stores one slice of a large file.

    Chunks land in ``data/parts/<upload_id>/`` and are concatenated in order by
    ``/api/upload/complete``. Each request stays well under any proxy limit.
    """
    _validate_upload_name(filename)  # fail fast, before accepting gigabytes
    _validate_chunk_request(index, total)
    folder = _parts_dir(upload_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{index:06d}.part"

    written = 0
    try:
        with target.open("wb") as buffer:
            while chunk := await file.read(_MB):
                written += len(chunk)
                if written > MAX_CHUNK_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"单个分片超过服务端上限 {MAX_CHUNK_BYTES // _MB} MB，"
                            "请调小前端分片大小后重试"
                        ),
                    )
                buffer.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as e:
        logger.error(f"Failed to store chunk {index} of {upload_id}: {e}")
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="保存分片失败")

    return {"ok": True, "index": index, "size": written}


class CompleteUploadRequest(BaseModel):
    upload_id: str
    filename: str
    total: int


@app.post("/api/upload/complete")
async def upload_complete(
    req: CompleteUploadRequest,
    _user: str | None = Depends(get_current_user),
):
    """Concatenates uploaded chunks into a finished upload."""
    suffix = _validate_upload_name(req.filename)
    _validate_chunk_request(0, req.total)
    folder = _parts_dir(req.upload_id)

    missing = [i for i in range(req.total) if not (folder / f"{i:06d}.part").is_file()]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"分片不完整，缺少 {len(missing)} 个（首个缺失序号：{missing[0]}）",
        )

    file_id = f"{uuid.uuid4().hex}_{req.filename}"
    target_path = UPLOAD_DIR / file_id
    try:
        total_bytes = 0
        with target_path.open("wb") as out:
            for index in range(req.total):
                part = folder / f"{index:06d}.part"
                total_bytes += part.stat().st_size
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过上限 {MAX_UPLOAD_BYTES // _MB} MB",
                    )
                with part.open("rb") as src:
                    shutil.copyfileobj(src, out, _MB)
    except HTTPException:
        target_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        logger.error(f"Failed to assemble upload {req.upload_id}: {e}")
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="合并分片失败")
    finally:
        # the staged slices are dead weight once assembled (or once rejected)
        shutil.rmtree(folder, ignore_errors=True)

    return {
        "file_id": file_id,
        "filename": req.filename,
        "size": total_bytes,
        "suffix": suffix,
        "kind": "pdf" if suffix == ".pdf" else "segments",
    }


async def _stream_segment_events(handler, translator, file_path, output_path, queue):
    """Progress events for the non-PDF segment pipeline.

    ``run_pipeline`` reports progress through a synchronous callback, so it
    pushes onto a queue that this generator drains. The queue is only touched
    from the event loop, so no locking is needed.

    Two things this must get right for a big document on a slow model:

    * the batch count is reported *before* the first request goes out, so the UI
      shows a real denominator instead of an apparently frozen 1%;
    * while nothing is coming back, a heartbeat keeps the UI honest about how
      long we have been waiting, because upstream silently retries a
      rate-limited engine for up to ~25 minutes.
    """
    label = handler.label
    loop = asyncio.get_running_loop()
    started = loop.time()

    def progress_event(stage: str, percent: float, current: int, total: int) -> dict:
        return {
            "event": "progress",
            "data": {
                "stage": stage,
                "progress": max(1, min(int(percent), 99)),
                "stage_current": current,
                "stage_total": total,
            },
        }

    yield progress_event(f"解析{label}结构", 1, 0, 0)

    task = asyncio.create_task(
        run_pipeline(
            handler,
            translator,
            file_path,
            output_path,
            on_progress=lambda done, total: queue.put_nowait(("batch", done, total)),
            on_plan=lambda total, unique: queue.put_nowait(("plan", total, unique)),
        )
    )

    plan_total = 0
    done_count = 0
    last_emit = loop.time()

    try:
        while True:
            try:
                kind, a, b = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if task.done():
                    break
                # Nothing has come back for a while: say so, with the elapsed
                # time, instead of leaving a frozen bar on screen.
                silent_for = loop.time() - last_emit
                if silent_for >= HEARTBEAT_SECONDS:
                    waited = int(loop.time() - started)
                    if plan_total:
                        stage = (
                            f"大模型翻译{label}中"
                            f"（已完成 {done_count}/{plan_total} 批，等待响应 {waited}s）"
                        )
                    else:
                        stage = f"正在调用大模型接口（已等待 {waited}s）"
                    percent = 5 + (done_count / plan_total * 90 if plan_total else 0)
                    yield progress_event(stage, percent, done_count, plan_total)
                    last_emit = loop.time()
                    if waited and waited % (HEARTBEAT_SECONDS * 6) < HEARTBEAT_SECONDS:
                        logger.warning(
                            "No batch has completed after %ss. The engine is "
                            "most likely rate limiting or overloaded; upstream "
                            "retries such requests silently for a long time.",
                            waited,
                        )
                continue

            # coalesce whatever else is already queued so the UI is not thrashed
            while not queue.empty():
                kind, a, b = queue.get_nowait()

            if kind == "plan":
                plan_total = a
                yield progress_event(
                    f"已解析{label}，共 {a} 个批次待翻译（{b} 条去重文本）", 5, 0, a
                )
            else:
                done_count = a
                percent = 5 + (a / b * 90 if b else 90)
                yield progress_event(f"大模型翻译{label}中", percent, a, b)
            last_emit = loop.time()

        outcome = task.result()
        yield progress_event("生成输出文档", 99, done_count, plan_total)
        yield {
            "event": "finish",
            "data": {
                "mono_path": str(outcome.output_path),
                "dual_path": None,
                "glossary_path": None,
                "segment_count": outcome.slot_count,
                "translated_count": outcome.translated_count,
            },
        }
    finally:
        if not task.done():
            task.cancel()


@app.post("/api/translate/stream")
async def stream_translation(
    req: TranslationRequest,
    _user: str | None = Depends(get_current_user),
):
    """
    Initiates translation and streams progress/results via Server-Sent Events (SSE).

    Two pipelines share this endpoint:

    * ``.pdf`` -> BabelDOC layout-aware translation through pdf2zh_next
    * everything else -> the generic segment pipeline in ``backend.formats``
    """
    file_path = UPLOAD_DIR / req.file_id
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    session_id = uuid.uuid4().hex
    session_output_dir = OUTPUT_DIR / session_id
    session_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        settings = TranslationAdapter.build_settings(
            lang_in=req.lang_in,
            lang_out=req.lang_out,
            engine_type=req.engine_type,
            engine_config=req.engine_config,
            output_dir=session_output_dir,
            thread_count=req.thread_count,
            pages=req.pages,
            advanced_settings=req.advanced_settings,
        )
    except Exception as e:
        logger.error(f"Failed to build translation settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    producer = None
    if file_path.suffix.lower() != ".pdf":
        handler = get_handler(file_path)
        if handler is None:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式：{file_path.suffix}，请重新上传。",
            )
        try:
            # Build in a worker thread: this performs a live translation as a
            # health check, and doing it inline would block the whole event loop
            # (every other request too) for the engine's entire retry window.
            translator = await asyncio.wait_for(
                asyncio.to_thread(TranslationAdapter.build_segment_translator, settings),
                timeout=ENGINE_PREFLIGHT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("Engine pre-flight timed out after %ss", ENGINE_PREFLIGHT_TIMEOUT_S)
            raise HTTPException(
                status_code=504,
                detail=(
                    f"大模型接口预检超过 {ENGINE_PREFLIGHT_TIMEOUT_S} 秒未响应。"
                    "该接口很可能正在限流或不可用，请检查 API Key / Base URL，"
                    "或改用其他引擎后重试。"
                ),
            )
        except Exception as e:
            logger.error(f"Failed to build segment translator: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        output_path = session_output_dir / (
            f"{file_path.stem}_{req.lang_out}{file_path.suffix.lower()}"
        )
        producer = _stream_segment_events(
            handler,
            translator,
            file_path,
            output_path,
            asyncio.Queue(),
        )

    async def event_generator():
        # Register task for cancellation
        current_task = asyncio.current_task()
        if current_task:
            active_tasks[session_id] = current_task

        # Send initial session metadata
        yield {
            "event": "session",
            "data": json.dumps({"session_id": session_id}),
        }

        try:
            source = (
                TranslationAdapter.translate_stream(settings, file_path)
                if producer is None
                else producer
            )
            async for event in source:
                evt_name = event["event"]
                evt_data = event["data"]

                # Rewrite file paths to web URLs if finished
                if evt_name == "finish":
                    # PDF pipeline reports *_pdf_path, the segment pipeline *_path
                    mono_path = evt_data.get("mono_pdf_path") or evt_data.get("mono_path")
                    dual_path = evt_data.get("dual_pdf_path") or evt_data.get("dual_path")
                    glossary_path = evt_data.get("glossary_path")

                    evt_data["mono_url"] = (
                        f"/api/files/{session_id}/{Path(mono_path).name}" if mono_path else None
                    )
                    evt_data["dual_url"] = (
                        f"/api/files/{session_id}/{Path(dual_path).name}" if dual_path else None
                    )
                    evt_data["glossary_url"] = (
                        f"/api/files/{session_id}/{Path(glossary_path).name}"
                        if glossary_path
                        else None
                    )

                yield {
                    "event": evt_name,
                    "data": json.dumps(evt_data),
                }

        except asyncio.CancelledError:
            logger.info(f"Translation cancelled for session: {session_id}")
            yield {
                "event": "cancelled",
                "data": json.dumps({"session_id": session_id, "message": "Translation cancelled"}),
            }
        except Exception as e:
            logger.error(f"Translation error in session {session_id}: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e), "session_id": session_id}),
            }
        finally:
            active_tasks.pop(session_id, None)

    return EventSourceResponse(
        event_generator(),
        ping=10,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/cancel/{session_id}")
async def cancel_translation(
    session_id: str,
    _user: str | None = Depends(get_current_user),
):
    """Cancels an ongoing translation task"""
    task = active_tasks.get(session_id)
    if not task:
        return {"status": "not_found", "message": "No running task for session"}

    task.cancel()
    return {"status": "cancelled", "session_id": session_id}


#: Media types used when serving uploaded / translated files. Only PDF keeps an
#: inline disposition (the workspace renders it in an iframe); plain-text formats
#: are served inline too so they can be eyeballed, Office files download.
_PREVIEWABLE_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".text": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
}


def _content_type(filename: str) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    media_type = _PREVIEWABLE_MEDIA_TYPES.get(suffix)
    if media_type is None:
        return "application/octet-stream", "attachment"
    return media_type, "inline"


@app.get("/api/files/{session_id}/{filename}")
async def get_translated_file(session_id: str, filename: str):
    """Serves a translated document or the generated glossary file"""
    file_path = OUTPUT_DIR / session_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type, disposition = _content_type(filename)
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        content_disposition_type=disposition,
    )


@app.get("/api/uploads/{file_id}")
async def get_uploaded_file(file_id: str):
    """Serves the uploaded original document"""
    file_path = UPLOAD_DIR / file_id
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type, disposition = _content_type(file_path.name)
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
        content_disposition_type=disposition,
    )


# Serve built frontend SPA if dist/ exists
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("BACKEND_PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
