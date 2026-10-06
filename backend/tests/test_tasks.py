"""Task history: the sidecar store, its reconciler, and the HTTP surface.

Run with:  .venv/bin/python -m pytest backend/tests -q
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import app as app_module  # noqa: E402
from backend.tasks import (  # noqa: E402
    CANCELLED,
    FAILED,
    FINISHED,
    INTERRUPTED,
    RUNNING,
    TaskStore,
    classify_artifact,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path) -> TaskStore:
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    uploads.mkdir()
    outputs.mkdir()
    return TaskStore(outputs, uploads, retention_days=0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    uploads.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(app_module, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(app_module, "OUTPUT_DIR", outputs)
    monkeypatch.setattr(app_module, "RETENTION_DAYS", 0)
    app_module.active_tasks.clear()
    with TestClient(app_module.app) as test_client:
        test_client.uploads = uploads  # type: ignore[attr-defined]
        test_client.outputs = outputs  # type: ignore[attr-defined]
        yield test_client
    app_module.active_tasks.clear()


def seed(
    outputs: Path,
    session_id: str,
    *,
    status: str = FINISHED,
    artifacts: tuple[str, ...] = ("a.zh-CN.mono.pdf", "a.zh-CN.dual.pdf"),
    file_id: str | None = None,
    created_at: float | None = None,
    write_sidecar: bool = True,
) -> Path:
    directory = outputs / session_id
    directory.mkdir(parents=True, exist_ok=True)
    for name in artifacts:
        (directory / name).write_bytes(b"x" * 32)
    if write_sidecar:
        payload = {
            "session_id": session_id,
            "status": status,
            "filename": "sample.pdf",
            "file_id": file_id,
            "lang_in": "en",
            "lang_out": "zh-CN",
            "engine_type": "OpenAI",
            "created_at": created_at if created_at is not None else time.time(),
            "updated_at": created_at if created_at is not None else time.time(),
        }
        (directory / "task.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# artifact classification + basic IO
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("doc.zh-CN.mono.pdf", "mono"),
        ("doc.zh-CN.dual.pdf", "dual"),
        ("auto_extracted_glossary.csv", "glossary"),
        ("something.txt", "other"),
    ],
)
def test_classify_artifact(name, kind):
    assert classify_artifact(name) == kind


def test_task_json_is_excluded_from_artifacts(store):
    session = "a" * 32
    store.start(session)
    assert store.scan_artifacts(session) == []
    store.finish(session)
    assert all(a["name"] != "task.json" for a in store.scan_artifacts(session))


def test_write_is_atomic_and_leaves_no_temp_file(store):
    session = "b" * 32
    store.start(session)
    store.finish(session)
    leftovers = list(store.session_dir(session).glob("*.tmp"))
    assert leftovers == []
    assert json.loads((store.session_dir(session) / "task.json").read_text())["status"] == FINISHED


def test_repeated_writes_merge_instead_of_clobbering(store):
    session = "c" * 32
    store.start(session, filename="a.pdf", engine_type="OpenAI")
    store.write(session, note="extra")
    store.finish(session)
    record = store.read(session)
    assert record["filename"] == "a.pdf"
    assert record["engine_type"] == "OpenAI"
    assert record["note"] == "extra"
    assert record["status"] == FINISHED
    assert record["created_at"] <= record["updated_at"]


# ---------------------------------------------------------------------------
# reconciliation: the filesystem is the truth
# ---------------------------------------------------------------------------
def test_session_without_sidecar_is_still_listed(store):
    """Sessions created before this feature existed must not disappear."""
    seed(store.outputs_dir, "d" * 32, write_sidecar=False)
    tasks, total, _bytes = store.list()
    assert total == 1
    assert tasks[0]["status"] == FINISHED
    assert len(tasks[0]["artifacts"]) == 2


def test_finished_task_whose_files_vanished_is_reported_interrupted(store):
    directory = seed(store.outputs_dir, "e" * 32, artifacts=())
    assert (directory / "task.json").exists()
    task = store.get("e" * 32)
    assert task["status"] == INTERRUPTED


def test_stale_running_sidecar_is_not_reported_as_running(store):
    """A killed container cannot have a live task; it must not look pending."""
    seed(store.outputs_dir, "f" * 32, status=RUNNING)
    task = store.get("f" * 32)  # nothing in running_ids
    assert task["status"] == FINISHED  # artifacts exist, so it clearly completed
    assert task["running"] is False


def test_active_task_is_reported_running(store):
    seed(store.outputs_dir, "0" * 32, status=RUNNING, artifacts=())
    task = store.get("0" * 32, running_ids=["0" * 32])
    assert task["status"] == RUNNING
    assert task["running"] is True


def test_mark_interrupted_fixes_stale_running_sidecars(store):
    seed(store.outputs_dir, "1" * 32, status=RUNNING, artifacts=())
    seed(store.outputs_dir, "2" * 32, status=FINISHED)
    assert store.mark_interrupted() == 1
    assert store.read("1" * 32)["status"] == INTERRUPTED
    assert store.read("2" * 32)["status"] == FINISHED


def test_listing_sorts_newest_first_and_reports_total_bytes(store):
    seed(store.outputs_dir, "3" * 32, created_at=time.time() - 100, artifacts=("a.mono.pdf",))
    seed(store.outputs_dir, "4" * 32, created_at=time.time(), artifacts=("a.mono.pdf", "b.dual.pdf"))
    tasks, total, total_bytes = store.list()
    assert [t["session_id"] for t in tasks] == ["4" * 32, "3" * 32]
    assert total == 2
    assert total_bytes == 32 * 3


def test_pagination_slices_but_totals_cover_everything(store):
    for index in range(5):
        seed(store.outputs_dir, f"{index}" * 32, created_at=time.time() + index)
    page, total, total_bytes = store.list(limit=2, offset=0)
    assert len(page) == 2 and total == 5
    assert total_bytes == 32 * 2 * 5  # 5 tasks x 2 artifacts


def test_source_availability_is_reported(store):
    file_id = "9" * 32 + "_sample.pdf"
    store.uploads_dir.joinpath(file_id).write_bytes(b"pdf")
    seed(store.outputs_dir, "5" * 32, file_id=file_id)
    assert store.get("5" * 32)["source_available"] is True

    seed(store.outputs_dir, "6" * 32, file_id="8" * 32 + "_gone.pdf")
    assert store.get("6" * 32)["source_available"] is False


# ---------------------------------------------------------------------------
# deletion
# ---------------------------------------------------------------------------
def test_delete_removes_artifacts_and_source(store):
    file_id = "7" * 32 + "_sample.pdf"
    store.uploads_dir.joinpath(file_id).write_bytes(b"p" * 100)
    seed(store.outputs_dir, "a1" * 16, file_id=file_id)

    result = store.delete("a1" * 16)
    assert not store.session_dir("a1" * 16).exists()
    assert not (store.uploads_dir / file_id).exists()
    assert result["source_removed"] is True
    assert result["freed_bytes"] == 32 * 2 + 100


def test_delete_refuses_while_running(store):
    seed(store.outputs_dir, "a2" * 16, status=RUNNING)
    with pytest.raises(PermissionError):
        store.delete("a2" * 16, running_ids=["a2" * 16])
    assert store.session_dir("a2" * 16).exists()


def test_delete_missing_task_raises(store):
    with pytest.raises(FileNotFoundError):
        store.delete("deadbeef" * 4)


def test_delete_keeps_a_source_another_task_still_needs(store):
    """Re-running the same upload creates a second session; deleting one must
    not remove the original out from under the other."""
    file_id = "aa" * 16 + "_shared.pdf"
    store.uploads_dir.joinpath(file_id).write_bytes(b"p" * 50)
    seed(store.outputs_dir, "b1" * 16, file_id=file_id)
    seed(store.outputs_dir, "b2" * 16, file_id=file_id)

    store.delete("b1" * 16)
    assert (store.uploads_dir / file_id).exists()

    store.delete("b2" * 16)
    assert not (store.uploads_dir / file_id).exists()


def test_delete_cannot_escape_the_outputs_directory(store):
    """file_id comes from a json file, so a hand-edited path must not delete
    anything outside data/uploads."""
    outside = store.uploads_dir.parent / "victim.txt"
    outside.write_text("keep me")
    seed(store.outputs_dir, "b3" * 16, file_id="../victim.txt")

    store.delete("b3" * 16)
    assert outside.exists(), "path traversal escaped data/uploads"


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------
def test_cleanup_disabled_by_default(store):
    seed(store.outputs_dir, "c1" * 16, created_at=time.time() - 400 * 86400)
    result = store.cleanup()
    assert result["enabled"] is False
    assert store.session_dir("c1" * 16).exists()


def test_cleanup_removes_old_and_spares_recent(store):
    old = time.time() - 30 * 86400
    seed(store.outputs_dir, "c2" * 16, created_at=old)
    seed(store.outputs_dir, "c3" * 16, created_at=time.time())
    result = store.cleanup(older_than_days=7)
    assert result["tasks"] == 1
    assert not store.session_dir("c2" * 16).exists()
    assert store.session_dir("c3" * 16).exists()


def test_cleanup_never_touches_a_running_task(store):
    seed(store.outputs_dir, "c4" * 16, status=RUNNING, artifacts=(), created_at=time.time() - 99 * 86400)
    store.cleanup(older_than_days=1)
    assert store.session_dir("c4" * 16).exists()


def test_cleanup_removes_orphan_uploads_but_keeps_referenced_ones(store):
    referenced = "dd" * 16 + "_used.pdf"
    orphan = "ee" * 16 + "_abandoned.pdf"
    recent = "ff" * 16 + "_fresh.pdf"
    for name in (referenced, orphan, recent):
        store.uploads_dir.joinpath(name).write_bytes(b"p" * 10)
    seed(store.outputs_dir, "c5" * 16, file_id=referenced, created_at=time.time())

    old = time.time() - 30 * 86400
    import os

    os.utime(store.uploads_dir / orphan, (old, old))
    os.utime(store.uploads_dir / referenced, (old, old))

    result = store.cleanup(older_than_days=7)
    assert result["orphan_uploads"] == 1
    assert (store.uploads_dir / referenced).exists(), "in-use upload was deleted"
    assert not (store.uploads_dir / orphan).exists()
    assert (store.uploads_dir / recent).exists(), "a fresh upload must survive"


def test_cleanup_reports_freed_bytes(store):
    seed(store.outputs_dir, "c6" * 16, created_at=time.time() - 30 * 86400)
    result = store.cleanup(older_than_days=7)
    assert result["freed_bytes"] == 32 * 2


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------
def test_tasks_listing_shape(client):
    seed(client.outputs, "f1" * 16, file_id="ab" * 16 + "_sample.pdf")
    (client.uploads / ("ab" * 16 + "_sample.pdf")).write_bytes(b"pdf")

    body = client.get("/api/tasks").json()
    assert body["total"] == 1
    task = body["tasks"][0]
    assert task["status"] == FINISHED
    assert task["filename"] == "sample.pdf"
    assert task["source_available"] is True
    assert task["cancel_url"] == f"/api/cancel/{'f1' * 16}"
    assert task["delete_url"] == f"/api/tasks/{'f1' * 16}"
    kinds = {a["kind"]: a for a in task["artifacts"]}
    assert set(kinds) == {"mono", "dual"}
    assert kinds["mono"]["url"] == f"/api/files/{'f1' * 16}/a.zh-CN.mono.pdf"
    assert body["total_bytes"] == 64


def test_task_detail_and_404(client):
    seed(client.outputs, "f2" * 16)
    assert client.get(f"/api/tasks/{'f2' * 16}").status_code == 200
    assert client.get(f"/api/tasks/{'f3' * 16}").status_code == 404


def test_delete_endpoint_reclaims_disk(client):
    file_id = "ac" * 16 + "_sample.pdf"
    (client.uploads / file_id).write_bytes(b"p" * 10)
    seed(client.outputs, "f4" * 16, file_id=file_id)

    response = client.delete(f"/api/tasks/{'f4' * 16}")
    assert response.status_code == 200, response.text
    assert not (client.outputs / ("f4" * 16)).exists()
    assert not (client.uploads / file_id).exists()
    assert client.get("/api/tasks").json()["total"] == 0


def test_delete_running_task_returns_409(client):
    seed(client.outputs, "f5" * 16, status=RUNNING, artifacts=())
    app_module.active_tasks["f5" * 16] = None  # type: ignore[assignment]
    try:
        response = client.delete(f"/api/tasks/{'f5' * 16}")
        assert response.status_code == 409
        assert "正在运行" in response.json()["detail"]
    finally:
        app_module.active_tasks.clear()


def test_delete_missing_task_returns_404(client):
    assert client.delete(f"/api/tasks/{'f6' * 16}").status_code == 404


def test_cleanup_requires_a_window_or_configuration(client):
    response = client.post("/api/tasks/cleanup", json={})
    assert response.status_code == 400
    assert "TASK_RETENTION_DAYS" in response.json()["detail"]


def test_cleanup_dry_run_deletes_nothing(client):
    seed(client.outputs, "f7" * 16, created_at=time.time() - 30 * 86400)
    response = client.post("/api/tasks/cleanup", json={"older_than_days": 7, "dry_run": True})
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True and body["tasks"] == 1
    assert (client.outputs / ("f7" * 16)).exists(), "dry run must not delete"


def test_cleanup_endpoint_honours_explicit_window(client):
    seed(client.outputs, "f8" * 16, created_at=time.time() - 30 * 86400)
    seed(client.outputs, "f9" * 16, created_at=time.time())
    body = client.post("/api/tasks/cleanup", json={"older_than_days": 7}).json()
    assert body["tasks"] == 1
    assert not (client.outputs / ("f8" * 16)).exists()
    assert (client.outputs / ("f9" * 16)).exists()


# ---------------------------------------------------------------------------
# auth: a leaked URL must no longer be enough
# ---------------------------------------------------------------------------
def test_download_endpoints_require_auth_when_enabled(client, monkeypatch):
    seed(client.outputs, "aa1" * 11)
    monkeypatch.setattr(app_module, "load_auth_users", lambda: {"u": "p"})

    assert client.get(f"/api/files/{'aa1' * 11}/a.zh-CN.mono.pdf").status_code == 401
    assert client.get(f"/api/tasks").status_code == 401
    assert client.delete(f"/api/tasks/{'aa1' * 11}").status_code == 401


def test_download_endpoints_accept_the_query_token(client, monkeypatch):
    """iframes cannot send headers, so the token must work as a query param."""
    seed(client.outputs, "aa2" * 11)
    monkeypatch.setattr(app_module, "load_auth_users", lambda: {"u": "p"})
    token = app_module.create_token("u")

    response = client.get(f"/api/files/{'aa2' * 11}/a.zh-CN.mono.pdf?token={token}")
    assert response.status_code == 200, response.text
    assert client.get(f"/api/uploads/{'zz' * 16}_x.pdf?token={token}").status_code == 404


def test_no_auth_configured_still_allows_downloads(client, monkeypatch):
    """Backwards compatible: the open mode must behave exactly as before."""
    seed(client.outputs, "aa3" * 11)
    monkeypatch.setattr(app_module, "load_auth_users", lambda: {})
    assert client.get(f"/api/files/{'aa3' * 11}/a.zh-CN.mono.pdf").status_code == 200


# ---------------------------------------------------------------------------
# lifecycle through the real endpoint
# ---------------------------------------------------------------------------
def test_translate_endpoint_records_the_whole_lifecycle(client, monkeypatch):
    """The SSE handler must write running -> finished for the history to be useful."""
    from backend import app as mod

    file_id = "af" * 16 + "_sample.pdf"
    (client.uploads / file_id).write_bytes(b"pdf bytes")

    async def fake_translate_stream(settings, file_path):
        yield {"event": "progress", "data": {"stage": "Parse", "progress": 10}}
        yield {
            "event": "finish",
            "data": {
                "mono_pdf_path": str(client.outputs / "pending" / "sample.zh-CN.mono.pdf"),
                "dual_pdf_path": None,
                "glossary_path": None,
            },
        }

    monkeypatch.setattr(mod.TranslationAdapter, "build_settings", classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(
        mod.TranslationAdapter, "translate_stream", staticmethod(fake_translate_stream)
    )

    with client.stream(
        "POST",
        "/api/translate/stream",
        json={"file_id": file_id, "lang_in": "en", "lang_out": "zh-CN", "engine_type": "OpenAI"},
    ) as response:
        assert response.status_code == 200
        payload = "".join(response.iter_text())

    assert '"event": "session"' in payload or "session" in payload
    tasks = client.get("/api/tasks").json()["tasks"]
    assert len(tasks) == 1, tasks
    task = tasks[0]
    assert task["filename"] == "sample.pdf"
    assert task["engine_type"] == "OpenAI"
    assert task["lang_out"] == "zh-CN"
    # fake_translate_stream wrote nothing, so the reconciler reports interrupted
    assert task["status"] in (FINISHED, INTERRUPTED)


def test_engine_config_secrets_are_never_persisted(client, monkeypatch):
    from backend import app as mod

    file_id = "a0" * 16 + "_sample.pdf"
    (client.uploads / file_id).write_bytes(b"pdf")

    async def empty_stream(settings, file_path):
        if False:  # pragma: no cover
            yield {}

    monkeypatch.setattr(mod.TranslationAdapter, "build_settings", classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(mod.TranslationAdapter, "translate_stream", staticmethod(empty_stream))

    with client.stream(
        "POST",
        "/api/translate/stream",
        json={
            "file_id": file_id,
            "engine_type": "OpenAI",
            "engine_config": {"openai_api_key": "sk-SUPER-SECRET", "openai_model": "gpt-4o-mini"},
        },
    ) as response:
        list(response.iter_text())

    raw = "".join(
        p.read_text(encoding="utf-8") for p in client.outputs.rglob("task.json")
    )
    assert raw, "no task.json was written"
    assert "SUPER-SECRET" not in raw
    assert "sk-" not in raw
    assert "openai_api_key" not in raw


def test_failed_translation_is_recorded_as_failed(client, monkeypatch):
    from backend import app as mod

    file_id = "a1" * 16 + "_sample.pdf"
    (client.uploads / file_id).write_bytes(b"pdf")

    async def boom(settings, file_path):
        yield {"event": "progress", "data": {"stage": "Parse", "progress": 1}}
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(mod.TranslationAdapter, "build_settings", classmethod(lambda cls, **kw: object()))
    monkeypatch.setattr(mod.TranslationAdapter, "translate_stream", staticmethod(boom))

    with client.stream(
        "POST", "/api/translate/stream", json={"file_id": file_id, "engine_type": "OpenAI"}
    ) as response:
        body = "".join(response.iter_text())

    assert "engine exploded" in body
    task = client.get("/api/tasks").json()["tasks"][0]
    assert task["status"] == FAILED
    assert "engine exploded" in (task.get("error") or "")


def test_rejected_settings_leave_no_history_entry(client, monkeypatch):
    from backend import app as mod

    file_id = "a2" * 16 + "_sample.pdf"
    (client.uploads / file_id).write_bytes(b"pdf")

    def bad_settings(**kwargs):
        raise ValueError("Unsupported translation engine: Nope")

    monkeypatch.setattr(mod.TranslationAdapter, "build_settings", staticmethod(bad_settings))

    response = client.post(
        "/api/translate/stream", json={"file_id": file_id, "engine_type": "Nope"}
    )
    assert response.status_code == 400
    assert client.get("/api/tasks").json()["total"] == 0, "empty session dir leaked into history"


def test_cancelled_status_is_cancelled(client):
    """store.cancel() is what the SSE CancelledError branch calls."""
    seed(client.outputs, "a3" * 16, status=RUNNING, artifacts=("a.zh-CN.mono.pdf",))
    app_module.task_store().cancel("a3" * 16)
    task = client.get(f"/api/tasks/{'a3' * 16}").json()
    assert task["status"] == CANCELLED
    assert len(task["artifacts"]) == 1


def test_cleanup_refuses_to_delete_a_live_task_even_without_running_ids(store):
    """Regression: cleanup() used to reclassify a RUNNING sidecar as interrupted
    and delete a live job's directory out from under the running pipeline."""
    directory = store.session_dir("fb" * 16)
    directory.mkdir(parents=True)
    (directory / "task.json").write_text(
        json.dumps({"session_id": "fb" * 16, "status": RUNNING, "created_at": time.time() - 99 * 86400})
    )
    result = store.cleanup(older_than_days=1)
    assert result["tasks"] == 0
    assert directory.exists(), "a running task's directory was deleted"

    # ...and the same when the caller does pass the active id
    result = store.cleanup(older_than_days=1, running_ids=["fb" * 16])
    assert result["tasks"] == 0
    assert directory.exists()


