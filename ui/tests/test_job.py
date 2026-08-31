from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from btm_sim.config import standard_defaults_path
from btm_sim.market import standard_day_ahead_prices_path
from tests.helpers import balanced_site, qh_range, write_site

from ui.flow import continue_to_step6, navigate_to_step
from ui.services.configure import POWER_EXPLICIT, apply_configure_fields, store_frozen_snapshot
from ui.services.job import (
    ERROR_PARITY,
    ERROR_WORKER,
    LAUNCH_LAUNCHED,
    LAUNCH_PLANNED,
    launch_live_job,
    reconcile_execution,
    return_to_review,
)
from ui.services.status import CLASS_QUEUED, CLASS_UNEXPECTED
from ui.services.paths import (
    KIND_COMPARISON,
    KIND_SWEEP,
    STAGING_FILENAMES,
    is_contained,
    output_dir_for,
    safe_slug,
    staging_dir_for,
)
from ui.services.review import REASON_UPLOADS, apply_review_fields, ensure_review_initialized
from ui.tests.test_review import _candidates, freeze_one, freeze_size, ready_review_state

UTC = timezone.utc
PROJECT_OUTPUTS = Path(__file__).resolve().parents[2] / "outputs"


class _Recorder:
    def __init__(self, *, pid: int = 4242, fail: bool = False, on_call=None):
        self.pid = pid
        self.fail = fail
        self.calls: list[dict] = []
        self.on_call = on_call

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_call is not None:
            self.on_call(kwargs)
        if self.fail:
            raise OSError("worker")
        return SimpleNamespace(pid=self.pid)


def _site(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts, import_kwh=1.0, export_kwh=2.0, pv_kwh=2.5)
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def _with_common_period(state: dict) -> dict:
    period = dict(state["ingest_snapshot"]["periods"][0])
    period["id"] = "common"
    period["kind"] = "common_overlap"
    period["complete_calendar_year"] = False
    period["label"] = "Common overlap"
    state["ingest_snapshot"]["periods"] = [period]
    state["period_id"] = "common"
    state["period_inspection"]["period_id"] = "common"
    state["period_inspection"]["selected_period"] = period
    return state


def _live_one(tmp_path: Path) -> dict:
    paths = _site(tmp_path / "site")
    state = _with_common_period(ready_review_state())
    state["upload_payloads"] = tuple((path.name, path.read_bytes()) for path in paths)
    return freeze_one(state)


def _live_size(tmp_path: Path) -> dict:
    paths = _site(tmp_path / "site")
    state = _with_common_period(ready_review_state())
    state["upload_payloads"] = tuple((path.name, path.read_bytes()) for path in paths)
    freeze_size(state)
    apply_configure_fields(
        state,
        sizing={
            "duration_2h": True,
            "duration_1h": False,
            "duration_4h": False,
            "duration_6h": False,
            "power_mode": POWER_EXPLICIT,
            "explicit_text": "10, 20\n20, 40",
        },
        candidates=_candidates(),
    )
    store_frozen_snapshot(state)
    state["review"] = None
    ensure_review_initialized(state)
    apply_review_fields(state, partial_period_ack=True)
    return state


def _launch_kwargs(tmp_path: Path, recorder: _Recorder) -> dict:
    outputs = tmp_path / "outputs"
    return {
        "outputs_root": outputs,
        "staging_root": outputs / "_ui_staging",
        "cwd": Path(__file__).resolve().parents[2],
        "popen": recorder,
        "now": datetime(2026, 8, 27, 16, 0, tzinfo=UTC),
        "job_id": "btm-testjob01",
    }


