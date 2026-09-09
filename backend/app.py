from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
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

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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


@app.post("/api/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    _user: str | None = Depends(get_current_user),
):
    """Uploads a PDF file and returns a unique file_id"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

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
    }


@app.post("/api/translate/stream")
async def stream_translation(
    req: TranslationRequest,
    _user: str | None = Depends(get_current_user),
):
    """
    Initiates translation and streams progress/results via Server-Sent Events (SSE).
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
            async for event in TranslationAdapter.translate_stream(settings, file_path):
                evt_name = event["event"]
                evt_data = event["data"]

                # Rewrite file paths to web URLs if finished
                if evt_name == "finish":
                    mono_path = evt_data.get("mono_pdf_path")
                    dual_path = evt_data.get("dual_pdf_path")
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


@app.get("/api/files/{session_id}/{filename}")
async def get_translated_file(session_id: str, filename: str):
    """Serves the translated PDF or glossary file"""
    file_path = OUTPUT_DIR / session_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Serve with inline preview support for PDFs
    media_type = "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline" if media_type == "application/pdf" else "attachment",
    )


@app.get("/api/uploads/{file_id}")
async def get_uploaded_file(file_id: str):
    """Serves the uploaded original PDF for inline preview"""
    file_path = UPLOAD_DIR / file_id
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_path.name,
        content_disposition_type="inline",
    )


# Serve built frontend SPA if dist/ exists
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("BACKEND_PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
