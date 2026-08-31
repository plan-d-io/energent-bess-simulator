"""Sweep peak-reduction reporting: comparison metrics, ranking, and missing values."""

from __future__ import annotations

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.metrics import attach_baseline_dispatch, scenario_metrics
from btm_sim.sweep.economics import recommend
from btm_sim.sweep.peaks import (
    DISPATCH_STRATEGY,
    PEAK_CANDIDATE_KEYS,
    PEAK_EXPLANATION,
    PEAK_SNAPSHOT_KEYS,
    baseline_peak_fields_from_metrics,
    build_peak_summary,
    candidate_peak_fields_from_metrics,
    select_largest_average_monthly_peak_reduction,
    select_largest_highest_interval_peak_reduction,
)
from tests.lp_frames import qh_frame
from tests.test_compare_months import complete_month_frame


def _row(**overrides):
    base = {
        "candidate_id": "c001",
        "duration_hours": 2.0,
        "power_kw": 10.0,
        "usable_energy_kwh": 20.0,
        "estimated_capex_eur": 6000.0,
        "annual_revenue_uplift_eur": 800.0,
        "estimated_value_eur": 2000.0,
        "simple_payback_years": 7.5,
        "equivalent_full_cycles": 100.0,
        "cycle_limit_binding": False,
        "baseline_annual_peak_kw": 12.0,
        "annual_peak_kw": 8.0,
        "annual_peak_reduction_kw": 4.0,
        "annual_peak_reduction_pct": 100.0 * 4.0 / 12.0,
        "baseline_average_monthly_peak_kw": 10.0,
        "average_monthly_peak_kw": 6.0,
        "average_monthly_peak_reduction_kw": 4.0,
        "average_monthly_peak_reduction_pct": 40.0,
        "average_monthly_peak_n_complete_months": 12,
    }
    base.update(overrides)
    return base


def test_candidate_peak_fields_match_comparison_metric_definitions():
    import pandas as pd

    jan = complete_month_frame(2024, 1, import_kwh=0.1, peak_import_kwh=2.5)
    feb = complete_month_frame(2024, 2, import_kwh=0.1, peak_import_kwh=2.0)
    mar = complete_month_frame(2024, 3, import_kwh=0.1, peak_import_kwh=3.0)
    canonical = pd.concat([jan, feb, mar], ignore_index=True)
    cfg = BatteryConfig(100, 50, 50, 1.0, 1.0, soc_initial_kwh=0.0)
    baseline = attach_baseline_dispatch(canonical, cfg)
    shaved = baseline.copy()
    jan_peak_i = int(jan["grid_import_baseline_kwh"].to_numpy().argmax())
    mar_peak_i = len(jan) + len(feb) + int(mar["grid_import_baseline_kwh"].to_numpy().argmax())
    shaved.loc[jan_peak_i, "grid_import_kwh"] = 1.5
    shaved.loc[jan_peak_i, "grid_import_kw"] = 6.0
    shaved.loc[mar_peak_i, "grid_import_kwh"] = 1.75
    shaved.loc[mar_peak_i, "grid_import_kw"] = 7.0
    metrics = scenario_metrics(shaved, cfg, scenario="revenue")
    copied = candidate_peak_fields_from_metrics(metrics)
    for key in PEAK_CANDIDATE_KEYS:
        assert copied[key] == metrics[key]
    assert copied["annual_peak_reduction_kw"] == pytest.approx(
        metrics["baseline_annual_peak_kw"] - metrics["annual_peak_kw"]
    )
    assert copied["average_monthly_peak_reduction_kw"] == pytest.approx(
        metrics["baseline_average_monthly_peak_kw"] - metrics["average_monthly_peak_kw"]
    )
    assert copied["average_monthly_peak_n_complete_months"] == 3
    baseline_fields = baseline_peak_fields_from_metrics(
        scenario_metrics(baseline, cfg, scenario="no_battery")
    )
    assert baseline_fields["average_monthly_peak_n_complete_months"] == 3
    assert baseline_fields["annual_peak_kw"] == pytest.approx(12.0)


