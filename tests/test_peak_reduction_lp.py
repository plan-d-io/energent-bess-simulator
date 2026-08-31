"""Hand-computable peak-reduction tests and optional Gurobi checks."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.model import build_physical_lp, optimize_stage
from btm_sim.optimizer.peak_reduction import optimize_peak_reduction
from btm_sim.optimizer.self_consumption import optimize_self_consumption
from tests.lp_frames import qh_frame

UTC = timezone.utc


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


GUROBI_AVAILABLE = _gurobi_available()


@pytest.mark.skipif(not GUROBI_AVAILABLE, reason="Gurobi package or licence is not available")
def test_peak_lp_is_continuous():
    frame = qh_frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(5, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    lp = build_physical_lp(frame, cfg)
    assert lp.model.NumIntVars == 0
    assert lp.model.NumBinVars == 0


def test_zero_battery_leaves_baseline_unchanged():
    frame = qh_frame([{"imp": 2.0, "exp": 0.5, "pv": 1.0}, {"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    result = optimize_peak_reduction(frame, BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0))
    assert result.ok
    assert result.frame["charge_pv_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert result.frame["discharge_load_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert result.frame["grid_import_kwh"].tolist() == pytest.approx(frame["grid_import_baseline_kwh"].tolist())
    assert result.summary["soc_final_kwh"] == pytest.approx(0.0)
    assert result.summary["case"] == "peak_reduction_first"
    assert "perfect_foresight_upper_bound" not in result.summary["result_description"]


def test_terminal_soc_equals_initial():
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    assert result.frame["soc_start_kwh"].iloc[0] == pytest.approx(0.0)
    assert result.frame["soc_end_kwh"].iloc[-1] == pytest.approx(0.0)


def test_efficiencies_reduce_deliverable_energy():
    frame = qh_frame([{"imp": 0.0, "exp": 2.0, "pv": 2.0}, {"imp": 5.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, eta_charge=0.5, eta_discharge=0.5, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(2.0, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(0.5, abs=1e-5)


def test_power_and_energy_limits():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 10.0, "pv": 10.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(e_usable_kwh=0.4, p_charge_kw=4.0, p_discharge_kw=4.0, eta_charge=1.0, eta_discharge=1.0)
    result = optimize_peak_reduction(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(0.4, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(0.4, abs=1e-5)


def test_inverter_time_prevents_full_power_both_directions():
    frame = qh_frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}])
    cfg = BatteryConfig(10, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(0.5, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.5, abs=1e-5)


def test_empty_frame_raises():
    empty = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}]).iloc[0:0]
    with pytest.raises(OptimizerError, match="empty"):
        optimize_peak_reduction(empty, BatteryConfig(1, 1, 1, 1.0, 1.0, 0.0))


@pytest.mark.skipif(not GUROBI_AVAILABLE, reason="Gurobi package or licence is not available")
def test_infeasible_gurobi_status_raises():
    frame = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(1, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0)
    lp = build_physical_lp(frame, cfg)
    lp.model.addConstr(lp.discharge.sum() >= 10.0, name="impossible")
    with pytest.raises(OptimizerError, match="did not return an optimal"):
        optimize_stage(lp, stage="impossible")


def test_annual_peak_is_reduced_first():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(4, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(12.0, abs=1e-4)
    assert result.frame["grid_import_kw"].max() == pytest.approx(12.0, abs=1e-4)
    assert result.frame["discharge_load_kwh"].sum() == pytest.approx(4.0, abs=1e-5)


def test_monthly_peaks_are_reduced_before_useful_pv():
    rows = [
        {"ts": datetime(2024, 1, 15, 10, 0, tzinfo=UTC), "imp": 10.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 0, tzinfo=UTC), "imp": 0.0, "exp": 2.0, "pv": 2.0},
        {"ts": datetime(2024, 1, 15, 11, 15, tzinfo=UTC), "imp": 8.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 30, tzinfo=UTC), "imp": 3.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 2, 15, 10, 0, tzinfo=UTC), "imp": 5.0, "exp": 0.0, "pv": 0.0},
    ]
    frame = qh_frame(rows)
    cfg = BatteryConfig(2, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(40.0, abs=1e-4)
    assert stages["minimize_sum_monthly_peak_import_kw"]["optimum"] == pytest.approx(52.0, abs=1e-3)
    assert result.frame["discharge_load_kwh"].iloc[-1] == pytest.approx(2.0, abs=1e-4)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_later_step_cannot_worsen_annual_or_monthly_peak():
    # After the annual peak is lowered to 20 kW, leftover export can still be
    # cycled through the morning import and refilled before the afternoon peak.
    # That extra PV delivery is allowed only if the 20 kW peak is kept.
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 5.0, "pv": 5.0},
            {"imp": 4.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(5, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    peak = optimize_peak_reduction(frame, cfg)
    self_consumption = optimize_self_consumption(frame, cfg)
    peak_stages = {stage["stage"]: stage for stage in peak.stages}

    assert peak_stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(20.0, abs=1e-4)
    assert peak_stages["maximize_discharge_load_kwh"]["optimum"] == pytest.approx(6.0, abs=1e-4)
    assert peak.frame["grid_import_kw"].max() == pytest.approx(20.0, abs=1e-4)
    assert peak.summary["peaks_kw"]["annual_max"] == pytest.approx(
        peak_stages["minimize_annual_peak_import_kw"]["optimum"], abs=1e-4
    )
    assert peak.summary["peaks_kw"]["sum_monthly_max"] == pytest.approx(
        peak_stages["minimize_sum_monthly_peak_import_kw"]["optimum"], abs=1e-4
    )
    assert peak.summary["peaks_kw"]["annual_max"] <= self_consumption.summary["peaks_kw"]["annual_max"] + 1e-6
    assert self_consumption.summary["energy_kwh"]["useful_additional_pv"] + 1e-6 >= (
        peak.summary["energy_kwh"]["useful_additional_pv"]
    )


def test_third_step_can_add_pv_without_raising_peaks():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 5.0, "pv": 5.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
            {"imp": 1.0, "exp": 1.0, "pv": 1.0},
        ]
    )
    cfg = BatteryConfig(5, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(20.0, abs=1e-4)
    assert stages["maximize_discharge_load_kwh"]["optimum"] == pytest.approx(6.0, abs=1e-4)
    assert result.frame["grid_import_kw"].max() == pytest.approx(20.0, abs=1e-4)
    assert result.summary["peaks_kw"]["sum_monthly_max"] == pytest.approx(
        stages["minimize_sum_monthly_peak_import_kw"]["optimum"], abs=1e-4
    )


def test_report_reconciliation_and_three_steps():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 3.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 0.9, 0.8, soc_initial_kwh=0.0)
    result = optimize_peak_reduction(frame, cfg)
    assert len(result.stages) == 3
    assert [stage["stage"] for stage in result.stages] == [
        "minimize_annual_peak_import_kw",
        "minimize_sum_monthly_peak_import_kw",
        "maximize_discharge_load_kwh",
    ]
    energy = result.summary["energy_kwh"]
    rec = result.summary["reconciliation"]
    assert rec["max_abs_interval_balance_kwh"] < 1e-9
    assert rec["loss_identity_residual_kwh"] == pytest.approx(0.0, abs=1e-9)
    assert rec["terminal_soc_gap_kwh"] == pytest.approx(0.0, abs=1e-9)
    assert energy["useful_additional_pv"] == pytest.approx(energy["discharge_load"])
    assert energy["charge_pv"] - energy["discharge_load"] - energy["total_loss"] == pytest.approx(0.0, abs=1e-9)
    throughput = result.summary["throughput"]["stored_throughput_kwh"]
    assert throughput == pytest.approx(2.0 * energy["discharge_load"] / cfg.eta_discharge)
    assert result.summary["battery_limits_and_balances"] == "passed"
    assert result.summary["solver"]["continuous_lp"] is True
    assert result.summary["feasibility"]["ok"] is True
