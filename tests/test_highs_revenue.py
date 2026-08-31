"""HiGHS fixed-tariff revenue and same-frozen-schedule differentials."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import calendar_year_physical_hours, cycle_limit_report
from btm_sim.battery.physics import interval_energy_balance_residual
from btm_sim.config.schema import TariffConfig, parse_hhmm
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH
from btm_sim.optimizer.constants import LEXICO_TOL_EUR
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.highs_backend import dispose_highs_lp, optimize_highs_stage
from btm_sim.optimizer.highs_export import build_highs_export_lp
from btm_sim.optimizer.highs_revenue import optimize_revenue_highs
from btm_sim.optimizer.highs_self_consumption import optimize_self_consumption_highs
from btm_sim.settlement.ledger import settle_dispatch
from tests.lp_frames import qh_frame

highspy = pytest.importorskip("highspy")

UTC = timezone.utc
UNCONSTRAINED = 1_000_000.0
STAGE_EUR_ATOL = 1e-6
STAGE_ENERGY_ATOL = 1e-6
UNIQUE_INTERVAL_ATOL = 1e-5


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


def _tariffs(*, customer: float = 130.0, peak: float = 80.0, offpeak: float = 20.0) -> TariffConfig:
    return TariffConfig(
        customer_sale_eur_per_mwh=customer,
        peak_export_eur_per_mwh=peak,
        offpeak_export_eur_per_mwh=offpeak,
        peak_start_local=parse_hhmm("08:00", name="peak_start"),
        peak_end_local=parse_hhmm("20:00", name="peak_end"),
        weekends_offpeak=False,
    )


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


def test_highs_revenue_zero_capacity():
    frame = qh_frame(
        [{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    zero = optimize_revenue_highs(frame, BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0), _tariffs())
    assert zero.frame["discharge_grid_kwh"].tolist() == pytest.approx([0.0, 0.0])
    baseline = settle_dispatch(frame, _tariffs())
    assert zero.summary["revenue"]["total_energent_pv_revenue_eur"] == pytest.approx(
        baseline.totals["total_energent_pv_revenue_eur"]
    )


def test_highs_revenue_preserves_customer_and_exports_after_covering_import():
    tariffs = _tariffs()
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    sc = optimize_self_consumption_highs(frame, _cfg())
    result = optimize_revenue_highs(frame, _cfg(), tariffs, customer_first=sc)
    np.testing.assert_allclose(
        result.frame["discharge_load_kwh"].to_numpy(),
        sc.frame["discharge_load_kwh"].to_numpy(),
        atol=DOCUMENTED_TOLERANCE_KWH,
    )
    row = result.frame.iloc[1]
    assert row["discharge_load_kwh"] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)
    assert row["grid_import_kwh"] == pytest.approx(0.0, abs=UNIQUE_INTERVAL_ATOL)
    assert row["discharge_grid_kwh"] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)
    overlapping = result.frame.loc[
        (result.frame["discharge_grid_kwh"] > DOCUMENTED_TOLERANCE_KWH)
        & (result.frame["grid_import_kwh"] > DOCUMENTED_TOLERANCE_KWH)
    ]
    assert overlapping.empty


def test_highs_revenue_peak_offpeak_shift():
    tariffs = _tariffs(peak=80.0, offpeak=20.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue_highs(frame, _cfg(), tariffs)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)
    assert result.frame["discharge_grid_kwh"].iloc[1] == pytest.approx(2.0, abs=UNIQUE_INTERVAL_ATOL)
    baseline = settle_dispatch(frame, tariffs).totals["total_energent_pv_revenue_eur"]
    assert result.summary["revenue"]["total_energent_pv_revenue_eur"] == pytest.approx(
        baseline + 0.12, abs=STAGE_EUR_ATOL
    )


def test_highs_revenue_binding_cycle_shared():
    tariffs = _tariffs(peak=200.0, offpeak=10.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    ).copy()
    frame["interval_hours"] = calendar_year_physical_hours(2024) / len(frame)
    cfg = _cfg(max_equivalent_full_cycles_per_year=0.1)
    sc = optimize_self_consumption_highs(frame, cfg)
    result = optimize_revenue_highs(frame, cfg, tariffs, customer_first=sc)
    report = cycle_limit_report(result.frame, cfg)
    assert report["equivalent_full_cycles"] <= report["allowed_equivalent_full_cycles"] + 1e-9
    assert float(result.frame["discharge_load_kwh"].sum()) == pytest.approx(
        float(sc.frame["discharge_load_kwh"].sum()), abs=DOCUMENTED_TOLERANCE_KWH
    )


def test_highs_revenue_settlement_and_keep_row():
    tariffs = _tariffs()
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue_highs(frame, _cfg(eta_charge=0.95, eta_discharge=0.95), tariffs)
    assert len(result.stages) == 2
    assert result.stages[0]["tolerance"] == LEXICO_TOL_EUR
    residual = interval_energy_balance_residual(result.frame)
    assert float(np.max(np.abs(residual))) <= DOCUMENTED_TOLERANCE_KWH
    revenue = result.summary["revenue"]
    assert revenue["revenue_change_eur"] == pytest.approx(
        revenue["extra_customer_sale_eur"]
        - revenue["foregone_export_eur"]
        + revenue["battery_grid_injection_revenue_eur"],
        abs=1e-9,
    )
    assert "no_export_probe" in result.summary["solver"]


def test_highs_revenue_infeasible_status():
    frame = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}], start=datetime(2024, 1, 3, 8, 0, tzinfo=UTC))
    sc = optimize_self_consumption_highs(frame, _cfg(e_usable_kwh=1.0))
    work = sc.frame
    lp = build_highs_export_lp(
        work,
        _cfg(e_usable_kwh=1.0),
        np.array([50.0]),
        allow_grid_export=True,
    )
    try:
        lp.highs.addRow(
            10.0,
            float(highspy.kHighsInf),
            1,
            np.array([lp.idx_discharge_grid], dtype=np.int32),
            np.array([1.0], dtype=np.float64),
        )
        with pytest.raises(OptimizerError, match="did not return an optimal"):
            optimize_highs_stage(lp, stage="impossible")
    finally:
        dispose_highs_lp(lp)


@pytest.mark.skipif(not _gurobi_available(), reason="Gurobi package or licence is not available")
def test_cross_frozen_fixed_export_same_schedule_parity():
    from btm_sim.optimizer.backend import get_optimizer_backend

    gurobi = get_optimizer_backend("gurobi")
    tariffs = _tariffs()
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    cfg = _cfg()
    sc_g = gurobi.optimize_self_consumption(frame, cfg)
    sc_h = optimize_self_consumption_highs(frame, cfg)

    combos = [
        ("g_on_g", gurobi.optimize_revenue(frame, cfg, tariffs, customer_first=sc_g)),
        ("h_on_g", optimize_revenue_highs(frame, cfg, tariffs, customer_first=sc_g)),
        ("g_on_h", gurobi.optimize_revenue(frame, cfg, tariffs, customer_first=sc_h)),
        ("h_on_h", optimize_revenue_highs(frame, cfg, tariffs, customer_first=sc_h)),
    ]
    by_name = dict(combos)

    def _assert_same_frozen(a, b):
        for stage_a, stage_b in zip(a.stages, b.stages, strict=True):
            assert stage_a["stage"] == stage_b["stage"]
            assert stage_a["optimum"] == pytest.approx(stage_b["optimum"], abs=STAGE_EUR_ATOL if stage_a["unit"] == "EUR" else STAGE_ENERGY_ATOL)
        assert a.summary["energy_kwh"]["charge_pv"] == pytest.approx(
            b.summary["energy_kwh"]["charge_pv"], abs=DOCUMENTED_TOLERANCE_KWH
        )
        assert a.summary["energy_kwh"]["discharge_grid"] == pytest.approx(
            b.summary["energy_kwh"]["discharge_grid"], abs=DOCUMENTED_TOLERANCE_KWH
        )
        assert a.summary["energy_kwh"]["grid_import"] == pytest.approx(
            b.summary["energy_kwh"]["grid_import"], abs=DOCUMENTED_TOLERANCE_KWH
        )
        assert a.summary["energy_kwh"]["grid_export"] == pytest.approx(
            b.summary["energy_kwh"]["grid_export"], abs=DOCUMENTED_TOLERANCE_KWH
        )
        assert a.summary["revenue"]["battery_grid_injection_revenue_eur"] == pytest.approx(
            b.summary["revenue"]["battery_grid_injection_revenue_eur"], abs=STAGE_EUR_ATOL
        )
        assert a.summary["revenue"]["total_energent_pv_revenue_eur"] == pytest.approx(
            b.summary["revenue"]["total_energent_pv_revenue_eur"], abs=STAGE_EUR_ATOL
        )
        assert a.summary["feasibility"]["ok"] is True
        assert b.summary["feasibility"]["ok"] is True

    _assert_same_frozen(by_name["g_on_g"], by_name["h_on_g"])
    _assert_same_frozen(by_name["g_on_h"], by_name["h_on_h"])
    # Unique small fixture: native end-to-end should also agree.
    _assert_same_frozen(by_name["g_on_g"], by_name["h_on_h"])
