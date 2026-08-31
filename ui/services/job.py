"""Atomic live-job launch, duplicate protection and recoverable job records."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from btm_sim.config import standard_defaults_path
from btm_sim.market import standard_day_ahead_prices_path
from btm_sim.run import build_run_request, serialize_run_request, write_run_request
from btm_sim.sweep import build_sweep_request, serialize_sweep_request, write_sweep_request

from ui.services.configure import MODE_SIZE, selected_period_record
from ui.services.paths import (
    KIND_COMPARISON,
    KIND_SWEEP,
    STAGING_FILENAMES,
    WORKER_CONSOLE_FILENAME,
    default_outputs_root,
    default_staging_root,
    output_dir_for,
    project_root,
    request_filename,
    src_dir,
    staging_dir_for,
)
from ui.services.request_intent import builder_kwargs_from_intent, mismatches_for_serialized_request
from ui.services.results import (
    SOURCE_LIVE,
    cleanup_staging_dir,
    result_record,
    results_are_valid,
    validate_result_folder,
)
from ui.services.status import (
    CLASS_FAILED,
    CLASS_INCOMPLETE,
    CLASS_QUEUED,
    CLASS_READY,
    CLASS_RUNNING,
    CLASS_UNEXPECTED,
    CLASS_VALIDATING,
    classify_job,
    iso_utc,
    pid_is_alive,
    read_status,
    trusted_status,
)

JOB_RECORD_VERSION = 1
LAUNCH_PLANNED = "planned"
LAUNCH_LAUNCHED = "launched"
LAUNCH_COMPLETED = "completed"
LAUNCH_TERMINAL = "terminal"

ERROR_LAUNCH = "The simulation could not be started. Check the settings and try again."
ERROR_WORKER = "The worker process could not be started."
ERROR_PARITY = (
    "The frozen settings did not match the request. Return to Configure options "
    "and confirm the simulation settings."
)
ERROR_ACTIVE = "A simulation is already running."
ERROR_STAGING = "The simulation files could not be prepared. Try again."
ERROR_UPLOADS = "Exactly three Fluvius CSV files are required."

PopenFactory = Callable[..., Any]


def new_job_id(*, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"btm-{stamp}-{uuid.uuid4().hex[:8]}"


def job_record_is_present(state: Mapping[str, Any]) -> bool:
    job = state.get("job")
    return isinstance(job, Mapping) and bool(job.get("job_id"))


def job_is_in_flight(job: Mapping[str, Any] | None) -> bool:
    if not isinstance(job, Mapping) or not job.get("job_id"):
        return False
    return str(job.get("launch_state") or "") != LAUNCH_COMPLETED


def job_blocks_new_launch(state: Mapping[str, Any]) -> bool:
    return job_is_in_flight(state.get("job") if isinstance(state.get("job"), Mapping) else None)


def job_locks_navigation(state: Mapping[str, Any]) -> bool:
    if results_are_valid(state.get("results") if isinstance(state.get("results"), Mapping) else None):
        return False
    job = state.get("job") if isinstance(state.get("job"), Mapping) else None
    if not isinstance(job, Mapping):
        return False
    if job.get("lock_navigation") is True:
        return True
    return job_is_in_flight(job)


def worker_command(kind: str, request_path: str | Path) -> list[str]:
    module = "btm_sim.sweep" if kind == KIND_SWEEP else "btm_sim.run"
    return [sys.executable, "-u", "-m", module, "--request", str(request_path)]


def worker_env(*, root: Path | None = None) -> dict[str, str]:
    del root
    env = os.environ.copy()
    src = str(src_dir())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _windows_creationflags() -> int:
    no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    new_group = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    return no_window | new_group


def write_staged_uploads(
    payloads: Sequence[tuple[str, bytes]],
    staging_dir: Path,
) -> tuple[Path, Path, Path]:
    if len(payloads) != 3:
        raise ValueError("uploads")
    staging_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for (_original, data), filename in zip(payloads, STAGING_FILENAMES, strict=True):
        dest = staging_dir / filename
        dest.write_bytes(data)
        paths.append(dest)
    return paths[0], paths[1], paths[2]


def _plain_job(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(record)
    json.dumps(payload)
    return payload


def _period_label(state: Mapping[str, Any]) -> str:
    selected = selected_period_record(state)
    if isinstance(selected, Mapping) and selected.get("label"):
        return str(selected["label"])
    return str(state.get("period_id") or "")


def _intent_and_fingerprint(state: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    intent = review.get("intent") if isinstance(review.get("intent"), Mapping) else None
    fingerprint = str(review.get("fingerprint") or "") or None
    return (dict(intent) if intent is not None else None), fingerprint


def _kind_from_intent(intent: Mapping[str, Any]) -> str:
    if str(intent.get("analysis_mode") or "") == MODE_SIZE:
        return KIND_SWEEP
    return KIND_COMPARISON


def _abort(
    state: dict[str, Any],
    *,
    staging_dir: Path | None,
    staging_root: Path,
    error: str,
) -> dict[str, Any]:
    state.pop("job", None)
    if staging_dir is not None:
        cleanup_staging_dir(staging_dir, staging_root=staging_root)
    return {"ok": False, "error": error}


def _launch_process(
    *,
    kind: str,
    request_path: Path,
    console_path: Path,
    cwd: Path,
    popen: PopenFactory,
) -> int:
    console_path.parent.mkdir(parents=True, exist_ok=True)
    handle = console_path.open("ab")
    try:
        kwargs: dict[str, Any] = {
            "args": worker_command(kind, request_path),
            "stdout": handle,
            "stderr": subprocess.STDOUT,
            "cwd": str(cwd),
            "env": worker_env(root=cwd),
            "shell": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = _windows_creationflags()
        else:
            kwargs["start_new_session"] = True
        process = popen(**kwargs)
    finally:
        handle.close()
    pid = getattr(process, "pid", None)
    if pid is None:
        raise OSError("pid")
    return int(pid)


def _adopt_worker_pid(job: dict[str, Any], status: Mapping[str, Any] | None) -> None:
    if not isinstance(status, Mapping):
        return
    raw = status.get("worker_pid")
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    job["pid"] = pid
    if str(job.get("launch_state") or "") == LAUNCH_PLANNED:
        job["launch_state"] = LAUNCH_LAUNCHED


def _reconnect_existing_job(state: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Reconnect to a stored job without calling Popen."""
    raw = read_status(job.get("output_dir"))
    trusted = trusted_status(job, raw)
    _adopt_worker_pid(job, trusted)
    job["lock_navigation"] = True
    state["job"] = _plain_job(job)
    state.pop("launch_error", None)
    return {"ok": True, "job": state["job"], "reconnect": True}


