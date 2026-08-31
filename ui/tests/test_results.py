from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from btm_sim.config import standard_defaults_path
from btm_sim.market import standard_day_ahead_prices_path
from btm_sim.run import build_run_request, write_run_request
from btm_sim.sweep import build_sweep_request, write_sweep_request
from tests.helpers import balanced_site, qh_range, write_site

from ui.services.job import store_live_results
from ui.services.paths import KIND_COMPARISON, KIND_SWEEP
from ui.services.request_intent import builder_kwargs_from_intent
from ui.services.results import (
    COMPARISON_FILES_LIVE,
    SCENARIO_ORDER,
    SOURCE_DEMO,
    SOURCE_LIVE,
    SWEEP_FILES_LIVE,
    cleanup_staging_dir,
    open_demo_results,
    required_files,
    validate_result_folder,
)
from ui.tests.test_job import _live_one, _live_size
from ui.tests.test_review import freeze_one, freeze_size, ready_review_state

UTC = timezone.utc


def _touch(folder: Path, names: tuple[str, ...]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = folder / name
        if path.suffix in {".json", ".jsonl"}:
            if not path.exists():
                path.write_text("{}\n" if path.suffix == ".json" else "", encoding="utf-8")
        else:
            path.write_bytes(b"x")


def _site(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts, import_kwh=1.0, export_kwh=2.0, pv_kwh=2.5)
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def _write_status(folder: Path, job_id: str, **fields) -> None:
    payload = {
        "job_id": job_id,
        "state": "completed",
        "output_dir": str(folder),
        "artifact_schema_version": 2,
        "message": "Comparison completed",
    }
    payload.update(fields)
    (folder / "run_status.json").write_text(json.dumps(payload), encoding="utf-8")


def _comparison_summary(folder: Path) -> None:
    (folder / "comparison_summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "artifact_schema_version": 2,
                "scenario_order": list(SCENARIO_ORDER),
            }
        ),
        encoding="utf-8",
    )


def _sweep_summary(folder: Path, candidates: list[dict]) -> None:
    (folder / "sweep_summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "sweep_artifact_schema_version": 1,
                "n_candidates": len(candidates),
                "candidates": candidates,
                "recommendation": {"recommendation_kind": "no_battery"},
            }
        ),
        encoding="utf-8",
    )


def _valid_comparison(tmp_path: Path) -> tuple[dict, Path]:
    state = _live_one(tmp_path)
    intent = state["review"]["intent"]
    folder = tmp_path / "compare_out"
    paths = _site(tmp_path / "fluvius")
    request = build_run_request(
        fluvius_paths=paths,
        output_dir=folder,
        job_id="v2-valid-compare",
        defaults_path=standard_defaults_path(),
        dynamic_injection_prices=standard_day_ahead_prices_path(),
        **builder_kwargs_from_intent(intent),
    )
    write_run_request(request, folder / "run_request.json")
    _touch(folder, COMPARISON_FILES_LIVE)
    _write_status(folder, "v2-valid-compare")
    _comparison_summary(folder)
    job = {
        "job_id": "v2-valid-compare",
        "kind": KIND_COMPARISON,
        "output_dir": str(folder),
        "site": intent["site_label"],
        "period_id": intent["period_id"],
        "period_label": "Common overlap",
        "staging_dir": str(tmp_path / "outputs" / "_ui_staging" / "v2-valid-compare"),
        "launch_state": "launched",
        "lock_navigation": True,
    }
    Path(job["staging_dir"]).mkdir(parents=True)
    (Path(job["staging_dir"]) / "fluvius_1.csv").write_bytes(b"a")
    state["job"] = job
    return state, folder


def _valid_sweep(tmp_path: Path) -> tuple[dict, Path]:
    state = _live_size(tmp_path)
    intent = state["review"]["intent"]
    folder = tmp_path / "sweep_out"
    paths = _site(tmp_path / "fluvius")
    request = build_sweep_request(
        fluvius_paths=paths,
        output_dir=folder,
        job_id="v2-valid-sweep",
        defaults_path=standard_defaults_path(),
        **builder_kwargs_from_intent(intent),
    )
    write_sweep_request(request, folder / "sweep_request.json")
    payload = json.loads((folder / "sweep_request.json").read_text(encoding="utf-8"))
    _touch(folder, SWEEP_FILES_LIVE)
    _write_status(folder, "v2-valid-sweep", artifact_schema_version=1)
    _sweep_summary(folder, payload["candidates"])
    job = {
        "job_id": "v2-valid-sweep",
        "kind": KIND_SWEEP,
        "output_dir": str(folder),
        "site": intent["site_label"],
        "period_id": intent["period_id"],
        "period_label": "Common overlap",
        "staging_dir": str(tmp_path / "outputs" / "_ui_staging" / "v2-valid-sweep"),
        "launch_state": "launched",
        "lock_navigation": True,
    }
    Path(job["staging_dir"]).mkdir(parents=True)
    state["job"] = job
    return state, folder