def test_percentage_null_when_baseline_peak_is_zero():
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    metrics = scenario_metrics(attach_baseline_dispatch(frame, cfg), cfg, scenario="no_battery")
    copied = candidate_peak_fields_from_metrics(metrics)
    assert copied["annual_peak_kw"] == pytest.approx(0.0)
    assert copied["annual_peak_reduction_kw"] == pytest.approx(0.0)
    assert copied["annual_peak_reduction_pct"] is None
    assert copied["average_monthly_peak_reduction_pct"] is None


def test_negative_peak_reductions_remain_negative():
    frame = qh_frame(
        [
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    worse = attach_baseline_dispatch(frame, cfg)
    worse.loc[0, "grid_import_kwh"] = 3.0
    worse.loc[0, "grid_import_kw"] = 12.0
    metrics = scenario_metrics(worse, cfg, scenario="revenue")
    copied = candidate_peak_fields_from_metrics(metrics)
    assert copied["annual_peak_reduction_kw"] == pytest.approx(-4.0)
    assert copied["annual_peak_reduction_pct"] == pytest.approx(-50.0)
    summary = build_peak_summary(
        [_row(annual_peak_reduction_kw=-4.0, average_monthly_peak_reduction_kw=-1.0)]
    )
    assert summary["largest_highest_interval_peak_reduction_candidate"] is None
    assert summary["largest_average_monthly_peak_reduction_candidate"] is None
    assert summary["candidates_with_positive_average_monthly_peak_reduction_count"] == 0


def test_no_complete_month_makes_average_monthly_peak_unavailable():
    frame = qh_frame([{"imp": 2.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    metrics = scenario_metrics(attach_baseline_dispatch(frame, cfg), cfg, scenario="no_battery")
    copied = candidate_peak_fields_from_metrics(metrics)
    assert copied["average_monthly_peak_kw"] is None
    assert copied["average_monthly_peak_reduction_kw"] is None
    assert copied["average_monthly_peak_n_complete_months"] == 0
    summary = build_peak_summary(
        [
            _row(
                average_monthly_peak_kw=None,
                average_monthly_peak_reduction_kw=None,
                average_monthly_peak_n_complete_months=0,
                baseline_average_monthly_peak_kw=None,
                annual_peak_reduction_kw=1.0,
            )
        ]
    )
    assert summary["average_monthly_peak_available"] is False
    assert summary["largest_average_monthly_peak_reduction_candidate"] is None
    assert summary["average_monthly_peak_n_complete_months"] == 0
    assert summary["largest_highest_interval_peak_reduction_candidate"]["candidate_id"] == "c001"


def test_largest_reduction_tie_break_is_deterministic():
    rows = [
        _row(
            candidate_id="z",
            average_monthly_peak_reduction_kw=5.0,
            annual_peak_reduction_kw=5.0,
            estimated_capex_eur=5000.0,
            usable_energy_kwh=20.0,
            power_kw=10.0,
            duration_hours=4.0,
        ),
        _row(
            candidate_id="a",
            average_monthly_peak_reduction_kw=5.0,
            annual_peak_reduction_kw=5.0,
            estimated_capex_eur=4000.0,
            usable_energy_kwh=30.0,
            power_kw=15.0,
            duration_hours=2.0,
        ),
        _row(
            candidate_id="b",
            average_monthly_peak_reduction_kw=4.0,
            annual_peak_reduction_kw=9.0,
            estimated_capex_eur=1000.0,
        ),
    ]
    assert select_largest_average_monthly_peak_reduction(rows)["candidate_id"] == "a"
    assert select_largest_highest_interval_peak_reduction(rows)["candidate_id"] == "b"
    same = [
        _row(candidate_id="late", average_monthly_peak_reduction_kw=8.0, estimated_capex_eur=1000.0),
        _row(candidate_id="early", average_monthly_peak_reduction_kw=8.0, estimated_capex_eur=1000.0),
    ]
    assert select_largest_average_monthly_peak_reduction(same)["candidate_id"] == "early"


def test_peak_summary_is_null_when_no_positive_reduction():
    summary = build_peak_summary(
        [
            _row(average_monthly_peak_reduction_kw=0.0, annual_peak_reduction_kw=0.0),
            _row(
                candidate_id="c002",
                average_monthly_peak_reduction_kw=-0.5,
                annual_peak_reduction_kw=-1.0,
            ),
        ]
    )
    assert summary["dispatch_strategy"] == DISPATCH_STRATEGY
    assert summary["financial_value_modelled"] is False
    assert summary["largest_average_monthly_peak_reduction_candidate"] is None
    assert summary["largest_highest_interval_peak_reduction_candidate"] is None
    assert summary["candidates_with_positive_average_monthly_peak_reduction_count"] == 0
    assert summary["explanation"] == PEAK_EXPLANATION
    assert "bill savings" in summary["explanation"]
    snapshot = build_peak_summary([_row()]).get("largest_average_monthly_peak_reduction_candidate")
    assert snapshot is not None
    assert list(snapshot.keys()) == list(PEAK_SNAPSHOT_KEYS)


def test_screening_snapshots_and_per_duration_include_peak_fields():
    rows = [
        _row(
            candidate_id="d2a",
            duration_hours=2.0,
            power_kw=10.0,
            usable_energy_kwh=20.0,
            estimated_value_eur=100.0,
            annual_revenue_uplift_eur=90.0,
            estimated_capex_eur=6000.0,
            simple_payback_years=8.0,
            average_monthly_peak_reduction_kw=2.0,
            annual_peak_reduction_kw=1.0,
        ),
        _row(
            candidate_id="d2b",
            duration_hours=2.0,
            power_kw=20.0,
            usable_energy_kwh=40.0,
            estimated_value_eur=80.0,
            annual_revenue_uplift_eur=100.0,
            estimated_capex_eur=12000.0,
            simple_payback_years=12.0,
            average_monthly_peak_reduction_kw=5.0,
            annual_peak_reduction_kw=3.0,
        ),
        _row(
            candidate_id="d4a",
            duration_hours=4.0,
            power_kw=10.0,
            usable_energy_kwh=40.0,
            estimated_value_eur=10.0,
            annual_revenue_uplift_eur=40.0,
            estimated_capex_eur=12000.0,
            simple_payback_years=None,
            average_monthly_peak_reduction_kw=1.0,
            annual_peak_reduction_kw=0.5,
        ),
        _row(
            candidate_id="d4b",
            duration_hours=4.0,
            power_kw=20.0,
            usable_energy_kwh=80.0,
            estimated_value_eur=40.0,
            annual_revenue_uplift_eur=50.0,
            estimated_capex_eur=24000.0,
            simple_payback_years=11.0,
            average_monthly_peak_reduction_kw=8.0,
            annual_peak_reduction_kw=6.0,
        ),
    ]
    choice = recommend(rows, revenue_capture_threshold_pct=95.0, evaluation_period_years=10.0)
    shortest = choice["screening_summary"]["shortest_payback_candidate"]
    highest = choice["screening_summary"]["highest_annual_revenue_candidate"]
    assert shortest["candidate_id"] == "d2a"
    assert shortest["average_monthly_peak_reduction_kw"] == 2.0
    assert shortest["annual_peak_reduction_kw"] == 1.0
    assert highest["candidate_id"] == "d2b"
    assert highest["average_monthly_peak_reduction_kw"] == 5.0
    peak = choice["peak_summary"]
    assert peak["largest_average_monthly_peak_reduction_candidate"]["candidate_id"] == "d4b"
    assert peak["largest_highest_interval_peak_reduction_candidate"]["candidate_id"] == "d4b"
    by_duration = {item["duration_hours"]: item for item in choice["best_per_duration"]}
    assert by_duration[2.0]["highest_value_candidate_id"] == "d2a"
    assert by_duration[2.0]["shortest_payback_candidate_id"] == "d2a"
    assert by_duration[2.0]["largest_average_monthly_peak_reduction_candidate_id"] == "d2b"
    assert by_duration[2.0]["largest_average_monthly_peak_reduction_candidate"]["candidate_id"] == "d2b"
    assert by_duration[4.0]["largest_average_monthly_peak_reduction_candidate_id"] == "d4b"
    assert by_duration[4.0]["range_boundary_reached"] is True