def test_cleanup_endpoint_spares_a_running_task(client):
    seed(client.outputs, "fc" * 16, status=RUNNING, artifacts=(), created_at=time.time() - 99 * 86400)
    app_module.active_tasks["fc" * 16] = None  # type: ignore[assignment]
    try:
        body = client.post("/api/tasks/cleanup", json={"older_than_days": 1}).json()
        assert body["tasks"] == 0
        assert (client.outputs / ("fc" * 16)).exists()
    finally:
        app_module.active_tasks.clear()


def test_explicit_zero_means_clear_history(store):
    """0 is a deliberate 'delete everything finished', not 'feature disabled'."""
    seed(store.outputs_dir, "d1" * 16, created_at=time.time())
    result = store.cleanup(older_than_days=0)
    assert result["enabled"] is True
    assert result["tasks"] == 1
    assert not store.session_dir("d1" * 16).exists()

    # while None (no argument) still means "use the configured retention"
    seed(store.outputs_dir, "d2" * 16, created_at=time.time())
    assert store.cleanup()["enabled"] is False
    assert store.session_dir("d2" * 16).exists()


def test_explicit_zero_still_spares_running(store):
    seed(store.outputs_dir, "d3" * 16, status=RUNNING, artifacts=(), created_at=time.time())
    result = store.cleanup(older_than_days=0, running_ids=["d3" * 16])
    assert result["tasks"] == 0
    assert store.session_dir("d3" * 16).exists()


