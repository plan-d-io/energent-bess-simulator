"""Unified comparison runner: SoC 0, reconciliation, metadata, repeatability."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cli import build_parser as reference_parser
from btm_sim.compare.artifacts import metrics_from_prefixed_dispatch
from btm_sim.compare.exceptions import ComparisonError
from btm_sim.compare.metrics import MONTHLY_PEAKS_DESCRIPTION, SCENARIO_ORDER
from btm_sim.compare.runner import comparison_config, run_comparison
from btm_sim.config.schema import TariffConfig
from btm_sim.optimizer.cli import build_parser as self_consumption_parser
from btm_sim.optimizer.peak_cli import build_parser as peak_parser
from tests.lp_frames import qh_frame

UTC = timezone.utc


def _two_interval_frame(*, simultaneous: bool = False, unvalidated: bool = False) -> pd.DataFrame:
    if simultaneous:
        rows = [
            {"imp": 1.0, "exp": 1.0, "pv": 2.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    else:
        rows = [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    frame = qh_frame(rows)
    if unvalidated:
        frame.loc[0, "quality_flag"] = "unvalidated"
        frame.loc[0, "offtake_quality"] = "unvalidated"
    return frame


def _cfg(soc: float = 5.0) -> BatteryConfig:
    return BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=soc)


def test_comparison_rejects_nonzero_initial_soc_and_standalone_defaults_remain_zero():
    with pytest.raises(ComparisonError, match="0 kWh"):
        comparison_config(_cfg(5.0))
    assert comparison_config(_cfg(0.0)).soc_initial_kwh == 0.0
    assert self_consumption_parser().get_default("soc_initial") is None
    assert peak_parser().get_default("soc_initial") is None
    assert reference_parser().get_default("soc_initial") == 0.0


def test_runner_uses_same_input_and_zero_soc(tmp_path: Path):
    frame = _two_interval_frame()
    result = run_comparison(frame, _cfg(0.0), output_dir=tmp_path / "run")
    assert result.config.soc_initial_kwh == 0.0
    assert result.summary["initial_soc_kwh"] == 0.0
    assert list(result.summary["scenarios"]) == list(SCENARIO_ORDER)
    pv = {name: result.summary["scenarios"][name]["total_pv_production_kwh"] for name in SCENARIO_ORDER}
    assert len(set(pv.values())) == 1
    for name in SCENARIO_ORDER:
        assert result.summary["scenarios"][name]["soc_initial_kwh"] == pytest.approx(0.0)
    assert result.summary["scenarios"]["self_consumption"]["soc_final_kwh"] == pytest.approx(0.0)
    assert result.summary["scenarios"]["peak_reduction"]["soc_final_kwh"] == pytest.approx(0.0)
    assert result.summary["scenarios"]["revenue"]["soc_final_kwh"] == pytest.approx(0.0)


def test_runner_rejects_nonzero_initial_soc(tmp_path: Path):
    with pytest.raises(ComparisonError, match="0 kWh"):
        run_comparison(_two_interval_frame(), _cfg(5.0), output_dir=tmp_path / "run")


def test_dispatch_monthly_and_summary_reconcile(tmp_path: Path):
    frame = _two_interval_frame()
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    result = run_comparison(frame, cfg, output_dir=tmp_path / "run")
    dispatch = pd.read_csv(result.directory / "comparison_dispatch.csv")
    summary = json.loads((result.directory / "comparison_summary.json").read_text(encoding="utf-8"))
    csv_rows = pd.read_csv(result.directory / "comparison_summary.csv")
    monthly = pd.read_csv(result.directory / "monthly_peaks.csv", comment="#")
    for scenario in SCENARIO_ORDER:
        rebuilt = metrics_from_prefixed_dispatch(dispatch, cfg, scenario=scenario, tariffs=TariffConfig())
        expected = summary["scenarios"][scenario]
        assert rebuilt["total_pv_production_kwh"] == pytest.approx(expected["total_pv_production_kwh"])
        assert rebuilt["useful_pv_delivered_kwh"] == pytest.approx(expected["useful_pv_delivered_kwh"])
        assert rebuilt["additional_useful_pv_kwh"] == pytest.approx(expected["additional_useful_pv_kwh"])
        assert rebuilt["grid_import_kwh"] == pytest.approx(expected["grid_import_kwh"])
        assert rebuilt["annual_peak_kw"] == pytest.approx(expected["annual_peak_kw"])
        csv_row = csv_rows.loc[csv_rows["scenario"] == scenario].iloc[0]
        assert csv_row["additional_useful_pv_kwh"] == pytest.approx(expected["additional_useful_pv_kwh"])
        assert csv_row["annual_peak_reduction_kw"] == pytest.approx(expected["annual_peak_reduction_kw"])
        assert monthly[f"{scenario}_kw"].iloc[0] == pytest.approx(expected["annual_peak_kw"])
        assert "revenue" in expected
        rebuilt_rev = rebuilt["revenue"]
        assert rebuilt_rev["total_energent_pv_revenue_eur"] == pytest.approx(
            expected["revenue"]["total_energent_pv_revenue_eur"]
        )
    text = (result.directory / "monthly_peaks.csv").read_text(encoding="utf-8")
    assert MONTHLY_PEAKS_DESCRIPTION in text
    assert result.summary["monthly_peaks_description"] == MONTHLY_PEAKS_DESCRIPTION
    assert result.summary["artifact_schema_version"] == 2
    assert result.summary["selected_period"]["kind"] in {"full_calendar_year", "rolling_twelve_months", "partial_period"}
    assert (result.directory / "resolved_config.json").exists()
    assert (result.directory / "monthly_summary.csv").exists()
    assert (result.directory / "comparison_dispatch.parquet").exists()


def test_simultaneous_import_export_remain_separate(tmp_path: Path):
    frame = _two_interval_frame(simultaneous=True)
    result = run_comparison(frame, BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0), output_dir=tmp_path / "run")
    row = result.dispatch.iloc[0]
    assert row["grid_import_baseline_kwh"] == pytest.approx(1.0)
    assert row["grid_export_baseline_kwh"] == pytest.approx(1.0)
    assert row["grid_import_baseline_kwh"] > 0 and row["grid_export_baseline_kwh"] > 0


def test_run_metadata_contains_required_fields(tmp_path: Path):
    parquet = tmp_path / "normalized_input.parquet"
    frame = _two_interval_frame(unvalidated=True)
    frame.to_parquet(parquet, index=False)
    validation = {
        "ok": True,
        "warnings": [{"code": "UNVALIDATED_READINGS", "message": "used with acknowledgement"}],
        "unvalidated_policy": {"allow_unvalidated": True, "acknowledged": True},
        "site_boundary_policy": {"acknowledge_site_boundary": True},
    }
    (tmp_path / "validation_report.json").write_text(json.dumps(validation), encoding="utf-8")
    result = run_comparison(
        pd.read_parquet(parquet),
        BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0),
        output_dir=tmp_path / "run",
        source_path=parquet,
    )
    meta = json.loads((result.directory / "run_metadata.json").read_text(encoding="utf-8"))
    from btm_sim import __version__

    assert meta["software_version"] == __version__
    assert meta["input"]["sha256"]
    assert meta["input"]["original_path"]
    assert meta["selected_period"]["start_utc"]
    assert meta["selected_period"]["start_local"]
    assert meta["battery"]["e_usable_kwh"] == 10
    assert meta["battery"]["soc_initial_kwh"] == 0.0
    assert meta["data_quality"]["n_unvalidated"] == 1
    assert meta["validation_report"]["sha256"]
    assert meta["validation_report"]["unvalidated_policy"]["acknowledged"] is True
    assert meta["validation_report"]["warnings"][0]["code"] == "UNVALIDATED_READINGS"
    assert meta["solvers"]["self_consumption"]["status"] == "OPTIMAL"
    assert meta["solvers"]["peak_reduction"]["status"] == "OPTIMAL"
    assert meta["solvers"]["revenue"]["status"] == "OPTIMAL"
    assert "normalized_input.parquet" in meta["filenames"]
    assert "resolved_config.json" in meta["filenames"]
    assert "monthly_summary.csv" in meta["filenames"]
    assert "comparison_dispatch.parquet" in meta["filenames"]
    assert meta["artifact_schema_version"] == 2
    assert "tariffs" in meta


def test_identical_inputs_produce_identical_numbers(tmp_path: Path):
    frame = _two_interval_frame()
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0)
    first = run_comparison(
        frame,
        cfg,
        output_root=tmp_path / "root",
        clock=lambda: datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    )
    second = run_comparison(
        frame,
        cfg,
        output_root=tmp_path / "root",
        clock=lambda: datetime(2024, 6, 1, 13, 0, tzinfo=UTC),
    )
    assert first.directory.name == "btm_compare_20240601T120000Z"
    assert second.directory.name == "btm_compare_20240601T130000Z"
    assert first.summary["scenarios"] == second.summary["scenarios"]
    pd.testing.assert_frame_equal(
        first.dispatch.drop(columns=["timestamp_utc", "timestamp_local"]),
        second.dispatch.drop(columns=["timestamp_utc", "timestamp_local"]),
    )


def test_winter_week_plots_are_written_and_other_seasons_omitted(tmp_path: Path):
    start = datetime(2024, 1, 14, 23, 0, tzinfo=UTC)
    rows = []
    for index in range(7 * 96):
        if index % 8 == 0:
            rows.append({"imp": 0.0, "exp": 0.5, "pv": 0.5})
        else:
            rows.append({"imp": 0.4, "exp": 0.0, "pv": 0.0})
    frame = qh_frame(rows, start=start)
    result = run_comparison(
        frame,
        BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0),
        output_dir=tmp_path / "run",
    )
    included = result.summary["seasonal_plots"]["included"]
    assert [item["season"] for item in included] == ["winter"]
    assert included[0]["iso_week"] == 3
    winter_sc = result.directory / "plots" / "self_consumption_winter_week03.png"
    winter_pk = result.directory / "plots" / "peak_reduction_winter_week03.png"
    winter_rv = result.directory / "plots" / "revenue_winter_week03.png"
    assert winter_sc.exists() and winter_sc.stat().st_size > 1000
    assert winter_pk.exists() and winter_pk.stat().st_size > 1000
    assert winter_rv.exists() and winter_rv.stat().st_size > 1000
    assert result.summary["seasonal_plots"]["omitted_seasons"] == ["spring", "summer", "autumn"]
    assert not (result.directory / "plots" / "self_consumption_summer_week26.png").exists()
