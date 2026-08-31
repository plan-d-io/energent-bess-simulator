"""Hand-computable and Gurobi-differential tests for the experimental HiGHS spike."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import calendar_year_physical_hours, cycle_limit_report
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH
from btm_sim.optimizer import __all__ as OPTIMIZER_PUBLIC
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.highs_backend import build_highs_physical_lp, dispose_highs_lp, optimize_highs_stage
from btm_sim.optimizer.backend import get_optimizer_backend
from btm_sim.optimizer.highs_self_consumption import optimize_self_consumption_highs
from tests.lp_frames import qh_frame

highspy = pytest.importorskip("highspy")

UTC = timezone.utc
# HiGHS default primal/dual feasibility is 1e-7. Keep comparison inside that
# scale for stage optima, and never looser than the Fluvius 0.001 kWh postcheck.
STAGE_ENERGY_ATOL = 1e-6
STAGE_POWER_ATOL = 1e-6
UNIQUE_INTERVAL_ATOL = 1e-5


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


def _compare_aggregates(highs_run, gurobi_run) -> None:
    hs = highs_run.summary
    gs = gurobi_run.summary
    h_stages = {stage["stage"]: stage for stage in highs_run.stages}
    g_stages = {stage["stage"]: stage for stage in gurobi_run.stages}
    for name in (
        "maximize_discharge_load_kwh",
        "minimize_annual_peak_import_kw",
        "minimize_sum_monthly_peak_import_kw",
    ):
        atol = STAGE_ENERGY_ATOL if name == "maximize_discharge_load_kwh" else STAGE_POWER_ATOL
        assert h_stages[name]["optimum"] == pytest.approx(g_stages[name]["optimum"], abs=atol, rel=0.0)
        assert h_stages[name]["status"] == "OPTIMAL"
        assert g_stages[name]["status"] == "OPTIMAL"
    energy_keys = (
        "useful_pv_delivered",
        "useful_additional_pv",
        "grid_import",
        "grid_export",
        "charge_pv",
        "discharge_load",
        "charge_loss",
        "discharge_loss",
        "total_loss",
    )
    for key in energy_keys:
        assert hs["energy_kwh"][key] == pytest.approx(gs["energy_kwh"][key], abs=DOCUMENTED_TOLERANCE_KWH)
    assert hs["peaks_kw"]["annual_max"] == pytest.approx(gs["peaks_kw"]["annual_max"], abs=STAGE_POWER_ATOL)
    assert hs["peaks_kw"]["sum_monthly_max"] == pytest.approx(
        gs["peaks_kw"]["sum_monthly_max"], abs=STAGE_POWER_ATOL
    )
    assert hs["peaks_kw"]["monthly_max"] == pytest.approx(gs["peaks_kw"]["monthly_max"], abs=STAGE_POWER_ATOL)
    assert hs["throughput"]["stored_throughput_kwh"] == pytest.approx(
        gs["throughput"]["stored_throughput_kwh"], abs=DOCUMENTED_TOLERANCE_KWH
    )
    assert hs["throughput"]["equivalent_full_cycles"] == pytest.approx(
        gs["throughput"]["equivalent_full_cycles"], abs=1e-6
    )
    assert hs["soc_initial_kwh"] == pytest.approx(gs["soc_initial_kwh"], abs=UNIQUE_INTERVAL_ATOL)
    assert hs["soc_final_kwh"] == pytest.approx(gs["soc_final_kwh"], abs=UNIQUE_INTERVAL_ATOL)
    assert hs["feasibility"]["ok"] is True
    assert gs["feasibility"]["ok"] is True
    assert hs["reconciliation"]["max_abs_interval_balance_kwh"] <= DOCUMENTED_TOLERANCE_KWH
    assert gs["reconciliation"]["max_abs_interval_balance_kwh"] <= DOCUMENTED_TOLERANCE_KWH


def test_highs_is_not_a_public_optimizer_export():
    assert "optimize_self_consumption_highs" not in OPTIMIZER_PUBLIC
    import btm_sim

    assert not hasattr(btm_sim, "optimize_self_consumption_highs")


def test_highs_model_is_continuous_sparse_lp():
    frame = qh_frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(5, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    lp = build_highs_physical_lp(frame, cfg, enforce_cycle_limit=False)
    try:
        assert lp.highs.getNumCol() == lp.num_col
        assert lp.num_col == 2 + 2 + 3 + 1 + 1
        assert lp.highs.getNumRow() == lp.num_row_physical
        assert lp.num_nz > 0
        model_lp = lp.highs.getLp()
        assert list(model_lp.integrality_) == []
        assert lp.highs.getNumCol() > 0
    finally:
        dispose_highs_lp(lp)


def test_zero_capacity_leaves_baseline_unchanged():
    frame = qh_frame([{"imp": 2.0, "exp": 0.5, "pv": 1.0}, {"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    result = optimize_self_consumption_highs(frame, BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0))
    assert result.ok
    assert result.frame["charge_pv_kwh"].to_numpy() == pytest.approx([0.0, 0.0], abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].to_numpy() == pytest.approx([0.0, 0.0], abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["grid_import_kwh"].to_numpy() == pytest.approx(
        frame["grid_import_baseline_kwh"].to_numpy(), abs=UNIQUE_INTERVAL_ATOL
    )
    assert result.summary["soc_final_kwh"] == pytest.approx(0.0)
    assert result.summary["solver"]["name"] == "HiGHS"
    assert result.summary["solver"]["continuous_lp"] is True
    assert result.summary["solver"]["production_backend"] is True


def test_pv_charge_then_customer_discharge_is_unique():
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption_highs(frame, cfg)
    assert result.frame["soc_start_kwh"].iloc[0] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["soc_end_kwh"].iloc[-1] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(1.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(1.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["charge_pv_kwh"].iloc[1] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)


def test_inverter_time_prevents_full_power_both_directions():
    frame = qh_frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}])
    cfg = BatteryConfig(10, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption_highs(frame, cfg)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(0.5, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.5, abs=UNIQUE_INTERVAL_ATOL)


def test_three_objectives_establish_visible_priority():
    rows = [
        {"ts": datetime(2024, 1, 15, 10, 0, tzinfo=UTC), "imp": 10.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 0, tzinfo=UTC), "imp": 0.0, "exp": 2.0, "pv": 2.0},
        {"ts": datetime(2024, 1, 15, 11, 15, tzinfo=UTC), "imp": 8.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 30, tzinfo=UTC), "imp": 3.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 2, 15, 10, 0, tzinfo=UTC), "imp": 5.0, "exp": 0.0, "pv": 0.0},
    ]
    frame = qh_frame(rows)
    cfg = BatteryConfig(2, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption_highs(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["maximize_discharge_load_kwh"]["optimum"] == pytest.approx(2.0, abs=STAGE_ENERGY_ATOL)
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(40.0, abs=STAGE_POWER_ATOL)
    assert stages["minimize_sum_monthly_peak_import_kw"]["optimum"] == pytest.approx(52.0, abs=STAGE_POWER_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[-1] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)


def test_non_binding_cycle_allowance_keeps_first_path():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 3.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 0.9, 0.8, soc_initial_kwh=0.0)
    result = optimize_self_consumption_highs(frame, cfg)
    assert result.summary["solver"]["cycle_cut_applied"] is False
    assert result.summary["throughput"]["cycle_limit_binding"] is False
    assert result.ok


def test_binding_cycle_allowance_rebuilds_with_hard_cut():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
        ]
    ).copy()
    frame["interval_hours"] = calendar_year_physical_hours(2024) / len(frame)
    limited = BatteryConfig(10, 100, 100, 1.0, 1.0, max_equivalent_full_cycles_per_year=0.05)
    result = optimize_self_consumption_highs(frame, limited)
    assert result.summary["solver"]["cycle_cut_applied"] is True
    report = cycle_limit_report(result.frame, limited)
    assert report["cycle_limit_binding"] is True
    assert report["equivalent_full_cycles"] == pytest.approx(0.05, abs=1e-6)
    assert float(result.frame["discharge_load_kwh"].sum()) == pytest.approx(0.5, abs=1e-6)


def test_infeasible_highs_status_raises():
    frame = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(1, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0)
    lp = build_highs_physical_lp(frame, cfg, enforce_cycle_limit=False)
    try:
        lp.highs.addRow(
            10.0,
            float(highspy.kHighsInf),
            1,
            np.array([lp.idx_discharge], dtype=np.int32),
            np.array([1.0], dtype=np.float64),
        )
        with pytest.raises(OptimizerError, match="did not return an optimal"):
            optimize_highs_stage(lp, stage="impossible")
    finally:
        dispose_highs_lp(lp)


def test_empty_frame_raises():
    empty = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}]).iloc[0:0]
    with pytest.raises(OptimizerError, match="empty"):
        optimize_self_consumption_highs(empty, BatteryConfig(1, 1, 1, 1.0, 1.0, 0.0))


@pytest.mark.skipif(not _gurobi_available(), reason="Gurobi package or licence is not available")
@pytest.mark.parametrize(
    "rows,cfg,unique_intervals",
    [
        (
            [{"imp": 2.0, "exp": 0.5, "pv": 1.0}, {"imp": 0.0, "exp": 1.0, "pv": 1.0}],
            BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
            True,
        ),
        (
            [{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}],
            BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0),
            True,
        ),
        (
            [{"imp": 1.0, "exp": 1.0, "pv": 1.0}],
            BatteryConfig(10, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0),
            True,
        ),
        (
            [
                {"imp": 0.0, "exp": 4.0, "pv": 4.0},
                {"imp": 5.0, "exp": 0.0, "pv": 0.0},
                {"imp": 5.0, "exp": 0.0, "pv": 0.0},
            ],
            BatteryConfig(4, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0),
            False,
        ),
    ],
)
def test_highs_matches_gurobi_on_synthetic_fixtures(rows, cfg, unique_intervals):
    gurobi = get_optimizer_backend("gurobi")

    frame = qh_frame(rows)
    highs_run = optimize_self_consumption_highs(frame, cfg)
    gurobi_run = gurobi.optimize_self_consumption(frame, cfg)
    _compare_aggregates(highs_run, gurobi_run)
    if unique_intervals:
        for column in ("charge_pv_kwh", "discharge_load_kwh", "soc_end_kwh"):
            assert highs_run.frame[column].to_numpy() == pytest.approx(
                gurobi_run.frame[column].to_numpy(), abs=UNIQUE_INTERVAL_ATOL
            )
