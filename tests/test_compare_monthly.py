"""Monthly summary identities, peak wording, and queryable dispatch Parquet."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    PARQUET_COMPRESSION,
    PARQUET_ROW_GROUP_SIZE,
    build_comparison_dispatch,
)
from btm_sim.compare.metrics import SCENARIO_ORDER, attach_baseline_dispatch, scenario_metrics
from btm_sim.compare.monthly import MONTHLY_SUMMARY_COLUMNS, build_monthly_summary, reconcile_monthly_summary
from btm_sim.compare.runner import run_comparison
from btm_sim.config.schema import TariffConfig
from tests.helpers import AUTUMN_STARTS
from tests.lp_frames import qh_frame
from tests.test_compare_months import complete_month_frame

UTC = timezone.utc


def test_monthly_peak_reductions_differ_when_annual_highest_interval_cut_is_fixed():
    jan = complete_month_frame(2024, 1, import_kwh=0.1, peak_import_kwh=2.5)
    feb = complete_month_frame(2024, 2, import_kwh=0.1, peak_import_kwh=2.0)
    mar = complete_month_frame(2024, 3, import_kwh=0.1, peak_import_kwh=3.0)
    canonical = pd.concat([jan, feb, mar], ignore_index=True)
    cfg = BatteryConfig(100, 50, 50, 1.0, 1.0, soc_initial_kwh=0.0)
    baseline = attach_baseline_dispatch(canonical, cfg)
    shaved = baseline.copy()
    jan_peak_i = int(np.argmax(jan["grid_import_baseline_kwh"].to_numpy()))
    mar_peak_i = len(jan) + len(feb) + int(np.argmax(mar["grid_import_baseline_kwh"].to_numpy()))
    shaved.loc[jan_peak_i, "grid_import_kwh"] = 1.5
    shaved.loc[jan_peak_i, "grid_import_kw"] = 6.0
    shaved.loc[jan_peak_i, "discharge_load_kwh"] = 1.0
    shaved.loc[mar_peak_i, "grid_import_kwh"] = 1.75
    shaved.loc[mar_peak_i, "grid_import_kw"] = 7.0
    shaved.loc[mar_peak_i, "discharge_load_kwh"] = 1.25
    before = scenario_metrics(baseline, cfg, scenario="no_battery")
    after = scenario_metrics(shaved, cfg, scenario="peak_reduction")
    assert before["annual_peak_kw"] == pytest.approx(12.0)
    assert after["annual_peak_kw"] == pytest.approx(8.0)
    assert after["annual_peak_reduction_kw"] == pytest.approx(4.0)
    assert after["monthly_peaks_kw"]["2024-01"] == pytest.approx(6.0)
    assert after["monthly_peaks_kw"]["2024-02"] == pytest.approx(8.0)
    assert after["monthly_peaks_kw"]["2024-03"] == pytest.approx(7.0)
    monthly_reductions = {
        month: before["monthly_peaks_kw"][month] - after["monthly_peaks_kw"][month]
        for month in ("2024-01", "2024-02", "2024-03")
    }
    assert monthly_reductions == pytest.approx({"2024-01": 4.0, "2024-02": 0.0, "2024-03": 5.0})
    assert len(set(monthly_reductions.values())) > 1
    assert monthly_reductions["2024-02"] != after["annual_peak_reduction_kw"]
    assert after["average_monthly_peak_n_complete_months"] == 3
    assert after["average_monthly_peak_kw"] == pytest.approx((6.0 + 8.0 + 7.0) / 3)
    assert after["average_monthly_peak_reduction_kw"] == pytest.approx((10.0 + 8.0 + 12.0) / 3 - (6.0 + 8.0 + 7.0) / 3)


def test_zero_baseline_peak_and_revenue_percentages_are_undefined():
    frame = qh_frame([{"imp": 0.0, "exp": 0.0, "pv": 0.0, "load": 0.0}])
    frame["site_load_kwh"] = 0.0
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    tariffs = TariffConfig()
    baseline = attach_baseline_dispatch(frame, cfg)
    metrics = scenario_metrics(baseline, cfg, scenario="no_battery", tariffs=tariffs)
    assert metrics["annual_peak_kw"] == pytest.approx(0.0)
    assert metrics["annual_peak_reduction_pct"] is None
    assert metrics["average_monthly_peak_kw"] is None
    assert metrics["average_monthly_peak_reduction_pct"] is None
    assert metrics["revenue"]["revenue_change_pct"] is None
    dispatch = build_comparison_dispatch(
        frame,
        {name: baseline.copy() for name in SCENARIO_ORDER},
        tariffs,
        da_prices=np.zeros(len(frame)),
    )
    rows = build_monthly_summary(dispatch, cfg, tariffs)
    assert rows
    for row in rows:
        assert row["monthly_peak_reduction_kw"] == pytest.approx(0.0)
        assert row["monthly_peak_reduction_pct"] is None
        assert row["revenue_change_pct"] is None
        assert row["useful_self_consumption_pct"] is None


def test_monthly_percentages_use_monthly_totals_not_mean_interval_ratios():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 6.0, "pv": 8.0},
            {"imp": 2.0, "exp": 0.0, "pv": 2.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    tariffs = TariffConfig()
    dispatched = attach_baseline_dispatch(frame, cfg)
    interval_pcts = [100.0 * (8.0 - 6.0) / 8.0, 100.0 * (2.0 - 0.0) / 2.0]
    assert interval_pcts == pytest.approx([25.0, 100.0])
    assert sum(interval_pcts) / 2 == pytest.approx(62.5)
    dispatch = build_comparison_dispatch(
        frame,
        {name: dispatched.copy() for name in SCENARIO_ORDER},
        tariffs,
        da_prices=np.zeros(len(frame)),
    )
    rows = [row for row in build_monthly_summary(dispatch, cfg, tariffs) if row["scenario"] == "no_battery"]
    assert len(rows) == 1
    assert rows[0]["useful_pv_delivered_kwh"] == pytest.approx(4.0)
    assert rows[0]["total_pv_production_kwh"] == pytest.approx(10.0)
    assert rows[0]["useful_self_consumption_pct"] == pytest.approx(40.0)
    assert rows[0]["useful_self_consumption_pct"] != pytest.approx(62.5)


def test_monthly_summary_reconciles_to_period_and_lists_five_scenarios(tmp_path: Path):
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    result = run_comparison(frame, cfg, output_dir=tmp_path / "run", create_plots=False)
    monthly_path = result.directory / "monthly_summary.csv"
    peaks_path = result.directory / "monthly_peaks.csv"
    assert monthly_path.exists()
    assert peaks_path.exists()
    assert (result.directory / "comparison_dispatch.csv").exists()
    assert (result.directory / "comparison_dispatch.parquet").exists()
    table = pd.read_csv(monthly_path)
    assert list(table.columns) == list(MONTHLY_SUMMARY_COLUMNS)
    assert set(table["scenario"]) == set(SCENARIO_ORDER)
    assert len(table) == 6
    assert (table["complete_local_month"].astype(str).str.lower() == "false").all()
    csv_text = monthly_path.read_text(encoding="utf-8")
    assert "€" not in csv_text
    assert "%" not in csv_text.split("\n", 1)[1]
    summary_csv = pd.read_csv(result.directory / "comparison_summary.csv")
    assert "average_monthly_peak_kw" in summary_csv.columns
    assert pd.isna(summary_csv.loc[0, "average_monthly_peak_kw"])
    assert int(summary_csv.loc[0, "average_monthly_peak_n_complete_months"]) == 0
    rows = build_monthly_summary(result.dispatch, cfg, TariffConfig())
    reconcile_monthly_summary(rows, result.summary)
    for name in SCENARIO_ORDER:
        sub = table.loc[table["scenario"] == name]
        expected = result.summary["scenarios"][name]
        assert float(sub["grid_import_kwh"].sum()) == pytest.approx(expected["grid_import_kwh"])
        assert float(sub["total_energent_pv_revenue_eur"].sum()) == pytest.approx(
            expected["revenue"]["total_energent_pv_revenue_eur"]
        )
        assert float(sub["equivalent_full_cycles"].sum()) == pytest.approx(expected["equivalent_full_cycles"])


def test_dispatch_parquet_matches_csv_and_supports_dataset_filter(tmp_path: Path):
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    result = run_comparison(
        frame,
        BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0),
        output_dir=tmp_path / "run",
        create_plots=False,
    )
    csv_path = result.directory / "comparison_dispatch.csv"
    parquet_path = result.directory / "comparison_dispatch.parquet"
    csv_df = pd.read_csv(csv_path)
    parquet_df = pd.read_parquet(parquet_path)
    assert list(parquet_df.columns) == list(csv_df.columns)
    assert len(parquet_df) == len(csv_df) == len(result.dispatch)
    numeric_cols = [col for col in parquet_df.columns if pd.api.types.is_numeric_dtype(parquet_df[col])]
    pd.testing.assert_frame_equal(
        parquet_df[numeric_cols].reset_index(drop=True),
        csv_df[numeric_cols].reset_index(drop=True),
        check_dtype=False,
        atol=1e-12,
        rtol=0,
    )
    utc = pd.to_datetime(parquet_df["timestamp_utc"], utc=True)
    assert str(utc.dt.tz) == "UTC"
    assert utc.is_unique
    assert utc.is_monotonic_increasing
    local = pd.to_datetime(parquet_df["timestamp_local"])
    assert "Brussels" in str(parquet_df["timestamp_local"].dtype) or "Brussels" in str(getattr(local.dt, "tz", ""))
    parquet_file = pq.ParquetFile(parquet_path)
    assert parquet_file.metadata.num_rows == 2
    assert parquet_file.metadata.row_group(0).num_rows <= PARQUET_ROW_GROUP_SIZE
    assert PARQUET_COMPRESSION in {codec.lower() for codec in _row_group_codecs(parquet_file)}
    second = pd.Timestamp(result.dispatch["timestamp_utc"].iloc[1])
    dataset = ds.dataset(str(parquet_path), format="parquet")
    filtered = dataset.to_table(
        filter=ds.field("timestamp_utc") >= second,
        columns=["timestamp_utc", "pv_production_kwh"],
    )
    assert filtered.num_rows == 1
    assert filtered.column_names == ["timestamp_utc", "pv_production_kwh"]
    meta = json.loads((result.directory / "run_metadata.json").read_text(encoding="utf-8"))
    summary = json.loads((result.directory / "comparison_summary.json").read_text(encoding="utf-8"))
    assert meta["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION == 2
    assert summary["artifact_schema_version"] == 2
    assert "comparison_dispatch.parquet" in meta["filenames"]
    assert "monthly_summary.csv" in meta["filenames"]
    assert result.summary["solvers"]["self_consumption"]["status"] == "OPTIMAL"
    assert result.summary["solvers"]["peak_reduction"]["status"] == "OPTIMAL"
    assert result.summary["solvers"]["revenue"]["status"] == "OPTIMAL"


def test_parquet_keeps_autumn_repeated_local_hour_as_two_utc_rows(tmp_path: Path):
    frame = qh_frame([{"imp": 0.2, "exp": 0.0, "pv": 0.0, "ts": ts} for ts in AUTUMN_STARTS])
    result = run_comparison(
        frame,
        BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0),
        output_dir=tmp_path / "run",
        create_plots=False,
    )
    parquet_df = pd.read_parquet(result.directory / "comparison_dispatch.parquet")
    local = pd.to_datetime(parquet_df["timestamp_local"])
    utc = pd.to_datetime(parquet_df["timestamp_utc"], utc=True)
    hour_two = local.dt.hour == 2
    assert int(hour_two.sum()) >= 2
    assert utc.loc[hour_two].nunique() == int(hour_two.sum())
    assert utc.is_unique
    folded = local[hour_two]
    offsets = {pd.Timestamp(value).utcoffset() for value in folded}
    assert len(offsets) == 2


def _row_group_codecs(parquet_file: pq.ParquetFile) -> list[str]:
    codecs = []
    for index in range(parquet_file.metadata.num_row_groups):
        group = parquet_file.metadata.row_group(index)
        for column_index in range(group.num_columns):
            codecs.append(group.column(column_index).compression)
    return codecs
