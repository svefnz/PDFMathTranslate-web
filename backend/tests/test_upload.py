"""Tests for upload validation and the chunked (large file) upload path.

Run with:  .venv/bin/python -m pytest backend/tests -q
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import app as app_module  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    parts = tmp_path / "parts"
    for folder in (uploads, outputs, parts):
        folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_module, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(app_module, "OUTPUT_DIR", outputs)
    monkeypatch.setattr(app_module, "PARTS_DIR", parts)
    # keep the tests hermetic regardless of the machine's auth.txt
    app_module.app.dependency_overrides[app_module.get_current_user] = lambda: None
    with TestClient(app_module.app) as test_client:
        yield test_client
    app_module.app.dependency_overrides.clear()


def _send_chunks(client, name: str, payload: bytes, chunk_size: int, upload_id: str, order=None):
    total = (len(payload) + chunk_size - 1) // chunk_size
    indices = order if order is not None else list(range(total))
    for index in indices:
        blob = payload[index * chunk_size : (index + 1) * chunk_size]
        response = client.post(
            "/api/upload/chunk",
            files={"file": (f"{name}.part{index}", blob, "application/octet-stream")},
            data={
                "upload_id": upload_id,
                "index": str(index),
                "total": str(total),
                "filename": name,
            },
        )
        assert response.status_code == 200, response.text
    return total


# ---------------------------------------------------------------------------
# single-shot upload (unchanged behaviour)
# ---------------------------------------------------------------------------
def test_small_file_upload_roundtrip(client, tmp_path):
    response = client.post("/api/upload", files={"file": ("note.txt", b"hello", "text/plain")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suffix"] == ".txt"
    assert body["kind"] == "segments"
    assert (app_module.UPLOAD_DIR / body["file_id"]).read_bytes() == b"hello"


def test_upload_rejects_legacy_office_with_actionable_hint(client):
    response = client.post("/api/upload", files={"file": ("old.doc", b"x", "application/msword")})
    assert response.status_code == 400
    assert ".docx" in response.json()["detail"]


def test_upload_rejects_unknown_extension(client):
    response = client.post("/api/upload", files={"file": ("evil.exe", b"x", "application/octet-stream")})
    assert response.status_code == 400
    assert "不支持的文件格式" in response.json()["detail"]


# ---------------------------------------------------------------------------
# advertised limits
# ---------------------------------------------------------------------------
def test_advertised_chunk_size_is_safe_behind_cloudflare(client):
    limits = client.get("/api/upload/limits").json()
    # Cloudflare Free/Pro reject >100 MB bodies, so one chunk must stay below it
    assert 0 < limits["chunk_bytes"] <= 90 * 1024 * 1024
    assert limits["single_shot_threshold_bytes"] == limits["chunk_bytes"]
    assert limits["max_upload_bytes"] >= 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# chunked upload
# ---------------------------------------------------------------------------
def test_chunked_upload_assembles_identical_bytes(client):
    payload = bytes(range(256)) * 4096  # 1 MiB of non-repeating-ish data
    upload_id = "a" * 32
    total = _send_chunks(client, "report.docx", payload, 256 * 1024, upload_id)

    response = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "report.docx", "total": total},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["size"] == len(payload)
    assert body["suffix"] == ".docx"
    assert body["kind"] == "segments"
    assert (app_module.UPLOAD_DIR / body["file_id"]).read_bytes() == payload


def test_chunks_may_arrive_out_of_order(client):
    payload = b"ABCDEFGH" * 1000
    upload_id = "b" * 32
    chunk_size = 1000
    total = _send_chunks(
        client, "deck.pptx", payload, chunk_size, upload_id, order=[4, 0, 7, 2, 5, 1, 6, 3]
    )
    response = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "deck.pptx", "total": total},
    )
    assert response.status_code == 200
    assert (app_module.UPLOAD_DIR / response.json()["file_id"]).read_bytes() == payload


def test_missing_chunk_is_reported(client):
    payload = b"z" * 3000
    upload_id = "c" * 32
    _send_chunks(client, "big.pdf", payload, 1000, upload_id, order=[0, 2])
    response = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "big.pdf", "total": 3},
    )
    assert response.status_code == 400
    assert "缺少 1 个" in response.json()["detail"]


def test_complete_validates_filename_extension(client):
    upload_id = "d" * 32
    _send_chunks(client, "x.docx", b"abc", 100, upload_id)
    response = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "x.doc", "total": 1},
    )
    assert response.status_code == 400
    assert ".docx" in response.json()["detail"]


def test_chunk_rejects_legacy_extension_before_storing(client):
    response = client.post(
        "/api/upload/chunk",
        files={"file": ("x.ppt", b"abc", "application/octet-stream")},
        data={"upload_id": "e" * 32, "index": "0", "total": "1", "filename": "x.ppt"},
    )
    assert response.status_code == 400
    assert ".pptx" in response.json()["detail"]
    assert not (app_module.PARTS_DIR / ("e" * 32)).exists()


@pytest.mark.parametrize("upload_id", ["../escape", "short", "Z" * 32, "a" * 31, "", "a/b"])
def test_upload_id_is_path_traversal_safe(client, upload_id):
    response = client.post(
        "/api/upload/chunk",
        files={"file": ("x.txt", b"abc", "text/plain")},
        data={"upload_id": upload_id, "index": "0", "total": "1", "filename": "x.txt"},
    )
    assert response.status_code in (400, 422), response.text
    # nothing may be created outside PARTS_DIR
    assert list(app_module.PARTS_DIR.iterdir()) == []


@pytest.mark.parametrize(
    ("index", "total"),
    [("-1", "3"), ("3", "3"), ("0", "0"), ("0", "100001")],
)
def test_chunk_index_and_total_are_validated(client, index, total):
    response = client.post(
        "/api/upload/chunk",
        files={"file": ("x.txt", b"abc", "text/plain")},
        data={"upload_id": "f" * 32, "index": index, "total": total, "filename": "x.txt"},
    )
    assert response.status_code == 400, response.text


def test_oversized_chunk_is_rejected_and_not_kept(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_CHUNK_BYTES", 1024)
    response = client.post(
        "/api/upload/chunk",
        files={"file": ("x.txt", b"y" * 4096, "text/plain")},
        data={"upload_id": "1" * 32, "index": "0", "total": "1", "filename": "x.txt"},
    )
    assert response.status_code == 413
    assert "单个分片超过" in response.json()["detail"]
    assert not (app_module.PARTS_DIR / ("1" * 32) / "000000.part").exists()


def test_oversized_total_is_rejected_and_staging_cleaned(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 2048)
    upload_id = "2" * 32
    _send_chunks(client, "big.xlsx", b"a" * 4096, 2048, upload_id)
    response = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "big.xlsx", "total": 2},
    )
    assert response.status_code == 413
    assert "超过上限" in response.json()["detail"]
    assert not (app_module.PARTS_DIR / upload_id).exists()
    assert list(app_module.UPLOAD_DIR.iterdir()) == []


def test_staging_directory_is_removed_after_completion(client):
    upload_id = "3" * 32
    total = _send_chunks(client, "a.csv", b"a,b\n1,2\n", 5, upload_id)
    assert (app_module.PARTS_DIR / upload_id).is_dir()
    response = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "a.csv", "total": total},
    )
    assert response.status_code == 200
    assert not (app_module.PARTS_DIR / upload_id).exists()


def test_staging_lives_outside_the_served_uploads_dir(client):
    """Partial chunks must never be reachable through /api/uploads/{file_id}."""
    assert app_module.PARTS_DIR != app_module.UPLOAD_DIR
    assert app_module.UPLOAD_DIR not in app_module.PARTS_DIR.parents
    upload_id = "4" * 32
    _send_chunks(client, "a.txt", b"a" * 100, 50, upload_id)
    assert client.get(f"/api/uploads/{upload_id}/000000.part").status_code == 404


def test_chunked_file_then_translates(client, tmp_path):
    """A chunk-assembled document behaves exactly like a directly uploaded one."""
    from backend.formats import get_handler, run_pipeline
    from backend.formats.base import SegmentTranslator
    import asyncio
    import re

    payload = ("Hello chunked world.\nSecond line here.\n").encode("utf-8")
    upload_id = "5" * 32
    total = _send_chunks(client, "notes.txt", payload, 16, upload_id)
    body = client.post(
        "/api/upload/complete",
        json={"upload_id": upload_id, "filename": "notes.txt", "total": total},
    ).json()

    source = app_module.UPLOAD_DIR / body["file_id"]

    class Fake:
        def llm_translate(self, prompt: str) -> str:
            import json

            items = json.loads(prompt.split("Input JSON array:\n", 1)[1])
            return json.dumps([re.sub(r"[^\W\d_]", lambda m: m.group(0).upper(), i) for i in items])

        def translate(self, text: str) -> str:
            return text.upper()

    dest = tmp_path / "out.txt"
    asyncio.run(run_pipeline(get_handler(source), SegmentTranslator(Fake(), "zh-CN", True), source, dest))
    assert dest.read_text() == "HELLO CHUNKED WORLD.\nSECOND LINE HERE.\n"
