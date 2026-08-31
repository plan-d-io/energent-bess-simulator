"""Durable job files: atomic status, JSONL events, and a readable log."""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from btm_sim.progress import (
    STATUS_SCHEMA_VERSION,
    ProgressEvent,
    iso_utc,
    utc_now,
)

STATUS_FILENAME = "run_status.json"
EVENTS_FILENAME = "run_events.jsonl"
LOG_FILENAME = "run.log"
REQUEST_FILENAME = "run_request.json"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a temporary file and replace so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class JobSession:
    """Owns ``run_status.json``, ``run_events.jsonl``, and ``run.log`` for one run."""

    def __init__(self, output_dir: Path, job_id: str, *, worker_pid: int | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.job_id = job_id
        self.worker_pid = os.getpid() if worker_pid is None else worker_pid
        self.started_at = utc_now()
        self._status_path = self.output_dir / STATUS_FILENAME
        self._events_path = self.output_dir / EVENTS_FILENAME
        self._log_path = self.output_dir / LOG_FILENAME
        self._events: TextIO | None = None
        self._log: TextIO | None = None
        self._payload: dict[str, Any] = {}

    @classmethod
    def create(cls, output_dir: Path, job_id: str) -> JobSession:
        session = cls(Path(output_dir), job_id)
        session.output_dir.mkdir(parents=True, exist_ok=True)
        session._open_files()
        session._payload = session._base_payload(state="queued", message="Run queued")
        session._write_status()
        session.write_log("Run queued")
        session._payload["state"] = "running"
        session._payload["message"] = "Run started"
        session._payload["updated_at_utc"] = iso_utc()
        session._write_status()
        session.write_log("Run started")
        return session

    def _open_files(self) -> None:
        self._events = self._events_path.open("a", encoding="utf-8")
        self._log = self._log_path.open("a", encoding="utf-8")

    def _base_payload(self, *, state: str, message: str) -> dict[str, Any]:
        now = iso_utc(self.started_at)
        return {
            "status_schema_version": STATUS_SCHEMA_VERSION,
            "job_id": self.job_id,
            "state": state,
            "stage_key": None,
            "stage_number": None,
            "stage_total": None,
            "message": message,
            "started_at_utc": now,
            "updated_at_utc": now,
            "completed_at_utc": None,
            "worker_pid": self.worker_pid,
            "elapsed_seconds": 0.0,
            "output_dir": str(self.output_dir),
            "artifact_schema_version": None,
            "error_category": None,
            "error_message": None,
        }

    def emit(self, event: ProgressEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        if self._events is None:
            self._open_files()
        assert self._events is not None
        self._events.write(line + "\n")
        self._events.flush()
        os.fsync(self._events.fileno())
        self.write_log(self._log_line(event))
        self._payload["stage_key"] = event.stage_key
        self._payload["stage_number"] = event.stage_number
        self._payload["stage_total"] = event.stage_total
        self._payload["message"] = event.message
        self._payload["updated_at_utc"] = event.event_time_utc
        self._payload["elapsed_seconds"] = self.elapsed_seconds()
        if event.state == "failed" or event.level == "error":
            self._payload["error_message"] = event.message
        self._write_status()

    def write_log(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self._log is None:
            self._open_files()
        assert self._log is not None
        self._log.write(f"{stamp} {message}\n")
        self._log.flush()
        os.fsync(self._log.fileno())

    def write_exception(self, exc: BaseException) -> None:
        self.write_log(f"ERROR {type(exc).__name__}: {exc}")
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        for line in text.rstrip().splitlines():
            self.write_log(line)

    def complete(self, *, artifact_schema_version: int, message: str = "Run completed") -> None:
        now = iso_utc()
        self._payload["state"] = "completed"
        self._payload["message"] = message
        self._payload["updated_at_utc"] = now
        self._payload["completed_at_utc"] = now
        self._payload["elapsed_seconds"] = self.elapsed_seconds()
        self._payload["artifact_schema_version"] = artifact_schema_version
        self._payload["error_category"] = None
        self._payload["error_message"] = None
        self._write_status()
        self.write_log(message)

    def fail(self, category: str, message: str) -> None:
        now = iso_utc()
        self._payload["state"] = "failed"
        self._payload["message"] = message
        self._payload["updated_at_utc"] = now
        self._payload["completed_at_utc"] = now
        self._payload["elapsed_seconds"] = self.elapsed_seconds()
        self._payload["error_category"] = category
        self._payload["error_message"] = message
        self._write_status()
        self.write_log(f"FAILED [{category}] {message}")

    def elapsed_seconds(self) -> float:
        return round((utc_now() - self.started_at).total_seconds(), 3)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._payload)

    def close(self) -> None:
        for handle in (self._events, self._log):
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                except OSError:
                    pass
        self._events = None
        self._log = None

    def _write_status(self) -> None:
        atomic_write_json(self._status_path, self._payload)

    @staticmethod
    def _log_line(event: ProgressEvent) -> str:
        prefix = event.level.upper()
        return (
            f"{prefix} [{event.stage_number}/{event.stage_total} {event.stage_key} {event.state}] "
            f"{event.message}"
        )


class JobFileProgress:
    def __init__(self, session: JobSession) -> None:
        self.session = session

    def emit(self, event: ProgressEvent) -> None:
        self.session.emit(event)