def test_cleanup_endpoint_accepts_explicit_zero(client):
    seed(client.outputs, "d4" * 16, created_at=time.time())
    response = client.post("/api/tasks/cleanup", json={"older_than_days": 0})
    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == 1
    assert client.get("/api/tasks").json()["total"] == 0


def test_cleanup_without_argument_still_requires_configuration(client):
    response = client.post("/api/tasks/cleanup", json={})
    assert response.status_code == 400
    assert "TASK_RETENTION_DAYS" in response.json()["detail"]


def test_inferred_interrupted_always_explains_itself(store):
    """A bare 'interrupted' is not actionable; the reason must be present."""
    seed(store.outputs_dir, "e1" * 16, status=RUNNING, artifacts=(), write_sidecar=True)
    task = store.get("e1" * 16)
    assert task["status"] == INTERRUPTED
    assert task.get("error"), "inferred interruption produced no reason"

    seed(store.outputs_dir, "e2" * 16, status=FINISHED, artifacts=())
    task = store.get("e2" * 16)
    assert task["status"] == INTERRUPTED
    assert "不存在" in (task.get("error") or "")


def test_sweep_reason_survives_reconciliation(store):
    seed(store.outputs_dir, "e3" * 16, status=RUNNING, artifacts=())
    store.mark_interrupted()
    task = store.get("e3" * 16)
    assert task["status"] == INTERRUPTED
    assert "重启" in (task.get("error") or ""), task.get("error")