def test_valid_comparison_and_sweep_folders_pass(tmp_path: Path) -> None:
    state, folder = _valid_comparison(tmp_path)
    issues = validate_result_folder(
        folder,
        kind=KIND_COMPARISON,
        source=SOURCE_LIVE,
        job=state["job"],
        intent=state["review"]["intent"],
        expected_dir=folder,
    )
    assert issues == []
    assert store_live_results(state, staging_root=tmp_path / "outputs" / "_ui_staging") == []
    assert state["results"]["validated"] is True
    assert state["results"]["source"] == SOURCE_LIVE
    assert not Path(state["job"]["staging_dir"]).exists()
    assert folder.is_dir()

    sweep_state, sweep_folder = _valid_sweep(tmp_path)
    sweep_issues = validate_result_folder(
        sweep_folder,
        kind=KIND_SWEEP,
        source=SOURCE_LIVE,
        job=sweep_state["job"],
        intent=sweep_state["review"]["intent"],
        expected_dir=sweep_folder,
    )
    assert sweep_issues == []


def test_missing_required_files_are_reported(tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    issues = validate_result_folder(folder, kind=KIND_COMPARISON, source=SOURCE_LIVE)
    for name in required_files(KIND_COMPARISON, source=SOURCE_LIVE):
        assert f"missing {name}" in issues
    sweep_issues = validate_result_folder(tmp_path / "missing-sweep", kind=KIND_SWEEP, source=SOURCE_LIVE)
    assert "result folder" in sweep_issues


def test_wrong_identity_schema_and_parity_fail(tmp_path: Path) -> None:
    state, folder = _valid_comparison(tmp_path)
    job = dict(state["job"])
    job["job_id"] = "other"
    issues = validate_result_folder(
        folder,
        kind=KIND_COMPARISON,
        source=SOURCE_LIVE,
        job=job,
        intent=state["review"]["intent"],
        expected_dir=folder,
    )
    assert "status job id" in issues or "request job id" in issues
    intent = dict(state["review"]["intent"])
    intent["one_battery"] = dict(intent["one_battery"])
    intent["one_battery"]["usable_kwh"] = 12.0
    parity = validate_result_folder(
        folder,
        kind=KIND_COMPARISON,
        source=SOURCE_LIVE,
        job=state["job"],
        intent=intent,
        expected_dir=folder,
    )
    assert "request does not match the frozen Review settings" in parity
    summary = json.loads((folder / "comparison_summary.json").read_text(encoding="utf-8"))
    summary["scenario_order"] = list(reversed(SCENARIO_ORDER))
    (folder / "comparison_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    order = validate_result_folder(
        folder,
        kind=KIND_COMPARISON,
        source=SOURCE_LIVE,
        job=state["job"],
        intent=state["review"]["intent"],
        expected_dir=folder,
    )
    assert "scenario order" in order


def test_incomplete_completed_run_does_not_store_results(tmp_path: Path) -> None:
    state, folder = _valid_comparison(tmp_path)
    (folder / "comparison_dispatch.csv").unlink()
    issues = store_live_results(state, staging_root=tmp_path / "outputs" / "_ui_staging")
    assert issues
    assert "results" not in state
    assert state["job"]["launch_state"] == "terminal"
    assert folder.exists()


def test_cleanup_cannot_leave_staging_root(tmp_path: Path) -> None:
    staging_root = tmp_path / "outputs" / "_ui_staging"
    staging_root.mkdir(parents=True)
    outside = tmp_path / "not-staging"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    assert cleanup_staging_dir(outside, staging_root=staging_root) is False
    assert marker.is_file()
    assert cleanup_staging_dir(staging_root, staging_root=staging_root) is False
    assert staging_root.is_dir()


def test_demo_open_does_not_launch_or_write(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def _boom(*_args, **_kwargs):
        calls.append("builder")
        raise AssertionError("demo must not build or launch")

    monkeypatch.setattr("btm_sim.run.build_run_request", _boom)
    monkeypatch.setattr("btm_sim.sweep.build_sweep_request", _boom)
    monkeypatch.setattr("btm_sim.run.write_run_request", _boom)
    monkeypatch.setattr("btm_sim.sweep.write_sweep_request", _boom)
    one = freeze_one(ready_review_state(demo=True))
    outcome = open_demo_results(one)
    assert outcome["ok"] is True, outcome
    assert one["results"]["source"] == SOURCE_DEMO
    assert one["results"]["demo"] is True
    assert "job" not in one
    assert calls == []
    size = freeze_size(ready_review_state(demo=True))
    sweep = open_demo_results(size)
    assert sweep["ok"] is True, sweep
    assert sweep_state_kind(size) == KIND_SWEEP


def sweep_state_kind(state: dict) -> str:
    return str(state["results"]["kind"])


def test_invalid_demo_stays_on_review(tmp_path: Path) -> None:
    state = freeze_one(ready_review_state(demo=True))
    outcome = open_demo_results(state, root=tmp_path)
    assert outcome["ok"] is False
    assert "results" not in state
    assert state.get("step") == 5
