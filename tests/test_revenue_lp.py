"""Hand-computable tests for fixed-tariff Revenue maximisation with battery export."""

from datetime import datetime, timezone

import numpy as np
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.physics import interval_energy_balance_residual
from btm_sim.config.schema import TariffConfig, parse_hhmm
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH
from btm_sim.optimizer.constants import LEXICO_TOL_EUR
from btm_sim.optimizer.revenue import optimize_revenue
from btm_sim.optimizer.self_consumption import optimize_self_consumption
from btm_sim.settlement.ledger import settle_dispatch
from tests.lp_frames import qh_frame

UTC = timezone.utc
UNCONSTRAINED = 1_000_000.0


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


def _peak_offpeak_tariffs(*, customer: float = 130.0, peak: float = 60.0, offpeak: float = 30.0) -> TariffConfig:
    return TariffConfig(
        customer_sale_eur_per_mwh=customer,
        peak_export_eur_per_mwh=peak,
        offpeak_export_eur_per_mwh=offpeak,
        peak_start_local=parse_hhmm("08:00", name="peak_start"),
        peak_end_local=parse_hhmm("20:00", name="peak_end"),
        weekends_offpeak=False,
    )


def test_revenue_model_remains_continuous():
    frame = qh_frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}], start=datetime(2024, 1, 3, 7, 0, tzinfo=UTC))
    result = optimize_revenue(frame, _cfg(e_usable_kwh=5.0, p_charge_kw=8.0, p_discharge_kw=8.0))
    assert result.summary["solver"]["num_int_vars"] == 0
    assert result.summary["solver"]["num_bin_vars"] == 0
    assert result.summary["solver"]["continuous_lp"] is True