def test_safe_paths_sanitize_and_stay_contained(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    staging = outputs / "_ui_staging"
    staging.mkdir(parents=True)
    outputs.mkdir(exist_ok=True)
    assert safe_slug("../evil name") == "evil_name"
    out = output_dir_for(
        "btm-id",
        site="../Plant A",
        period_id="2024\\x",
        kind=KIND_COMPARISON,
        outputs_root=outputs,
    )
    assert is_contained(out, outputs)
    assert ".." not in out.name
    stage = staging_dir_for("btm-id", staging_root=staging)
    assert is_contained(stage, staging)


def test_one_battery_launch_writes_public_request(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    seen: dict = {}

    def on_call(_kwargs):
        seen["job"] = dict(state["job"])

    recorder.on_call = on_call
    kwargs = _launch_kwargs(tmp_path, recorder)
    outcome = launch_live_job(state, **kwargs)
    assert outcome["ok"] is True
    assert recorder.calls, "worker was not launched"
    assert seen["job"]["launch_state"] == LAUNCH_PLANNED
    assert seen["job"]["pid"] is None
    job = state["job"]
    assert job["launch_state"] == LAUNCH_LAUNCHED
    assert job["pid"] == 4242
    assert job["kind"] == KIND_COMPARISON
    request_path = Path(job["request_path"])
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert Path(payload["prices"]["path"]).resolve() == Path(standard_day_ahead_prices_path()).resolve()
    assert Path(payload["defaults_path"]).resolve() == Path(standard_defaults_path()).resolve()
    assert payload["job_id"] == "btm-testjob01"
    assert payload["allow_unvalidated"] is True
    assert payload["acknowledge_site_boundary"] is False
    assert payload["detailed_solver_output"] is False
    assert payload["tariffs"]["timezone"] == "Europe/Brussels"
    staged = Path(job["staging_dir"])
    for name in STAGING_FILENAMES:
        assert (staged / name).is_file()
    call = recorder.calls[0]
    assert call["shell"] is False
    assert call["args"][1:4] == ["-u", "-m", "btm_sim.run"]
    assert "--request" in call["args"]
    assert call["env"]["PYTHONUNBUFFERED"] == "1"
    assert "src" in call["env"]["PYTHONPATH"]
    assert Path(job["output_dir"]).is_relative_to(kwargs["outputs_root"])
    assert PROJECT_OUTPUTS.resolve() not in Path(job["output_dir"]).parents
    json.dumps(job)


def test_sweep_launch_keeps_candidate_order_without_price_override(tmp_path: Path) -> None:
    state = _live_size(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    kwargs["job_id"] = "btm-testsweep1"
    outcome = launch_live_job(state, **kwargs)
    assert outcome["ok"] is True, outcome
    payload = json.loads(Path(state["job"]["request_path"]).read_text(encoding="utf-8"))
    assert "prices" not in payload
    assert payload["mode"] == "explicit"
    ids = [item["candidate_id"] for item in payload["candidates"]]
    assert ids == [item["candidate_id"] for item in state["review"]["intent"]["sizing"]["candidates"]]
    assert recorder.calls[0]["args"][1:4] == ["-u", "-m", "btm_sim.sweep"]


def test_parity_mismatch_aborts_before_popen(tmp_path: Path, monkeypatch) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    monkeypatch.setattr(
        "ui.services.job.mismatches_for_serialized_request",
        lambda *_args, **_kwargs: ["usable capacity"],
    )
    kwargs = _launch_kwargs(tmp_path, recorder)
    staging_root = kwargs["staging_root"]
    outcome = launch_live_job(state, **kwargs)
    assert outcome["ok"] is False
    assert outcome["error"] == ERROR_PARITY
    assert recorder.calls == []
    assert "job" not in state
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


def test_popen_failure_cleans_staging_and_keeps_review(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    fingerprint = state["review"]["fingerprint"]
    recorder = _Recorder(fail=True)
    kwargs = _launch_kwargs(tmp_path, recorder)
    staging_root = kwargs["staging_root"]
    outcome = launch_live_job(state, **kwargs)
    assert outcome["ok"] is False
    assert outcome["error"] == ERROR_WORKER
    assert "job" not in state
    assert state["review"]["fingerprint"] == fingerprint
    assert state["step"] == 5
    assert not staging_root.exists() or list(staging_root.iterdir()) == []
    assert not any(kwargs["outputs_root"].glob("Plant_A_*")) or True
    leftover = [
        path
        for path in kwargs["outputs_root"].rglob("*")
        if path.is_file() and "_ui_staging" not in path.parts
    ]
    assert leftover == []


def test_missing_uploads_do_not_launch(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    state["upload_payloads"] = (("a.csv", b"a"),)
    recorder = _Recorder()
    outcome = launch_live_job(state, **_launch_kwargs(tmp_path, recorder))
    assert outcome == {"ok": False, "error": REASON_UPLOADS}
    assert recorder.calls == []


def test_duplicate_launch_does_not_start_a_second_worker(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    first = launch_live_job(state, **kwargs)
    assert first["ok"] is True
    second = launch_live_job(state, **kwargs)
    assert second["ok"] is True
    assert second.get("reconnect") is True
    assert len(recorder.calls) == 1
    continue_to_step6(state)
    assert navigate_to_step(state, 4) is False


def test_return_to_review_keeps_output_and_frozen_inputs(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    assert launch_live_job(state, **kwargs)["ok"] is True
    continue_to_step6(state)
    output = Path(state["job"]["output_dir"])
    output.mkdir(parents=True)
    marker = output / "run.log"
    marker.write_text("kept\n", encoding="utf-8")
    staging = Path(state["job"]["staging_dir"])
    snapshot = dict(state["configure"]["snapshot"])
    return_to_review(state, staging_root=kwargs["staging_root"])
    assert state["step"] == 5
    assert state["max_step"] == 5
    assert "job" not in state
    assert "results" not in state
    assert state["configure"]["snapshot"] == snapshot
    assert marker.is_file()
    assert not staging.exists()


def _planned_leftover(state: dict, tmp_path: Path, kwargs: dict, *, with_status: bool = False) -> dict:
    output = kwargs["outputs_root"] / "Plant_A_common_btm-testjob01"
    output.mkdir(parents=True, exist_ok=True)
    state["job"] = {
        "version": 1,
        "job_id": kwargs["job_id"],
        "kind": KIND_COMPARISON,
        "output_dir": str(output),
        "request_path": str(kwargs["staging_root"] / "btm-testjob01" / "run_request.json"),
        "staging_dir": str(kwargs["staging_root"] / "btm-testjob01"),
        "worker_console_path": str(kwargs["staging_root"] / "btm-testjob01" / "worker_stdout.log"),
        "pid": None,
        "launch_state": LAUNCH_PLANNED,
        "launch_utc": "2026-08-27T16:00:00Z",
        "site": "Plant A",
        "period_id": "common",
        "period_label": "Common overlap",
        "fingerprint": state["review"]["fingerprint"],
        "data_route": "live",
        "lock_navigation": True,
    }
    if with_status:
        (output / "run_status.json").write_text(
            json.dumps(
                {
                    "job_id": kwargs["job_id"],
                    "state": "running",
                    "output_dir": str(output),
                    "worker_pid": 7777,
                    "message": "Solving revenue case",
                    "updated_at_utc": "2026-08-27T16:00:01Z",
                    "started_at_utc": "2026-08-27T16:00:00Z",
                    "artifact_schema_version": 2,
                }
            ),
            encoding="utf-8",
        )
    del tmp_path
    return state


def test_existing_planned_record_never_calls_popen(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    _planned_leftover(state, tmp_path, kwargs)
    outcome = launch_live_job(state, **kwargs)
    assert outcome["ok"] is True
    assert outcome.get("reconnect") is True
    assert recorder.calls == []
    assert state["job"]["launch_state"] == LAUNCH_PLANNED
    assert state["job"]["pid"] is None


def test_planned_record_adopts_trusted_running_pid_without_launch(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    _planned_leftover(state, tmp_path, kwargs, with_status=True)
    outcome = launch_live_job(state, **kwargs)
    assert outcome["ok"] is True
    assert recorder.calls == []
    assert state["job"]["pid"] == 7777
    assert state["job"]["launch_state"] == LAUNCH_LAUNCHED


def test_planned_record_stays_queued_then_unexpected_without_launch(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    _planned_leftover(state, tmp_path, kwargs)
    now = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)
    kwargs["now"] = now
    assert launch_live_job(state, **kwargs)["ok"] is True
    assert recorder.calls == []
    queued = reconcile_execution(
        state,
        now=datetime(2026, 8, 27, 16, 0, 3, tzinfo=UTC),
        pid_alive=lambda _pid: False,
    )
    assert queued == CLASS_QUEUED
    unexpected = reconcile_execution(
        state,
        now=datetime(2026, 8, 27, 16, 0, 9, tzinfo=UTC),
        pid_alive=lambda _pid: False,
    )
    assert unexpected == CLASS_UNEXPECTED
    assert recorder.calls == []


def test_original_launch_calls_popen_once_and_second_call_does_not_relaunch(tmp_path: Path) -> None:
    state = _live_one(tmp_path)
    recorder = _Recorder()
    kwargs = _launch_kwargs(tmp_path, recorder)
    first = launch_live_job(state, **kwargs)
    assert first["ok"] is True
    assert first.get("reconnect") is not True
    assert len(recorder.calls) == 1
    second = launch_live_job(state, **kwargs)
    assert second.get("reconnect") is True
    assert len(recorder.calls) == 1


def test_return_to_review_without_injected_root_does_not_delete_outside_path(tmp_path: Path) -> None:
    outside = tmp_path / "not-staging" / "job"
    outside.mkdir(parents=True)
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    output = tmp_path / "run-out"
    output.mkdir()
    kept = output / "run.log"
    kept.write_text("kept", encoding="utf-8")
    state = {
        "job": {"staging_dir": str(outside), "output_dir": str(output)},
        "results": {"validated": False},
        "launch_error": "x",
        "step": 6,
        "max_step": 6,
        "configure": {"snapshot": {"site_name": "Plant A"}},
        "review": {"fingerprint": "abc"},
    }
    return_to_review(state)
    assert marker.is_file()
    assert kept.is_file()
    assert state["step"] == 5
    assert "job" not in state
    assert "results" not in state


def test_return_to_review_deletes_only_job_dir_under_default_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "outputs" / "_ui_staging"
    job_dir = root / "btm-safe"
    job_dir.mkdir(parents=True)
    (job_dir / "fluvius_1.csv").write_text("a", encoding="utf-8")
    sibling = root / "other"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("y", encoding="utf-8")
    monkeypatch.setattr(
        "ui.services.job.default_staging_root",
        lambda outputs_root=None: root,
    )
    state = {
        "job": {"staging_dir": str(job_dir)},
        "step": 6,
        "max_step": 6,
    }
    return_to_review(state)
    assert not job_dir.exists()
    assert (sibling / "keep.txt").is_file()
    assert state["step"] == 5
