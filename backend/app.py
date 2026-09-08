from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
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


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "PDFMathTranslate-web"}


@app.get("/api/config/engines")
async def get_engines():
    """Returns available translation engines and field definitions"""
    try:
        engines = TranslationAdapter.get_supported_engines()
        return {"engines": engines}
    except Exception as e:
        logger.error(f"Failed to get engines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
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
async def stream_translation(req: TranslationRequest):
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

    return EventSourceResponse(event_generator())


@app.post("/api/cancel/{session_id}")
async def cancel_translation(session_id: str):
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


# Serve built frontend SPA if dist/ exists
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
