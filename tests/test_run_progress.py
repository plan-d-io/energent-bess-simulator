"""Progress events, atomic status, and promptly readable job files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from btm_sim.progress import (
    STAGE_ORDER,
    STAGE_TOTAL,
    CallbackProgress,
    ProgressEvent,
    make_event,
)
from btm_sim.run.status import JobSession, atomic_write_json
from btm_sim.run.request import build_run_request
from btm_sim.run.workflow import run_end_to_end
from tests.helpers import balanced_site, qh_range, write_site

UTC = timezone.utc


def _site(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts, import_kwh=1.0, export_kwh=2.0, pv_kwh=2.5)
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def test_make_event_uses_stable_stage_keys():
    assert STAGE_TOTAL == 10
    assert STAGE_ORDER[0] == "read_fluvius"
    assert STAGE_ORDER[-1] == "verify_complete"
    event = make_event("optimize_peak_reduction", "started")
    assert event.stage_number == 6
    assert event.stage_total == 10
    assert event.message == "Optimising peak reduction"
    done = make_event("verify_complete", "completed")
    assert done.message == "Run completed"


def test_atomic_status_is_always_valid_json(tmp_path: Path):
    path = tmp_path / "run_status.json"
    atomic_write_json(path, {"state": "running", "n": 1})
    assert json.loads(path.read_text(encoding="utf-8"))["n"] == 1
    atomic_write_json(path, {"state": "completed", "n": 2})
    assert json.loads(path.read_text(encoding="utf-8"))["n"] == 2


def test_job_session_flushes_jsonl_and_log(tmp_path: Path):
    session = JobSession.create(tmp_path / "job", "btm-test")
    try:
        event = make_event("read_fluvius", "started")
        session.emit(event)
        events = (tmp_path / "job" / "run_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(events[-1])["stage_key"] == "read_fluvius"
        log = (tmp_path / "job" / "run.log").read_text(encoding="utf-8")
        assert "Reading and checking the three Fluvius files" in log
        status = json.loads((tmp_path / "job" / "run_status.json").read_text(encoding="utf-8"))
        assert status["state"] == "running"
        assert status["stage_key"] == "read_fluvius"
        assert status["worker_pid"]
    finally:
        session.close()


def test_successful_run_emits_ordered_stage_keys(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "run",
        cli={"seasonal_plots": False},
    )
    seen: list[ProgressEvent] = []
    result = run_end_to_end(request, progress=CallbackProgress(seen.append), console=False)
    assert result.ok
    started = [event.stage_key for event in seen if event.state == "started" and event.level == "info"]
    # Warnings may inject extra started events; keep the first occurrence of each stage.
    ordered = []
    for key in started:
        if key not in ordered:
            ordered.append(key)
    assert ordered == list(STAGE_ORDER)
    status = json.loads((result.directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["artifact_schema_version"] == 2
    assert (result.directory / "run_events.jsonl").exists()
    assert (result.directory / "run.log").exists()
    assert (result.directory / "run_request.json").exists()
    events = [
        json.loads(line)
        for line in (result.directory / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events[0]["stage_key"] == "read_fluvius"
    assert any(item["message"] == "Run completed" for item in events)