def launch_live_job(
    state: dict[str, Any],
    *,
    outputs_root: Path | None = None,
    staging_root: Path | None = None,
    cwd: Path | None = None,
    popen: PopenFactory = subprocess.Popen,
    now: datetime | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    if os.environ.get("PYTEST_CURRENT_TEST") and outputs_root is None:
        raise RuntimeError("tests must pass a temporary outputs_root")
    if os.environ.get("PYTEST_CURRENT_TEST") and popen is subprocess.Popen:
        raise RuntimeError("tests must inject a fake worker")

    from ui.services.review import snapshot_block_reason, snapshot_fingerprint, stored_snapshot

    reason = snapshot_block_reason(state)
    if reason is not None:
        return {"ok": False, "error": reason}

    intent, fingerprint = _intent_and_fingerprint(state)
    snapshot = stored_snapshot(state)
    if intent is None or fingerprint is None or snapshot is None:
        return {"ok": False, "error": ERROR_LAUNCH}
    if snapshot_fingerprint(snapshot) != fingerprint:
        return {"ok": False, "error": ERROR_LAUNCH}

    existing = state.get("job") if isinstance(state.get("job"), dict) else None
    if existing is not None and job_is_in_flight(existing):
        if str(existing.get("fingerprint") or "") != fingerprint:
            return {"ok": False, "error": ERROR_ACTIVE}
        return _reconnect_existing_job(state, dict(existing))

    payloads = tuple(state.get("upload_payloads") or ())
    if len(payloads) != 3:
        return {"ok": False, "error": ERROR_UPLOADS}

    kind = _kind_from_intent(intent)
    out_root = Path(outputs_root) if outputs_root is not None else default_outputs_root()
    stage_root = Path(staging_root) if staging_root is not None else default_staging_root(out_root)
    workdir = Path(cwd) if cwd is not None else project_root()
    created_id = job_id or new_job_id(now=now)
    site = str(intent.get("site_label") or "")
    period_id = str(intent.get("period_id") or "")
    staging_dir: Path | None = None
    try:
        staging_dir = staging_dir_for(created_id, staging_root=stage_root)
        output_dir = output_dir_for(
            created_id,
            site=site,
            period_id=period_id,
            kind=kind,
            outputs_root=out_root,
        )
    except ValueError:
        return {"ok": False, "error": ERROR_LAUNCH}

    try:
        fluvius_paths = write_staged_uploads(payloads, staging_dir)
        kwargs = builder_kwargs_from_intent(intent)
        if kind == KIND_SWEEP:
            request = build_sweep_request(
                fluvius_paths=fluvius_paths,
                output_dir=output_dir,
                job_id=created_id,
                defaults_path=standard_defaults_path(),
                cwd=workdir,
                **kwargs,
            )
            payload = serialize_sweep_request(request)
        else:
            request = build_run_request(
                fluvius_paths=fluvius_paths,
                output_dir=output_dir,
                job_id=created_id,
                defaults_path=standard_defaults_path(),
                dynamic_injection_prices=standard_day_ahead_prices_path(),
                cwd=workdir,
                **kwargs,
            )
            payload = serialize_run_request(request)
        mismatches = mismatches_for_serialized_request(payload, intent)
        if mismatches:
            return _abort(state, staging_dir=staging_dir, staging_root=stage_root, error=ERROR_PARITY)
        request_path = staging_dir / request_filename(kind)
        if kind == KIND_SWEEP:
            write_sweep_request(request, request_path)
        else:
            write_run_request(request, request_path)
        console_path = staging_dir / WORKER_CONSOLE_FILENAME
        planned = {
            "version": JOB_RECORD_VERSION,
            "job_id": created_id,
            "kind": kind,
            "output_dir": str(output_dir),
            "request_path": str(request_path),
            "staging_dir": str(staging_dir),
            "worker_console_path": str(console_path),
            "pid": None,
            "launch_state": LAUNCH_PLANNED,
            "launch_utc": iso_utc(now),
            "site": site,
            "period_id": period_id,
            "period_label": _period_label(state),
            "fingerprint": fingerprint,
            "data_route": str(state.get("data_route") or "live"),
            "lock_navigation": True,
        }
        state["job"] = _plain_job(planned)
    except Exception:
        return _abort(state, staging_dir=staging_dir, staging_root=stage_root, error=ERROR_LAUNCH)
    try:
        pid = _launch_process(
            kind=kind,
            request_path=request_path,
            console_path=console_path,
            cwd=workdir,
            popen=popen,
        )
    except Exception:
        return _abort(state, staging_dir=staging_dir, staging_root=stage_root, error=ERROR_WORKER)
    state["job"]["pid"] = pid
    state["job"]["launch_state"] = LAUNCH_LAUNCHED
    state["job"] = _plain_job(state["job"])
    state.pop("launch_error", None)
    return {"ok": True, "job": state["job"]}


def mark_job_terminal(state: dict[str, Any], *, launch_state: str = LAUNCH_TERMINAL) -> dict[str, Any]:
    job = state.get("job") if isinstance(state.get("job"), dict) else None
    if job is None:
        return dict(state)
    job["launch_state"] = launch_state
    job["lock_navigation"] = launch_state != LAUNCH_COMPLETED
    state["job"] = _plain_job(job)
    return dict(state)


def store_live_results(state: dict[str, Any], *, staging_root: Path | None = None) -> list[str]:
    job = state.get("job") if isinstance(state.get("job"), dict) else None
    if job is None:
        return ["job"]
    intent, _fingerprint = _intent_and_fingerprint(state)
    issues = validate_result_folder(
        str(job.get("output_dir") or ""),
        kind=str(job.get("kind") or KIND_COMPARISON),
        source=SOURCE_LIVE,
        job=job,
        intent=intent,
        expected_dir=str(job.get("output_dir") or ""),
    )
    if issues:
        job["validation_issues"] = list(issues)
        mark_job_terminal(state, launch_state=LAUNCH_TERMINAL)
        return issues
    state["results"] = result_record(
        kind=str(job.get("kind") or KIND_COMPARISON),
        folder=str(job["output_dir"]),
        source=SOURCE_LIVE,
        job_id=str(job.get("job_id") or ""),
        site=str(job.get("site") or ""),
        period_id=str(job.get("period_id") or ""),
        period_label=str(job.get("period_label") or ""),
    )
    stage_root = staging_root or default_staging_root()
    cleanup_staging_dir(job.get("staging_dir"), staging_root=stage_root)
    job["staging_dir"] = job.get("staging_dir")
    mark_job_terminal(state, launch_state=LAUNCH_COMPLETED)
    return []


def reconcile_execution(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    pid_alive: Callable[[int | None], bool] | None = None,
    staging_root: Path | None = None,
) -> str:
    if results_are_valid(state.get("results") if isinstance(state.get("results"), Mapping) else None):
        return CLASS_READY
    job = state.get("job") if isinstance(state.get("job"), dict) else None
    if job is None:
        return CLASS_INCOMPLETE
    raw = read_status(job.get("output_dir"))
    trusted = trusted_status(job, raw)
    klass = classify_job(
        job,
        now=now,
        pid_alive=pid_alive or pid_is_alive,
        status=raw,
        has_results=False,
    )
    if klass == CLASS_VALIDATING:
        issues = store_live_results(state, staging_root=staging_root)
        if not issues:
            return CLASS_READY
        return CLASS_INCOMPLETE
    if klass in {CLASS_FAILED, CLASS_UNEXPECTED, CLASS_INCOMPLETE}:
        mark_job_terminal(state, launch_state=LAUNCH_TERMINAL)
    if klass in {CLASS_QUEUED, CLASS_RUNNING}:
        job["lock_navigation"] = True
        state["job"] = _plain_job(job)
    if trusted is not None:
        _adopt_worker_pid(job, trusted)
        job["last_status_state"] = str(trusted.get("state") or "")
        state["job"] = _plain_job(job)
    return klass


def return_to_review(state: dict[str, Any], *, staging_root: Path | None = None) -> dict[str, Any]:
    job = state.get("job") if isinstance(state.get("job"), dict) else None
    stage_root = Path(staging_root) if staging_root is not None else default_staging_root()
    if job is not None:
        cleanup_staging_dir(job.get("staging_dir"), staging_root=stage_root)
    state.pop("job", None)
    state.pop("results", None)
    state.pop("launch_error", None)
    state["step"] = 5
    if int(state.get("max_step") or 1) > 5:
        state["max_step"] = 5
    return dict(state)
