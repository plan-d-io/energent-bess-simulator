"""HiGHS dynamic injection and same-frozen-schedule differentials."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import calendar_year_physical_hours
from btm_sim.config.schema import TariffConfig
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH
from btm_sim.optimizer.constants import LEXICO_TOL_EUR
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.highs_dynamic_injection import optimize_dynamic_injection_highs
from btm_sim.optimizer.highs_self_consumption import optimize_self_consumption_highs
from tests.lp_frames import qh_frame

pytest.importorskip("highspy")

UNCONSTRAINED = 1_000_000.0
STAGE_EUR_ATOL = 1e-6
STAGE_ENERGY_ATOL = 1e-6


def _cfg(**overrides) -> BatteryConfig:
    payload = dict(
        e_usable_kwh=10.0,
        p_charge_kw=100.0,
        p_discharge_kw=100.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_initial_kwh=0.0,
        max_equivalent_full_cycles_per_year=UNCONSTRAINED,
    )
    payload.update(overrides)
    return BatteryConfig(**payload)


def _spread_frame() -> pd.DataFrame:
    return qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ]
    )


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


def test_highs_dynamic_high_price_shift_preserves_customer():
    frame = _spread_frame()
    prices = np.array([10.0, 10.0, 10.0, 200.0])
    cfg = _cfg()
    sc = optimize_self_consumption_highs(frame, cfg)
    result = optimize_dynamic_injection_highs(frame, cfg, prices, customer_first=sc)
    assert result.ok
    np.testing.assert_allclose(
        result.frame["discharge_load_kwh"].to_numpy(),
        sc.frame["discharge_load_kwh"].to_numpy(),
        atol=DOCUMENTED_TOLERANCE_KWH,
    )
    assert float(result.frame["discharge_grid_kwh"].iloc[3]) == pytest.approx(2.0, abs=1e-6)
    import_when_exporting = result.frame.loc[result.frame["discharge_grid_kwh"] > 1e-9, "grid_import_kwh"]
    assert (import_when_exporting <= DOCUMENTED_TOLERANCE_KWH).all()


def test_highs_dynamic_negative_and_zero_prices():
    frame = _spread_frame()
    prices = np.array([-5.0, 0.0, 10.0, 50.0])
    result = optimize_dynamic_injection_highs(frame, _cfg(), prices)
    assert result.ok
    assert float(result.frame["discharge_grid_kwh"].sum()) >= 0.0
    assert result.stages[0]["tolerance"] == LEXICO_TOL_EUR


def test_highs_dynamic_cannot_cut_customer_for_high_price():
    frame = _spread_frame()
    prices = np.array([10.0, 10.0, 1000.0, 10.0])
    cfg = _cfg()
    sc = optimize_self_consumption_highs(frame, cfg)
    result = optimize_dynamic_injection_highs(frame, cfg, prices, customer_first=sc)
    assert float(result.frame.loc[2, "discharge_load_kwh"]) == pytest.approx(2.0)


def test_highs_dynamic_price_length_and_nonfinite_fail():
    frame = _spread_frame()
    cfg = _cfg()
    with pytest.raises(OptimizerError, match="aligned"):
        optimize_dynamic_injection_highs(frame, cfg, np.array([1.0, 2.0]))
    with pytest.raises(OptimizerError, match="finite"):
        optimize_dynamic_injection_highs(frame, cfg, np.array([1.0, 2.0, np.nan, 4.0]))


def test_highs_dynamic_cycle_budget_shared():
    frame = _spread_frame().copy()
    frame["interval_hours"] = calendar_year_physical_hours(2024) / len(frame)
    cfg = _cfg(max_equivalent_full_cycles_per_year=0.1)
    prices = np.array([10.0, 10.0, 10.0, 500.0])
    sc = optimize_self_consumption_highs(frame, cfg)
    result = optimize_dynamic_injection_highs(frame, cfg, prices, customer_first=sc)
    assert result.summary["throughput"]["equivalent_full_cycles"] <= (
        result.summary["throughput"]["allowed_equivalent_full_cycles"] + 1e-9
    )
    np.testing.assert_allclose(
        result.frame["discharge_load_kwh"].to_numpy(),
        sc.frame["discharge_load_kwh"].to_numpy(),
        atol=DOCUMENTED_TOLERANCE_KWH,
    )


def test_highs_dynamic_settlement_against_fixed_no_battery_baseline():
    frame = _spread_frame()
    prices = np.array([10.0, 10.0, 10.0, 200.0])
    result = optimize_dynamic_injection_highs(frame, _cfg(), prices, tariffs=TariffConfig())
    revenue = result.summary["revenue"]
    assert revenue["financial_baseline"] == "fixed_tariff_no_battery"
    assert revenue["settlement_mode"] == "dynamic_injection"
    assert revenue["revenue_change_eur"] == pytest.approx(
        revenue["total_energent_pv_revenue_eur"] - revenue["baseline_total_energent_pv_revenue_eur"],
        abs=1e-9,
    )


@pytest.mark.skipif(not _gurobi_available(), reason="Gurobi package or licence is not available")
def test_cross_frozen_dynamic_export_same_schedule_parity():
    from btm_sim.optimizer.backend import get_optimizer_backend

    gurobi = get_optimizer_backend("gurobi")
    frame = _spread_frame()
    prices = np.array([10.0, 10.0, 10.0, 200.0])
    cfg = _cfg()
    sc_g = gurobi.optimize_self_consumption(frame, cfg)
    sc_h = optimize_self_consumption_highs(frame, cfg)
    combos = {
        "g_on_g": gurobi.optimize_dynamic_injection(frame, cfg, prices, customer_first=sc_g),
        "h_on_g": optimize_dynamic_injection_highs(frame, cfg, prices, customer_first=sc_g),
        "g_on_h": gurobi.optimize_dynamic_injection(frame, cfg, prices, customer_first=sc_h),
        "h_on_h": optimize_dynamic_injection_highs(frame, cfg, prices, customer_first=sc_h),
    }

    def _assert_same_frozen(a, b):
        for stage_a, stage_b in zip(a.stages, b.stages, strict=True):
            atol = STAGE_EUR_ATOL if stage_a["unit"] == "EUR" else STAGE_ENERGY_ATOL
            assert stage_a["optimum"] == pytest.approx(stage_b["optimum"], abs=atol)
        assert a.summary["energy_kwh"]["charge_pv"] == pytest.approx(
            b.summary["energy_kwh"]["charge_pv"], abs=DOCUMENTED_TOLERANCE_KWH
        )
        assert a.summary["energy_kwh"]["discharge_grid"] == pytest.approx(
            b.summary["energy_kwh"]["discharge_grid"], abs=DOCUMENTED_TOLERANCE_KWH
        )
        assert a.summary["revenue"]["dynamic_grid_injection_revenue_eur"] == pytest.approx(
            b.summary["revenue"]["dynamic_grid_injection_revenue_eur"], abs=STAGE_EUR_ATOL
        )
        assert a.summary["revenue"]["total_energent_pv_revenue_eur"] == pytest.approx(
            b.summary["revenue"]["total_energent_pv_revenue_eur"], abs=STAGE_EUR_ATOL
        )
        assert a.summary["feasibility"]["ok"] and b.summary["feasibility"]["ok"]

    _assert_same_frozen(combos["g_on_g"], combos["h_on_g"])
    _assert_same_frozen(combos["g_on_h"], combos["h_on_h"])
    _assert_same_frozen(combos["g_on_g"], combos["h_on_h"])