def test_zero_battery_reproduces_no_battery_revenue():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    zero = optimize_revenue(frame, BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0))
    assert zero.frame["charge_pv_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert zero.frame["discharge_load_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert zero.frame["discharge_grid_kwh"].tolist() == pytest.approx([0.0, 0.0])
    baseline = settle_dispatch(frame, TariffConfig())
    assert zero.summary["revenue"]["total_energent_pv_revenue_eur"] == pytest.approx(
        baseline.totals["total_energent_pv_revenue_eur"]
    )


def test_preserves_self_consumption_customer_discharge_interval_by_interval():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    cfg = _cfg()
    sc = optimize_self_consumption(frame, cfg)
    revenue = optimize_revenue(frame, cfg, customer_first=sc)
    gap = np.max(np.abs(
        revenue.frame["discharge_load_kwh"].to_numpy() - sc.frame["discharge_load_kwh"].to_numpy()
    ))
    assert gap <= DOCUMENTED_TOLERANCE_KWH


def test_customer_supply_beats_higher_export_tariff():
    tariffs = _peak_offpeak_tariffs(customer=10.0, peak=200.0, offpeak=200.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    cfg = _cfg()
    sc = optimize_self_consumption(frame, cfg)
    revenue = optimize_revenue(frame, cfg, tariffs)
    assert sc.frame["discharge_load_kwh"].iloc[1] == pytest.approx(1.0, abs=1e-6)
    assert revenue.frame["discharge_load_kwh"].iloc[1] == pytest.approx(1.0, abs=1e-6)
    assert revenue.frame["discharge_load_kwh"].sum() == pytest.approx(
        sc.frame["discharge_load_kwh"].sum(), abs=1e-6
    )


def test_original_import_interval_can_finish_as_export():
    tariffs = _peak_offpeak_tariffs(peak=80.0, offpeak=20.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},  # 07:45 off-peak surplus
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},  # 08:00 originally importing
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue(frame, _cfg(), tariffs)
    row = result.frame.iloc[1]
    assert row["discharge_load_kwh"] == pytest.approx(2.0, abs=1e-6)
    assert row["grid_import_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert row["discharge_grid_kwh"] == pytest.approx(2.0, abs=1e-6)
    assert row["grid_export_kwh"] == pytest.approx(2.0, abs=1e-6)
    overlapping = result.frame.loc[
        (result.frame["discharge_grid_kwh"] > DOCUMENTED_TOLERANCE_KWH)
        & (result.frame["grid_import_kwh"] > DOCUMENTED_TOLERANCE_KWH)
    ]
    assert overlapping.empty


def test_profitable_offpeak_to_peak_export_shift():
    tariffs = _peak_offpeak_tariffs(peak=80.0, offpeak=20.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},  # off-peak surplus
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},  # peak, no customer import
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue(frame, _cfg(), tariffs)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(2.0, abs=1e-6)
    assert result.frame["discharge_grid_kwh"].iloc[1] == pytest.approx(2.0, abs=1e-6)
    baseline = settle_dispatch(frame, tariffs).totals["total_energent_pv_revenue_eur"]
    assert result.summary["revenue"]["total_energent_pv_revenue_eur"] == pytest.approx(baseline + 0.12, abs=1e-6)


def test_uneconomic_export_shift_is_not_performed():
    tariffs = _peak_offpeak_tariffs(peak=20.0, offpeak=20.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue(frame, _cfg(eta_charge=0.9, eta_discharge=0.9), tariffs)
    assert result.frame["discharge_grid_kwh"].sum() == pytest.approx(0.0, abs=1e-6)
    assert result.frame["charge_pv_kwh"].sum() == pytest.approx(0.0, abs=1e-6)


def test_export_extension_never_below_no_grid_export_schedule():
    tariffs = _peak_offpeak_tariffs()
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    cfg = _cfg()
    sc = optimize_self_consumption(frame, cfg)
    no_export = settle_dispatch(sc.frame.assign(discharge_grid_kwh=0.0), tariffs)
    result = optimize_revenue(frame, cfg, tariffs, customer_first=sc)
    assert result.summary["revenue"]["total_energent_pv_revenue_eur"] + LEXICO_TOL_EUR >= (
        no_export.totals["total_energent_pv_revenue_eur"]
    )


def test_physics_accounting_and_settlement_identities():
    tariffs = _peak_offpeak_tariffs(peak=80.0, offpeak=20.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    cfg = _cfg(eta_charge=0.95, eta_discharge=0.95)
    result = optimize_revenue(frame, cfg, tariffs)
    work = result.frame
    assert (work["charge_pv_kwh"] <= work["grid_export_baseline_kwh"] + 1e-9).all()
    assert work["soc_start_kwh"].iloc[0] == pytest.approx(0.0)
    assert work["soc_end_kwh"].iloc[-1] == pytest.approx(0.0)
    total_discharge = work["discharge_load_kwh"] + work["discharge_grid_kwh"]
    assert (total_discharge <= cfg.p_discharge_kw * work["interval_hours"] + 1e-9).all()
    assert result.summary["throughput"]["equivalent_full_cycles"] <= (
        cfg.max_equivalent_full_cycles_per_year + 1e-9
    )
    assert (work["grid_import_kwh"] <= work["grid_import_baseline_kwh"] + 1e-9).all()
    expected_discharge_loss = total_discharge.to_numpy() / cfg.eta_discharge - total_discharge.to_numpy()
    assert work["discharge_loss_kwh"].to_numpy() == pytest.approx(expected_discharge_loss, abs=1e-9)
    residual = interval_energy_balance_residual(work)
    assert float(np.max(np.abs(residual))) <= DOCUMENTED_TOLERANCE_KWH
    assert result.summary["reconciliation"]["loss_identity_residual_kwh"] == pytest.approx(0.0, abs=1e-9)
    revenue = result.summary["revenue"]
    assert revenue["revenue_change_eur"] == pytest.approx(
        revenue["extra_customer_sale_eur"]
        - revenue["foregone_export_eur"]
        + revenue["battery_grid_injection_revenue_eur"],
        abs=1e-9,
    )
    assert revenue["uplift_eur"] == pytest.approx(revenue["revenue_change_eur"], abs=1e-9)
    assert result.summary["solver"]["continuous_lp"] is True
    assert len(result.stages) == 2


def test_terminal_soc_equals_initial():
    frame = qh_frame(
        [{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue(frame, _cfg())
    assert result.frame["soc_start_kwh"].iloc[0] == pytest.approx(0.0)
    assert result.frame["soc_end_kwh"].iloc[-1] == pytest.approx(0.0)


def test_prefers_low_value_export_intervals_for_customer_charging():
    tariffs = _peak_offpeak_tariffs(peak=200.0, offpeak=30.0)
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    result = optimize_revenue(frame, _cfg(e_usable_kwh=1.0, p_charge_kw=8.0, p_discharge_kw=8.0), tariffs)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(1.0, abs=1e-5)
    assert result.frame["charge_pv_kwh"].iloc[1] == pytest.approx(0.0, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[2] == pytest.approx(1.0, abs=1e-5)
