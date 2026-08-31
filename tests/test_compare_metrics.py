"""Useful-PV definitions, percentage points, and zero denominators."""

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.metrics import attach_baseline_dispatch, ratio_or_none, scenario_metrics
from tests.lp_frames import qh_frame


def test_ratio_or_none_returns_none_for_zero_denominator():
    assert ratio_or_none(1.0, 0.0) is None
    assert ratio_or_none(0.0, 0.0) is None
    assert ratio_or_none(2.0, 4.0) == 0.5


def test_useful_pv_definitions_and_percentage_points():
    # 10 kWh PV, 6 kWh export => 4 kWh direct. 2 kWh discharge => 6 kWh useful.
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 6.0, "pv": 8.0},
            {"imp": 4.0, "exp": 0.0, "pv": 2.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    baseline = attach_baseline_dispatch(frame, cfg)
    before = scenario_metrics(baseline, cfg, scenario="no_battery")
    assert before["total_pv_production_kwh"] == 10.0
    assert before["useful_pv_direct_kwh"] == 4.0
    assert before["useful_pv_delivered_kwh"] == 4.0
    assert before["additional_useful_pv_kwh"] == 0.0
    assert before["useful_self_consumption_pct_before"] == 40.0
    assert before["useful_self_consumption_pct_after"] == 40.0
    assert before["useful_self_consumption_change_pp"] == 0.0
    assert before["additional_useful_pv_pct_of_total_pv"] == 0.0

    after_frame = baseline.copy()
    after_frame.loc[1, "discharge_load_kwh"] = 2.0
    after_frame.loc[1, "grid_import_kwh"] = 2.0
    after_frame.loc[0, "charge_pv_kwh"] = 2.0
    after_frame.loc[0, "grid_export_kwh"] = 4.0
    after_frame.loc[0, "soc_end_kwh"] = 2.0
    after_frame.loc[1, "soc_start_kwh"] = 2.0
    after = scenario_metrics(after_frame, cfg, scenario="self_consumption")
    assert after["useful_pv_delivered_kwh"] == 6.0
    assert after["additional_useful_pv_kwh"] == 2.0
    assert after["additional_useful_pv_pct_of_total_pv"] == 20.0
    assert after["useful_self_consumption_pct_before"] == pytest.approx(40.0)
    assert after["useful_self_consumption_pct_after"] == pytest.approx(60.0)
    assert after["useful_self_consumption_change_pp"] == pytest.approx(20.0)


def test_zero_pv_and_zero_load_are_null():
    frame = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0, "load": 0.0}])
    # Force reconstructed load to 0 even though the identity would be 1.
    frame["site_load_kwh"] = 0.0
    cfg = BatteryConfig(1, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0)
    metrics = scenario_metrics(attach_baseline_dispatch(frame, cfg), cfg, scenario="no_battery")
    assert metrics["total_pv_production_kwh"] == 0.0
    assert metrics["site_load_kwh"] == 0.0
    assert metrics["useful_self_consumption_ratio_before"] is None
    assert metrics["useful_self_consumption_ratio_after"] is None
    assert metrics["useful_self_consumption_pct_before"] is None
    assert metrics["useful_self_consumption_pct_after"] is None
    assert metrics["useful_self_consumption_change_pp"] is None
    assert metrics["additional_useful_pv_pct_of_total_pv"] is None
    assert metrics["self_sufficiency_ratio"] is None


def test_annual_peak_reduction_and_zero_baseline_peak():
    frame = qh_frame(
        [
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    baseline = attach_baseline_dispatch(frame, cfg)
    metrics = scenario_metrics(baseline, cfg, scenario="no_battery")
    assert metrics["annual_peak_kw"] == pytest.approx(8.0)
    assert metrics["annual_peak_reduction_kw"] == pytest.approx(0.0)
    assert metrics["annual_peak_reduction_pct"] == pytest.approx(0.0)
    assert metrics["average_monthly_peak_kw"] is None
    assert metrics["average_monthly_peak_n_complete_months"] == 0

    shaved = baseline.copy()
    shaved.loc[0, "grid_import_kwh"] = 1.0
    shaved.loc[0, "grid_import_kw"] = 4.0
    shaved.loc[0, "discharge_load_kwh"] = 1.0
    after = scenario_metrics(shaved, cfg, scenario="peak_reduction")
    assert after["annual_peak_reduction_kw"] == pytest.approx(4.0)
    assert after["annual_peak_reduction_pct"] == pytest.approx(50.0)

    zero = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    zero_metrics = scenario_metrics(attach_baseline_dispatch(zero, cfg), cfg, scenario="no_battery")
    assert zero_metrics["annual_peak_kw"] == pytest.approx(0.0)
    assert zero_metrics["annual_peak_reduction_kw"] == pytest.approx(0.0)
    assert zero_metrics["annual_peak_reduction_pct"] is None
