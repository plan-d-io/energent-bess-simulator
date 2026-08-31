"""End-to-end sweep worker, artifacts, progress, and failure files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.progress import ProgressEvent
from btm_sim.sweep.cli import main
from btm_sim.sweep.request import build_sweep_request, write_sweep_request
from btm_sim.sweep.site import preflight_sweep_candidates
from btm_sim.sweep.workflow import run_sweep_end_to_end
from tests.helpers import qh_range, write_site

UTC = timezone.utc


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


def _site(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    return write_site(
        tmp_path,
        starts,
        import_kwh=[2.0, 0.0, 1.0, 0.0],
        export_kwh=[0.0, 3.0, 0.0, 1.0],
        pv_kwh=[0.0, 3.0, 0.0, 1.0],
    )


def test_preflight_does_not_write_a_sweep_folder_or_use_gurobi(tmp_path: Path, monkeypatch):
    offtake, injection, pv = _site(tmp_path)
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("preflight must not call Gurobi")

    monkeypatch.setattr("btm_sim.optimizer.revenue.optimize_revenue", boom)
    analysis = preflight_sweep_candidates(
        [offtake, injection, pv],
        "common",
        durations_hours=[2.0, 4.0],
    )
    assert analysis.automatic_candidates
    assert called["n"] == 0
    assert list(tmp_path.glob("**/sweep_summary.json")) == []
    assert list(tmp_path.glob("**/run_status.json")) == []


def test_end_to_end_writes_sweep_artifacts_and_progress(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "sweep"
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=out,
        mode="explicit",
        explicit_pairs=[(5.0, 10.0), (10.0, 20.0)],
        site_label="Sweep unit",
    )
    recorder = RecordingProgress()
    result = run_sweep_end_to_end(request, progress=recorder)
    assert result.ok
    names = {path.name for path in out.iterdir() if path.is_file()}
    assert {
        "sweep_request.json",
        "run_status.json",
        "run_events.jsonl",
        "run.log",
        "normalized_input.parquet",
        "validation_report.json",
        "sweep_summary.json",
        "sweep_summary.csv",
        "sweep_summary.parquet",
        "site_analysis.json",
        "sweep_metadata.json",
        "resolved_config.json",
        "source_defaults.toml",
    } <= names
    assert "run_request.json" not in names
    assert "dynamic_injection_prices.parquet" not in names
    assert "comparison_summary.json" not in names
    assert "revenue_dispatch.csv" not in names
    assert not any(name.endswith("_dispatch.csv") for name in names)
    assert offtake.name not in names
    summary = json.loads((out / "sweep_summary.json").read_text(encoding="utf-8"))
    assert summary["sweep_artifact_schema_version"] == 1
    assert summary["annualized_from_partial_period"] is True
    assert "This period is not a complete calendar year" in (summary["partial_period_warning"] or "")
    assert summary["n_candidates"] == 2
    screening = summary["screening_summary"]
    assert screening["candidate_count"] == 2
    assert screening["screening_period_years"] == 10.0
    assert "screening_outcome" in screening
    peak = summary["peak_summary"]
    assert peak["dispatch_strategy"] == "revenue_maximisation"
    assert peak["financial_value_modelled"] is False
    assert peak["average_monthly_peak_available"] is False
    assert peak["average_monthly_peak_n_complete_months"] == 0
    assert peak["largest_average_monthly_peak_reduction_candidate"] is None
    assert summary["optimizer"] == "revenue"
    assert "annual_peak_reduction_kw" in summary["candidates"][0]
    assert "average_monthly_peak_reduction_kw" in summary["candidates"][0]
    assert summary["candidates"][0]["average_monthly_peak_n_complete_months"] == 0
    assert summary["baseline"]["average_monthly_peak_n_complete_months"] == 0
    csv_header = (out / "sweep_summary.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "payback_within_evaluation_period" in csv_header
    assert "average_monthly_peak_reduction_kw" in csv_header
    assert "annual_peak_reduction_pct" in csv_header
    import pandas as pd

    parquet = pd.read_parquet(out / "sweep_summary.parquet")
    assert "payback_within_evaluation_period" in parquet.columns
    assert "average_monthly_peak_reduction_kw" in parquet.columns
    assert "baseline_annual_peak_kw" in parquet.columns
    metadata = json.loads((out / "sweep_metadata.json").read_text(encoding="utf-8"))
    assert metadata["screening_summary"]["screening_outcome"] == screening["screening_outcome"]
    assert metadata["peak_summary"]["dispatch_strategy"] == peak["dispatch_strategy"]
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["message"] == "Sweep completed"
    assert status["stage_total"] == 7
    started = [event for event in recorder.events if event.state == "started"]
    assert started[0].message == "Reading and checking the three Fluvius files"
    assert any("Testing candidate 1 of 2" in event.message for event in started)
    assert any(event.message == "Calculating sizing recommendations" for event in started)
    assert any(event.message == "Writing result files" for event in started)
    completed = [event for event in recorder.events if event.state == "completed"]
    assert completed[-1].message == "Sweep completed"
    candidate_events = [event for event in started if event.stage_key == "test_candidate"]
    assert candidate_events[0].details["candidate_index"] == 1
    assert candidate_events[0].details["candidate_count"] == 2


def test_cli_frozen_request_and_ordinary_path(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "cli"
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=out,
        mode="explicit",
        explicit_pairs=[(5.0, 10.0)],
    )
    frozen = write_sweep_request(request, tmp_path / "sweep_request.json")
    code = main(["--request", str(frozen)])
    assert code == 0
    assert (out / "sweep_summary.json").exists()

    out2 = tmp_path / "cli2"
    code2 = main(
        [str(offtake), str(injection), str(pv), "--period", "common", "--output-dir", str(out2),
         "--mode", "explicit", "--candidate", "5,10"]
    )
    assert code2 == 0
    assert (out2 / "sweep_summary.json").exists()


def test_candidate_failure_leaves_status_events_and_log(tmp_path: Path, monkeypatch):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "fail"
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=out,
        mode="explicit",
        explicit_pairs=[(5.0, 10.0), (10.0, 20.0)],
    )

    def boom(*args, **kwargs):
        raise OptimizerError("forced failure", status="FAILED")

    monkeypatch.setattr("btm_sim.sweep.runner.optimize_revenue", boom)
    with pytest.raises(Exception, match="failed"):
        run_sweep_end_to_end(request)
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_category"]
    events = (out / "run_events.jsonl").read_text(encoding="utf-8")
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "failed" in events.lower() or "FAILED" in log
    assert (out / "sweep_request.json").exists()
    assert not (out / "sweep_summary.json").exists()
