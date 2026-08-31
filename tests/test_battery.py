"""Hand-computable battery physics and reference-controller tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.battery.physics import (
    apply_step,
    check_dispatch_feasibility,
    equivalent_full_cycles,
    interval_energy_balance_residual,
    next_soc_kwh,
    stored_throughput_kwh,
)
from btm_sim.battery.dispatch import run_reference_controller
from btm_sim.fluvius.constants import CANONICAL_COLUMNS, INTERVAL_HOURS

UTC = timezone.utc


def _frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    start = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
    records = []
    for index, row in enumerate(rows):
        import0 = row["imp"]
        export0 = row["exp"]
        pv = row["pv"]
        load = row.get("load", pv + import0 - export0)
        ts = pd.Timestamp(start) + pd.Timedelta(minutes=15 * index)
        records.append(
            {
                "timestamp_utc": ts,
                "timestamp_local": ts.tz_convert("Europe/Brussels"),
                "interval_hours": INTERVAL_HOURS,
                "grid_import_baseline_kwh": import0,
                "grid_export_baseline_kwh": export0,
                "pv_production_kwh": pv,
                "site_load_kwh": load,
                "offtake_quality": "validated",
                "injection_quality": "validated",
                "pv_quality": "validated",
                "quality_flag": "validated",
                "pv_source": "measured_fluvius",
            }
        )
    frame = pd.DataFrame.from_records(records)
    assert list(frame.columns)[: len(CANONICAL_COLUMNS)]
    return frame


def test_config_rejects_invalid_efficiency():
    with pytest.raises(BatteryConfigError):
        BatteryConfig(1, 1, 1, eta_charge=0.0, eta_discharge=1.0)
    with pytest.raises(BatteryConfigError):
        BatteryConfig(1, 1, 1, eta_charge=1.0, eta_discharge=1.1)


def test_config_keeps_asymmetric_power():
    cfg = BatteryConfig(10, p_charge_kw=4, p_discharge_kw=8, eta_charge=0.9, eta_discharge=0.8)
    assert cfg.p_charge_kw == 4
    assert cfg.p_discharge_kw == 8
    symmetric = BatteryConfig.with_symmetric_power(10, 5, 0.95, 0.95)
    assert symmetric.p_charge_kw == symmetric.p_discharge_kw == 5


def test_zero_battery_leaves_baseline_unchanged():
    frame = _frame([{"imp": 1.0, "exp": 0.5, "pv": 2.0}, {"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    cfg = BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, soc_initial_kwh=0.0)
    result = run_reference_controller(frame, cfg)
    assert result.feasibility_ok
    dispatched = result.frame
    assert dispatched["charge_pv_kwh"].tolist() == [0.0, 0.0]
    assert dispatched["discharge_load_kwh"].tolist() == [0.0, 0.0]
    assert dispatched["grid_import_kwh"].tolist() == frame["grid_import_baseline_kwh"].tolist()
    assert dispatched["grid_export_kwh"].tolist() == frame["grid_export_baseline_kwh"].tolist()
    assert result.summary["soc_final_kwh"] == 0.0
    assert result.summary["energy_kwh"]["useful_additional_pv"] == 0.0
    assert result.summary["throughput"]["equivalent_full_cycles"] == 0.0


def test_apply_step_losses_and_soc():
    cfg = BatteryConfig(10, 4, 4, eta_charge=0.9, eta_discharge=0.8, soc_initial_kwh=0.0)
    charged = apply_step(
        0.0,
        1.0,
        0.0,
        config=cfg,
        grid_import_baseline_kwh=0.0,
        grid_export_baseline_kwh=2.0,
    )
    assert charged.soc_end_kwh == pytest.approx(0.9)
    assert charged.charge_loss_kwh == pytest.approx(0.1)
    assert charged.grid_export_kwh == pytest.approx(1.0)
    discharged = apply_step(
        0.9,
        0.0,
        0.72,
        config=cfg,
        grid_import_baseline_kwh=3.0,
        grid_export_baseline_kwh=0.0,
    )
    assert discharged.soc_end_kwh == pytest.approx(0.0)
    assert discharged.discharge_loss_kwh == pytest.approx(0.18)
    assert discharged.grid_import_kwh == pytest.approx(2.28)


def test_power_limit_caps_charge():
    # P=4 kW * 0.25 h = 1 kWh even though 2 kWh is exported.
    frame = _frame([{"imp": 0.0, "exp": 2.0, "pv": 2.0}])
    cfg = BatteryConfig(100, 4, 4, 1.0, 1.0)
    result = run_reference_controller(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(1.0)
    assert result.frame["soc_end_kwh"].iloc[0] == pytest.approx(1.0)


def test_capacity_limit_caps_charge_with_efficiency():
    # Remaining stored room 1 kWh at eta_c=0.5 allows 2 kWh AC charge.
    frame = _frame([{"imp": 0.0, "exp": 10.0, "pv": 10.0}])
    cfg = BatteryConfig(1.0, 100, 100, eta_charge=0.5, eta_discharge=1.0)
    result = run_reference_controller(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(2.0)
    assert result.frame["soc_end_kwh"].iloc[0] == pytest.approx(1.0)
    assert result.frame["charge_loss_kwh"].iloc[0] == pytest.approx(1.0)


def test_discharge_limited_by_soc_and_efficiency():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, eta_charge=1.0, eta_discharge=0.5)
    result = run_reference_controller(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(1.0)
    assert result.frame["soc_end_kwh"].iloc[0] == pytest.approx(1.0)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(0.5)
    assert result.frame["soc_end_kwh"].iloc[1] == pytest.approx(0.0)
    assert result.frame["discharge_loss_kwh"].iloc[1] == pytest.approx(0.5)


def test_soc_never_leaves_bounds_and_chains():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 3.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(1.5, 8, 8, 1.0, 1.0)
    result = run_reference_controller(frame, cfg)
    soc_start = result.frame["soc_start_kwh"]
    soc_end = result.frame["soc_end_kwh"]
    assert (soc_start >= -1e-12).all() and (soc_end >= -1e-12).all()
    assert (soc_start <= 1.5 + 1e-12).all() and (soc_end <= 1.5 + 1e-12).all()
    assert soc_end.iloc[0] == pytest.approx(soc_start.iloc[1])
    assert soc_end.iloc[1] == pytest.approx(soc_start.iloc[2])
    assert result.frame["soc_start_kwh"].iloc[0] == 0.0


def test_pv_only_charging_and_load_only_discharge():
    frame = _frame(
        [
            {"imp": 4.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=2.0)
    result = run_reference_controller(frame, cfg)
    # Interval 0 has no net export, so empty-battery charging from import is forbidden.
    # Initial SoC 2 kWh is discharged into the 4 kWh import.
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(0.0)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(2.0)
    assert result.frame["grid_import_kwh"].iloc[0] <= frame["grid_import_baseline_kwh"].iloc[0]
    # Interval 1 has no net import; stored energy must not be exported.
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(0.0)
    assert result.frame["charge_pv_kwh"].iloc[1] <= frame["grid_export_baseline_kwh"].iloc[1]


def test_simultaneous_import_export_uses_net_and_preserves_counterflow():
    frame = _frame([{"imp": 3.0, "exp": 2.0, "pv": 2.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=5.0)
    result = run_reference_controller(frame, cfg)
    row = result.frame.iloc[0]
    assert row["net_export_available_kwh"] == pytest.approx(0.0)
    assert row["net_import_need_kwh"] == pytest.approx(1.0)
    assert row["charge_pv_kwh"] == pytest.approx(0.0)
    assert row["discharge_load_kwh"] == pytest.approx(1.0)
    assert row["grid_export_kwh"] == pytest.approx(2.0)
    assert row["grid_import_kwh"] == pytest.approx(2.0)
    assert row["grid_import_baseline_kwh"] == pytest.approx(3.0)
    assert row["grid_export_baseline_kwh"] == pytest.approx(2.0)


def test_controller_never_charges_and_discharges_together():
    frame = _frame(
        [
            {"imp": 2.0, "exp": 1.0, "pv": 1.5},
            {"imp": 0.5, "exp": 2.0, "pv": 2.0},
            {"imp": 1.0, "exp": 1.0, "pv": 1.0},
        ]
    )
    cfg = BatteryConfig(10, 8, 8, 0.9, 0.9, soc_initial_kwh=1.0)
    result = run_reference_controller(frame, cfg)
    both = (result.frame["charge_pv_kwh"] > 1e-12) & (result.frame["discharge_load_kwh"] > 1e-12)
    assert not bool(both.any())


def test_inverter_time_rejects_full_power_both_directions():
    frame = _frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}])
    cfg = BatteryConfig(10, 4, 4, 1.0, 1.0)
    dispatched = run_reference_controller(frame, cfg).frame.copy()
    dispatched["charge_pv_kwh"] = 1.0
    dispatched["discharge_load_kwh"] = 1.0
    dispatched["grid_import_kwh"] = 0.0
    dispatched["grid_export_kwh"] = 0.0
    dispatched["soc_end_kwh"] = next_soc_kwh(0.0, 1.0, 1.0, cfg)
    feasibility = check_dispatch_feasibility(dispatched, cfg)
    assert not feasibility.ok
    assert any(item["code"] == "INVERTER_TIME" for item in feasibility.violations)


def test_inverter_time_allows_half_and_half_share():
    frame = _frame([{"imp": 0.5, "exp": 0.5, "pv": 0.5}])
    cfg = BatteryConfig(10, 4, 4, 1.0, 1.0)
    dispatched = run_reference_controller(frame, cfg).frame.copy()
    dispatched["charge_pv_kwh"] = 0.5
    dispatched["discharge_load_kwh"] = 0.5
    dispatched["grid_import_kwh"] = 0.0
    dispatched["grid_export_kwh"] = 0.0
    dispatched["soc_start_kwh"] = 0.5
    dispatched["soc_end_kwh"] = next_soc_kwh(0.5, 0.5, 0.5, cfg)
    feasibility = check_dispatch_feasibility(dispatched, cfg)
    assert feasibility.ok


def test_loss_identity_and_interval_balance():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 0.8, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 8, 8, 0.9, 0.8)
    result = run_reference_controller(frame, cfg)
    energy = result.summary["energy_kwh"]
    delta_soc = result.summary["soc_final_kwh"] - result.summary["soc_initial_kwh"]
    assert energy["charge_pv"] - energy["discharge_load"] - energy["total_loss"] == pytest.approx(delta_soc)
    residual = interval_energy_balance_residual(result.frame)
    assert np.max(np.abs(residual)) < 1e-9
    throughput = stored_throughput_kwh(result.frame["charge_pv_kwh"], result.frame["discharge_load_kwh"], cfg)
    assert result.summary["throughput"]["stored_throughput_kwh"] == pytest.approx(throughput)


def test_lower_efficiency_cannot_increase_useful_delivery():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    high = run_reference_controller(frame, BatteryConfig(10, 100, 100, 1.0, 1.0))
    low = run_reference_controller(frame, BatteryConfig(10, 100, 100, 0.5, 0.5))
    assert low.summary["energy_kwh"]["useful_additional_pv"] <= high.summary["energy_kwh"]["useful_additional_pv"] + 1e-12
    assert high.summary["energy_kwh"]["useful_additional_pv"] == pytest.approx(2.0)
    assert low.summary["energy_kwh"]["useful_additional_pv"] == pytest.approx(0.5)


def test_output_is_deterministic_and_labelled():
    frame = _frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(5, 6, 7, 0.95, 0.9)
    first = run_reference_controller(frame, cfg)
    second = run_reference_controller(frame, cfg)
    cols = ["charge_pv_kwh", "discharge_load_kwh", "soc_end_kwh", "grid_import_kwh", "grid_export_kwh"]
    pd.testing.assert_frame_equal(first.frame[cols], second.frame[cols])
    assert first.summary["label"] == "diagnostic_reference"
    assert first.summary["diagnostic_reference"] is True
    assert first.summary["not_upper_bound"] is True
    assert first.summary["soc_initial_kwh"] == 0.0
    assert equivalent_full_cycles(0.0, 0.0) == 0.0
