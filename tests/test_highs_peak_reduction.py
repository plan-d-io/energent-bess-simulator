"""HiGHS Peak reduction and Gurobi differential tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import calendar_year_physical_hours, cycle_limit_report
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH
from btm_sim.optimizer.backend import get_optimizer_backend
from btm_sim.optimizer.highs_peak_reduction import optimize_peak_reduction_highs
from tests.lp_frames import qh_frame

pytest.importorskip("highspy")

UTC = timezone.utc
STAGE_ENERGY_ATOL = 1e-6
STAGE_POWER_ATOL = 1e-6
UNIQUE_INTERVAL_ATOL = 1e-5


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


def test_highs_peak_zero_capacity():
    frame = qh_frame([{"imp": 2.0, "exp": 0.5, "pv": 1.0}, {"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    result = optimize_peak_reduction_highs(frame, BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0))
    assert result.ok
    assert result.frame["charge_pv_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert result.frame["discharge_load_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert result.summary["solver"]["name"] == "HiGHS"
    assert result.summary["case"] == "peak_reduction_first"


def test_highs_peak_annual_priority_unique():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(4, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction_highs(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(12.0, abs=STAGE_POWER_ATOL)
    assert result.frame["grid_import_kw"].max() == pytest.approx(12.0, abs=STAGE_POWER_ATOL)
    assert result.frame["discharge_load_kwh"].sum() == pytest.approx(4.0, abs=STAGE_ENERGY_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[2] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)


def test_highs_peak_monthly_tie_break():
    rows = [
        {"ts": datetime(2024, 1, 15, 10, 0, tzinfo=UTC), "imp": 10.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 0, tzinfo=UTC), "imp": 0.0, "exp": 2.0, "pv": 2.0},
        {"ts": datetime(2024, 1, 15, 11, 15, tzinfo=UTC), "imp": 8.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 30, tzinfo=UTC), "imp": 3.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 2, 15, 10, 0, tzinfo=UTC), "imp": 5.0, "exp": 0.0, "pv": 0.0},
    ]
    frame = qh_frame(rows)
    cfg = BatteryConfig(2, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction_highs(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(40.0, abs=STAGE_POWER_ATOL)
    assert stages["minimize_sum_monthly_peak_import_kw"]["optimum"] == pytest.approx(52.0, abs=STAGE_POWER_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[-1] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)


def test_highs_peak_third_priority_adds_customer_discharge():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 5.0, "pv": 5.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
            {"imp": 1.0, "exp": 1.0, "pv": 1.0},
        ]
    )
    cfg = BatteryConfig(5, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_peak_reduction_highs(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(20.0, abs=STAGE_POWER_ATOL)
    assert stages["maximize_discharge_load_kwh"]["optimum"] == pytest.approx(6.0, abs=STAGE_ENERGY_ATOL)
    assert result.frame["grid_import_kw"].max() == pytest.approx(20.0, abs=STAGE_POWER_ATOL)


def test_highs_peak_non_binding_cycle_path():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 3.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 0.9, 0.8, soc_initial_kwh=0.0)
    result = optimize_peak_reduction_highs(frame, cfg)
    assert result.summary["solver"]["cycle_cut_applied"] is False
    assert result.ok


def test_highs_peak_binding_cycle_path():
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
    result = optimize_peak_reduction_highs(frame, limited)
    assert result.summary["solver"]["cycle_cut_applied"] is True
    report = cycle_limit_report(result.frame, limited)
    assert report["cycle_limit_binding"] is True
    assert float(result.frame["discharge_load_kwh"].sum()) == pytest.approx(0.5, abs=1e-6)


@pytest.mark.skipif(not _gurobi_available(), reason="Gurobi package or licence is not available")
def test_highs_peak_matches_gurobi_on_unique_fixture():
    gurobi = get_optimizer_backend("gurobi")

    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(4, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    highs = optimize_peak_reduction_highs(frame, cfg)
    gurobi_run = gurobi.optimize_peak_reduction(frame, cfg)
    for stage_name in (
        "minimize_annual_peak_import_kw",
        "minimize_sum_monthly_peak_import_kw",
        "maximize_discharge_load_kwh",
    ):
        h = next(s for s in highs.stages if s["stage"] == stage_name)
        g = next(s for s in gurobi_run.stages if s["stage"] == stage_name)
        atol = STAGE_ENERGY_ATOL if "kwh" in stage_name else STAGE_POWER_ATOL
        assert h["optimum"] == pytest.approx(g["optimum"], abs=atol)
    for column in ("charge_pv_kwh", "discharge_load_kwh", "soc_end_kwh"):
        assert highs.frame[column].to_numpy() == pytest.approx(
            gurobi_run.frame[column].to_numpy(), abs=UNIQUE_INTERVAL_ATOL
        )
    assert highs.summary["feasibility"]["ok"] is True
    assert gurobi_run.summary["feasibility"]["ok"] is True
    assert highs.summary["energy_kwh"]["useful_additional_pv"] == pytest.approx(
        gurobi_run.summary["energy_kwh"]["useful_additional_pv"], abs=DOCUMENTED_TOLERANCE_KWH
    )
