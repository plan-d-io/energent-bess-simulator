from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ui.services.status import (
    CLASS_FAILED,
    CLASS_QUEUED,
    CLASS_RUNNING,
    CLASS_UNEXPECTED,
    CLASS_VALIDATING,
    LAUNCH_GRACE_SECONDS,
    STALE_RUNNING_SECONDS,
    classify_job,
    format_elapsed,
    live_elapsed_seconds,
    read_status,
    safe_error_message,
    trusted_status,
    worker_ended_unexpectedly,
)

UTC = timezone.utc


def _job(tmp_path: Path, **extra) -> dict:
    output = tmp_path / "run"
    output.mkdir(exist_ok=True)
    record = {
        "job_id": "btm-test",
        "kind": "comparison",
        "output_dir": str(output),
        "pid": 9,
        "launch_state": "launched",
        "launch_utc": "2026-08-27T16:00:00Z",
        "lock_navigation": True,
    }
    record.update(extra)
    return record


def _write_status(folder: Path, **fields) -> None:
    payload = {
        "job_id": "btm-test",
        "state": "running",
        "output_dir": str(folder),
        "message": "Solving revenue case",
        "stage_number": 4,
        "stage_total": 12,
        "updated_at_utc": "2026-08-27T16:00:02Z",
        "started_at_utc": "2026-08-27T16:00:00Z",
        "artifact_schema_version": 2,
    }
    payload.update(fields)
    (folder / "run_status.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_status_during_grace_is_queued(tmp_path: Path) -> None:
    job = _job(tmp_path)
    now = datetime(2026, 8, 27, 16, 0, 3, tzinfo=UTC)
    assert classify_job(job, now=now, pid_alive=lambda _pid: True, status=None) == CLASS_QUEUED


def test_valid_queued_and_running_status(tmp_path: Path) -> None:
    job = _job(tmp_path)
    folder = Path(job["output_dir"])
    _write_status(folder, state="queued", message="Waiting for worker")
    payload = read_status(folder)
    trusted = trusted_status(job, payload)
    assert trusted is not None
    assert trusted["message"] == "Waiting for worker"
    assert classify_job(job, status=payload, pid_alive=lambda _pid: True) == CLASS_QUEUED
    _write_status(folder, state="running")
    running = trusted_status(job, read_status(folder))
    assert classify_job(job, status=running, pid_alive=lambda _pid: True) == CLASS_RUNNING


def test_elapsed_advances_from_launch_clock(tmp_path: Path) -> None:
    job = _job(tmp_path)
    status = {
        "state": "running",
        "started_at_utc": "2026-08-27T16:00:00Z",
        "elapsed_seconds": 1.0,
    }
    now = datetime(2026, 8, 27, 16, 0, 12, tzinfo=UTC)
    elapsed = live_elapsed_seconds(status, launched_at_utc=job["launch_utc"], now=now)
    assert elapsed == 12.0
    assert format_elapsed(elapsed) == "0:12"
    assert format_elapsed(3661) == "1:01:01"


def test_malformed_status_does_not_crash(tmp_path: Path) -> None:
    job = _job(tmp_path)
    folder = Path(job["output_dir"])
    (folder / "run_status.json").write_text("{not json", encoding="utf-8")
    assert read_status(folder) is None
    assert trusted_status(job, {"job_id": "other", "state": "running", "output_dir": str(folder)}) is None
    assert classify_job(job, now=datetime(2026, 8, 27, 16, 0, 2, tzinfo=UTC), pid_alive=lambda _pid: True) == CLASS_QUEUED


def test_wrong_identity_is_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    folder = Path(job["output_dir"])
    _write_status(folder, job_id="other")
    assert trusted_status(job, read_status(folder)) is None
    _write_status(folder, output_dir=str(tmp_path / "elsewhere"))
    assert trusted_status(job, read_status(folder)) is None


def test_dead_pid_and_stale_status_is_unexpected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    now = datetime(2026, 8, 27, 16, 0, 20, tzinfo=UTC)
    assert STALE_RUNNING_SECONDS == LAUNCH_GRACE_SECONDS == 8.0
    assert worker_ended_unexpectedly(job, None, now=now, pid_alive=lambda _pid: False) is True
    assert classify_job(job, now=now, pid_alive=lambda _pid: False, status=None) == CLASS_UNEXPECTED
    stale = {
        "job_id": "btm-test",
        "state": "running",
        "output_dir": job["output_dir"],
        "updated_at_utc": "2026-08-27T16:00:00Z",
    }
    later = datetime(2026, 8, 27, 16, 0, 9, tzinfo=UTC)
    assert classify_job(job, now=later, pid_alive=lambda _pid: False, status=stale) == CLASS_UNEXPECTED


def test_core_failed_and_completed_are_distinct(tmp_path: Path) -> None:
    job = _job(tmp_path)
    failed = {
        "job_id": "btm-test",
        "state": "failed",
        "output_dir": job["output_dir"],
        "error_message": "Solver failed",
        "error_category": "solver",
    }
    completed = {
        "job_id": "btm-test",
        "state": "completed",
        "output_dir": job["output_dir"],
        "artifact_schema_version": 2,
    }
    assert classify_job(job, status=failed, pid_alive=lambda _pid: False) == CLASS_FAILED
    assert classify_job(job, status=completed, pid_alive=lambda _pid: False) == CLASS_VALIDATING
    assert safe_error_message("Solver failed") == "Solver failed"
    assert "\\" not in safe_error_message("C:\\\\outputs\\\\run")
    assert safe_error_message("C:\\outputs\\run") == "The simulation failed."


def test_missing_diagnostic_paths_do_not_use_cwd(tmp_path: Path, monkeypatch) -> None:
    from ui.services.paths import recorded_path
    from ui.services.status import diagnostic_file_paths, tail_text

    monkeypatch.chdir(tmp_path)
    (tmp_path / "run.log").write_text("cwd-log", encoding="utf-8")
    (tmp_path / "run_request.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_status.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_events.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "worker_stdout.log").write_text("cwd-console", encoding="utf-8")
    assert recorded_path("") is None
    assert recorded_path(None) is None
    assert recorded_path("  ") is None
    job = {
        "output_dir": "",
        "request_path": "",
        "worker_console_path": None,
    }
    assert diagnostic_file_paths(job) == {}
    assert tail_text("") == ""
    assert tail_text(None) == ""
    assert (tmp_path / "run.log").read_text(encoding="utf-8") == "cwd-log"


def test_run_log_tail_shows_newest_lines_first(tmp_path: Path) -> None:
    from ui.services.status import tail_text

    log = tmp_path / "run.log"
    log.write_text("line-a\nline-b\nline-c\n", encoding="utf-8")
    assert tail_text(log) == "line-c\nline-b\nline-a"
