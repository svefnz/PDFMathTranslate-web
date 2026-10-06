"""Translation task history.

Design note: there is no database, and no index file.

``data/outputs/<session_id>/`` already exists and is the durable record -- the
artifacts on disk *are* the history. One small ``task.json`` next to them adds
only what the filesystem cannot tell us: what the job was, and whether it
actually succeeded. That keeps this feature to a few hundred lines with no
schema, no migration and no extra service to run.

Because the directory is authoritative, every read reconciles against it:

* a session dir with no ``task.json`` (created before this feature existed, or
  written by a killed process) is still listed, synthesised from its artifacts;
* a ``task.json`` claiming artifacts that were deleted by hand is corrected.

Security: ``engine_config`` is deliberately never persisted -- it contains API
keys. Only the engine *name* is stored.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("pdf2zh-web.tasks")

TASK_FILENAME = "task.json"

RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
CANCELLED = "cancelled"
#: set on startup for anything still marked RUNNING: the process that owned it
#: is gone, so it can never finish.
INTERRUPTED = "interrupted"

TERMINAL_STATUSES = frozenset({FINISHED, FAILED, CANCELLED, INTERRUPTED})


def _now() -> float:
    return time.time()


def classify_artifact(name: str) -> str:
    """Best-effort kind from the filename upstream produces."""
    lowered = name.lower()
    if ".mono." in lowered:
        return "mono"
    if ".dual." in lowered:
        return "dual"
    if "glossary" in lowered:
        return "glossary"
    return "other"


@dataclass
class TaskStore:
    """Reads and writes ``<outputs>/<session_id>/task.json``."""

    outputs_dir: Path
    uploads_dir: Path
    retention_days: int = 0

    # -- paths --
    def session_dir(self, session_id: str) -> Path:
        return self.outputs_dir / session_id

    def _task_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / TASK_FILENAME

    # -- low level --
    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        """Write via a temp file + rename so a crash cannot leave torn JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def read(self, session_id: str) -> dict[str, Any] | None:
        path = self._task_path(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Unreadable %s (%s); falling back to the filesystem", path, exc)
            return None
        return data if isinstance(data, dict) else None

    def write(self, session_id: str, **fields: Any) -> dict[str, Any]:
        """Merge ``fields`` into the sidecar. Never pass engine_config here."""
        record = self.read(session_id) or {}
        record.update(fields)
        record["session_id"] = session_id
        record["updated_at"] = _now()
        record.setdefault("created_at", record["updated_at"])
        self._atomic_write(self._task_path(session_id), record)
        return record

    # -- filesystem truth --
    def scan_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        directory = self.session_dir(session_id)
        artifacts: list[dict[str, Any]] = []
        try:
            entries = sorted(directory.iterdir())
        except FileNotFoundError:
            return artifacts
        for entry in entries:
            if not entry.is_file() or entry.name == TASK_FILENAME or entry.name.endswith(".tmp"):
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            artifacts.append(
                {"kind": classify_artifact(entry.name), "name": entry.name, "size": size}
            )
        return artifacts

    def source_exists(self, file_id: str | None) -> bool:
        if not file_id:
            return False
        # file_id is "<uuid>_<original name>"; guard against traversal anyway
        if "/" in file_id or "\\" in file_id or file_id in (".", ".."):
            return False
        return (self.uploads_dir / file_id).is_file()

    # -- listing --
    def _session_ids(self) -> list[str]:
        try:
            entries = list(os.scandir(self.outputs_dir))
        except FileNotFoundError:
            return []
        return sorted(entry.name for entry in entries if entry.is_dir())

    def reconcile(self, session_id: str, running_ids: Iterable[str]) -> dict[str, Any]:
        """Merge the sidecar with what is actually on disk."""
        record = self.read(session_id) or {}
        artifacts = self.scan_artifacts(session_id)
        running = session_id in set(running_ids)

        if "created_at" not in record:
            try:
                record["created_at"] = self.session_dir(session_id).stat().st_mtime
            except OSError:
                record["created_at"] = _now()
        record.setdefault("updated_at", record["created_at"])

        if running:
            status = RUNNING
        else:
            status = record.get("status")
            if status not in TERMINAL_STATUSES:
                # No sidecar, or a stale "running" from a killed process: infer,
                # and say why so the history is never a bare "interrupted".
                if artifacts:
                    status = FINISHED
                else:
                    status = INTERRUPTED
                    record.setdefault("error", "任务未完成（进程重启或异常退出，且没有产物）")
            elif status == FINISHED and not artifacts:
                # someone deleted the files by hand
                status = INTERRUPTED
                record["error"] = "产物文件已不存在"
        record["status"] = status
        record["running"] = running
        record["artifacts"] = artifacts
        record["total_bytes"] = sum(a["size"] for a in artifacts)
        record["source_available"] = self.source_exists(record.get("file_id"))
        return record

    def list(
        self,
        running_ids: Iterable[str] = (),
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Return ``(page, total_count, total_bytes_of_all_tasks)``."""
        running_ids = list(running_ids)
        everything = [self.reconcile(sid, running_ids) for sid in self._session_ids()]
        everything.sort(key=lambda t: (-(t.get("created_at") or 0), t.get("session_id", "")))
        total_bytes = sum(t.get("total_bytes", 0) for t in everything)
        page = everything[offset : offset + limit] if limit is not None else everything[offset:]
        return page, len(everything), total_bytes

    def get(self, session_id: str, running_ids: Iterable[str] = ()) -> dict[str, Any] | None:
        if not self.session_dir(session_id).is_dir():
            return None
        return self.reconcile(session_id, running_ids)

    # -- lifecycle --
    def start(self, session_id: str, **fields: Any) -> dict[str, Any]:
        self.session_dir(session_id).mkdir(parents=True, exist_ok=True)
        return self.write(session_id, status=RUNNING, error=None, started_at=_now(), **fields)

    def finish(self, session_id: str, artifacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self.write(
            session_id,
            status=FINISHED,
            error=None,
            finished_at=_now(),
            artifacts=artifacts if artifacts is not None else self.scan_artifacts(session_id),
        )

    def fail(self, session_id: str, error: str) -> dict[str, Any]:
        return self.write(
            session_id,
            status=FAILED,
            error=(error or "")[:1000],
            finished_at=_now(),
        )

    def cancel(self, session_id: str) -> dict[str, Any]:
        return self.write(
            session_id,
            status=CANCELLED,
            finished_at=_now(),
            artifacts=self.scan_artifacts(session_id),
        )

    def mark_interrupted(self) -> int:
        """Called once at startup: nothing can still be running in a fresh process."""
        fixed = 0
        for session_id in self._session_ids():
            record = self.read(session_id)
            if record and record.get("status") == RUNNING:
                self.write(
                    session_id,
                    status=INTERRUPTED,
                    error="服务重启，任务被中断",
                    finished_at=_now(),
                )
                fixed += 1
        if fixed:
            logger.info("Marked %d interrupted task(s) from a previous run", fixed)
        return fixed

    # -- deletion --
    def delete(self, session_id: str, running_ids: Iterable[str] = ()) -> dict[str, Any]:
        """Remove a task's artifacts and its source upload."""
        if session_id in set(running_ids):
            raise PermissionError("任务正在运行，请先取消再删除")

        directory = self.session_dir(session_id)
        if not directory.is_dir():
            raise FileNotFoundError(session_id)

        record = self.read(session_id) or {}
        file_id = record.get("file_id")
        freed = sum(a["size"] for a in self.scan_artifacts(session_id))
        shutil.rmtree(directory, ignore_errors=True)

        source_removed = False
        # Only drop the original when nothing else points at it.
        if file_id and not self._file_id_in_use(file_id, exclude=session_id):
            source = self.uploads_dir / file_id if "/" not in file_id else None
            if source is not None and source.is_file():
                freed += source.stat().st_size
                source.unlink(missing_ok=True)
                source_removed = True
        logger.info("Deleted task %s (freed %d bytes)", session_id, freed)
        return {"session_id": session_id, "freed_bytes": freed, "source_removed": source_removed}

    def _file_id_in_use(self, file_id: str, exclude: str) -> bool:
        for session_id in self._session_ids():
            if session_id == exclude:
                continue
            record = self.read(session_id)
            if record and record.get("file_id") == file_id:
                return True
        return False

    # -- retention --
    def cleanup(
        self,
        older_than_days: int | None = None,
        running_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Delete finished tasks older than the retention window, plus uploads that
        were never translated.

        Running tasks are never touched. The check is deliberately doubled: the
        caller's active set *and* the raw sidecar status, because a task that is
        mid-flight must survive even if the caller forgets to pass its id.
        """
        # None means "use the configured retention"; an explicit 0 means "delete
        # every finished task" (an intentional "clear history"), which is a
        # different thing from the feature being disabled.
        if older_than_days is None:
            days = self.retention_days
            if days <= 0:
                return {"enabled": False, "tasks": 0, "orphan_uploads": 0, "freed_bytes": 0}
        else:
            days = max(0, older_than_days)

        cutoff = _now() - days * 86400
        running_set = set(running_ids)
        deleted_tasks: list[str] = []
        freed = 0

        for session_id in self._session_ids():
            if session_id in running_set:
                continue
            if (self.read(session_id) or {}).get("status") == RUNNING:
                continue
            record = self.reconcile(session_id, running_set)
            if record.get("status") == RUNNING:
                continue
            stamp = record.get("updated_at") or record.get("created_at") or 0
            if stamp and stamp > cutoff:
                continue
            freed += record.get("total_bytes", 0)
            shutil.rmtree(self.session_dir(session_id), ignore_errors=True)
            deleted_tasks.append(session_id)

        # uploads with no task referencing them: uploaded, then abandoned
        referenced = {r.get("file_id") for r in (self.read(s) for s in self._session_ids()) if r}
        orphans = 0
        try:
            upload_entries = list(os.scandir(self.uploads_dir))
        except FileNotFoundError:
            upload_entries = []
        for entry in upload_entries:
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.name in referenced:
                continue
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                freed += entry.stat().st_size
                os.unlink(entry.path)
                orphans += 1
            except OSError as exc:
                logger.warning("Could not remove orphan upload %s: %s", entry.name, exc)

        if deleted_tasks or orphans:
            logger.info(
                "Retention cleanup: %d task(s), %d orphan upload(s), %d bytes freed",
                len(deleted_tasks),
                orphans,
                freed,
            )
        return {
            "enabled": True,
            "retention_days": days,
            "tasks": len(deleted_tasks),
            "deleted_sessions": deleted_tasks,
            "orphan_uploads": orphans,
            "freed_bytes": freed,
        }
