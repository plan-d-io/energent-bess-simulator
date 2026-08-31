"""Read worker status files and classify an active V2 job."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ui.services.paths import recorded_path

STATUS_FILENAME = "run_status.json"
EVENTS_FILENAME = "run_events.jsonl"
LOG_FILENAME = "run.log"
CORE_QUEUED = "queued"
CORE_RUNNING = "running"
CORE_COMPLETED = "completed"
CORE_FAILED = "failed"
CORE_ACTIVE = frozenset({CORE_QUEUED, CORE_RUNNING})
CORE_TERMINAL = frozenset({CORE_COMPLETED, CORE_FAILED})
CORE_STATES = CORE_ACTIVE | CORE_TERMINAL

CLASS_QUEUED = "queued"
CLASS_RUNNING = "running"
CLASS_VALIDATING = "validating"
CLASS_FAILED = "failed"
CLASS_UNEXPECTED = "unexpected"
CLASS_INCOMPLETE = "incomplete"
CLASS_READY = "ready"

STALE_RUNNING_SECONDS = 8.0
LAUNCH_GRACE_SECONDS = STALE_RUNNING_SECONDS
DEFAULT_LOG_TAIL = 80
DEFAULT_LOG_BYTES = 65536

PidCheck = Callable[[int | None], bool]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime | None = None) -> str:
    value = utc_now() if moment is None else moment
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_is_alive(number)
    try:
        os.kill(number, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    if not ok:
        return False
    return int(code.value) == still_active


def read_status(output_dir: str | Path | None) -> dict[str, Any] | None:
    folder = recorded_path(output_dir)
    if folder is None:
        return None
    path = folder / STATUS_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _same_dir(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def trusted_status(job: Mapping[str, Any], payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("job_id") or "") != str(job.get("job_id") or ""):
        return None
    reported = payload.get("output_dir")
    stored = job.get("output_dir")
    if not reported or not stored or not _same_dir(str(reported), str(stored)):
        return None
    state = str(payload.get("state") or "")
    if state not in CORE_STATES:
        return None
    return dict(payload)


def status_age_seconds(status: Mapping[str, Any] | None, *, now: datetime | None = None) -> float | None:
    if not status:
        return None
    updated = parse_utc(status.get("updated_at_utc"))
    if updated is None:
        return None
    current = now or utc_now()
    return max(0.0, (current - updated).total_seconds())


def worker_ended_unexpectedly(
    job: Mapping[str, Any],
    status: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    pid_alive: PidCheck | None = None,
    stale_after: float = STALE_RUNNING_SECONDS,
) -> bool:
    state = str((status or {}).get("state") or "")
    if state in CORE_TERMINAL:
        return False
    if state not in CORE_ACTIVE and status is not None:
        return False
    check = pid_alive or pid_is_alive
    if check(job.get("pid")):
        return False
    current = now or utc_now()
    if status is None:
        launched = parse_utc(job.get("launch_utc")) or current
        return (current - launched).total_seconds() >= stale_after
    age = status_age_seconds(status, now=current)
    if age is None:
        return True
    return age >= stale_after


def classify_job(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    pid_alive: PidCheck | None = None,
    status: Mapping[str, Any] | None = None,
    has_results: bool = False,
) -> str:
    if has_results:
        return CLASS_READY
    check = pid_alive or pid_is_alive
    current = now or utc_now()
    raw = status if status is not None else read_status(job.get("output_dir"))
    trusted = trusted_status(job, raw)
    if trusted and str(trusted.get("state")) == CORE_FAILED:
        return CLASS_FAILED
    if trusted and str(trusted.get("state")) == CORE_COMPLETED:
        return CLASS_VALIDATING
    if worker_ended_unexpectedly(job, trusted, now=current, pid_alive=check):
        return CLASS_UNEXPECTED
    if trusted and str(trusted.get("state")) == CORE_QUEUED:
        return CLASS_QUEUED
    if trusted and str(trusted.get("state")) == CORE_RUNNING:
        return CLASS_RUNNING
    if trusted is None and raw is not None and check(job.get("pid")):
        return CLASS_RUNNING
    launched = parse_utc(job.get("launch_utc")) or current
    age = (current - launched).total_seconds()
    if check(job.get("pid")):
        return CLASS_QUEUED if age < LAUNCH_GRACE_SECONDS else CLASS_RUNNING
    if age < LAUNCH_GRACE_SECONDS:
        return CLASS_QUEUED
    return CLASS_UNEXPECTED


def live_elapsed_seconds(
    status: Mapping[str, Any] | None,
    *,
    launched_at_utc: str | None = None,
    now: datetime | None = None,
) -> float | None:
    current = now or utc_now()
    payload = status or {}
    state = str(payload.get("state") or "")
    started = parse_utc(payload.get("started_at_utc")) or parse_utc(launched_at_utc)
    finished = parse_utc(payload.get("completed_at_utc"))
    reported = payload.get("elapsed_seconds")
    if state in CORE_TERMINAL:
        if reported is not None:
            try:
                return max(0.0, float(reported))
            except (TypeError, ValueError):
                pass
        if started is not None and finished is not None:
            return max(0.0, (finished - started).total_seconds())
        if started is not None:
            return max(0.0, (current - started).total_seconds())
        return None
    if started is not None:
        return max(0.0, (current - started).total_seconds())
    if reported is not None:
        try:
            return max(0.0, float(reported))
        except (TypeError, ValueError):
            return None
    return None


def format_elapsed(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def stage_pair(status: Mapping[str, Any] | None) -> tuple[int, int] | None:
    if not status:
        return None
    try:
        number = int(status.get("stage_number"))
        total = int(status.get("stage_total"))
    except (TypeError, ValueError):
        return None
    if total <= 0 or number < 0 or number > total:
        return None
    return number, total


def safe_error_message(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "The simulation failed."
    if "Traceback" in raw or "\n" in raw:
        return "The simulation failed."
    if "\\" in raw or raw.startswith("/") or ":/" in raw:
        return "The simulation failed."
    return raw


def tail_text(
    path: str | Path | None,
    *,
    max_lines: int = DEFAULT_LOG_TAIL,
    max_bytes: int = DEFAULT_LOG_BYTES,
) -> str:
    file_path = recorded_path(path)
    if file_path is None or not file_path.is_file():
        return ""
    try:
        size = file_path.stat().st_size
        with file_path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            chunk = handle.read(max_bytes)
    except OSError:
        return ""
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(reversed(lines[-max_lines:]))


def diagnostic_file_paths(job: Mapping[str, Any] | None) -> dict[str, Path]:
    """Return existing diagnostic files from stored job paths. Never uses cwd fallbacks."""
    if not isinstance(job, Mapping):
        return {}
    found: dict[str, Path] = {}
    request = recorded_path(job.get("request_path"))
    if request is not None and request.is_file():
        found["request"] = request
    output = recorded_path(job.get("output_dir"))
    if output is not None:
        for key, name in (
            ("status", STATUS_FILENAME),
            ("events", EVENTS_FILENAME),
            ("log", LOG_FILENAME),
        ):
            path = output / name
            if path.is_file():
                found[key] = path
    console = recorded_path(job.get("worker_console_path"))
    if console is not None and console.is_file():
        found["console"] = console
    return found
