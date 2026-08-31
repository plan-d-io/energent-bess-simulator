"""Revenue sweep runner: one baseline, one solve per candidate, cycle flags."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from btm_sim.config.schema import SweepConfig, TariffConfig
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.revenue import optimize_revenue
from btm_sim.sweep.candidates import SweepCandidate
from btm_sim.sweep.exceptions import SweepExecutionError
from btm_sim.sweep.runner import candidate_battery, run_revenue_sweep
from tests.lp_frames import HIGH_CYCLE_LIMIT, battery_cfg, qh_frame

UTC = timezone.utc


def _frame():
    return qh_frame(
        [
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 1.5, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
        ],
        start=datetime(2024, 6, 1, 10, 0, tzinfo=UTC),
    )


def _candidates():
    return [
        SweepCandidate("c001_5kW_10kWh", 5.0, 10.0, 2.0, False, True, "explicit"),
        SweepCandidate("c002_10kW_20kWh", 10.0, 20.0, 2.0, True, True, "explicit"),
    ]


def test_runner_calls_revenue_once_per_candidate_and_baseline_once(monkeypatch):
    calls: list[tuple[float, float]] = []
    original = optimize_revenue

    def spy(frame, config, tariffs=None, **kwargs):
        calls.append((config.e_usable_kwh, config.p_charge_kw))
        return original(frame, config, tariffs, **kwargs)

    monkeypatch.setattr("btm_sim.sweep.runner.optimize_revenue", spy)
    run = run_revenue_sweep(
        _frame(),
        _candidates(),
        battery_cfg(10.0, 5.0, 5.0, 0.95, 0.95),
        TariffConfig(),
        SweepConfig(),
    )
    assert len(calls) == 2
    assert calls[0] == (10.0, 5.0)
    assert calls[1] == (20.0, 10.0)
    assert run.baseline["total_energent_pv_revenue_eur"] is not None
    assert len(run.rows) == 2
    assert run.rows[0]["continuous_lp"] is True
    assert run.rows[0]["solver_num_int_vars"] == 0
    assert run.rows[0]["solver_num_bin_vars"] == 0
    assert "annual_peak_reduction_kw" in run.rows[0]
    assert run.rows[0]["annual_peak_reduction_kw"] == pytest.approx(
        run.rows[0]["baseline_annual_peak_kw"] - run.rows[0]["annual_peak_kw"]
    )
    assert run.rows[0]["average_monthly_peak_n_complete_months"] == 0
    assert run.rows[0]["average_monthly_peak_kw"] is None
    assert run.baseline["average_monthly_peak_n_complete_months"] == 0
    assert run.recommendation["peak_summary"]["dispatch_strategy"] == "revenue_maximisation"
    assert run.recommendation["peak_summary"]["financial_value_modelled"] is False
    assert run.year_fraction < 1.0
    assert run.annualized_from_partial_period is True
    assert run.explanations["partial_period_warning"]


def test_runner_uses_year_fraction_one_when_patched_to_full_or_multi_year(monkeypatch):
    monkeypatch.setattr("btm_sim.sweep.runner.selected_period_year_fraction", lambda frame: 1.0)
    full = run_revenue_sweep(
        _frame(),
        _candidates()[:1],
        battery_cfg(10.0, 5.0, 5.0, 0.95, 0.95),
        TariffConfig(),
        SweepConfig(),
    )
    assert full.year_fraction == 1.0
    assert full.annualized_from_partial_period is False
    monkeypatch.setattr("btm_sim.sweep.runner.selected_period_year_fraction", lambda frame: 2.0)
    multi = run_revenue_sweep(
        _frame(),
        _candidates()[:1],
        battery_cfg(10.0, 5.0, 5.0, 0.95, 0.95),
        TariffConfig(),
        SweepConfig(),
    )
    assert multi.year_fraction == 2.0
    assert multi.annualized_from_partial_period is False
    assert multi.rows[0]["annual_revenue_uplift_eur"] == pytest.approx(
        multi.rows[0]["period_revenue_uplift_eur"] / 2.0
    )


def test_runner_stops_on_first_candidate_failure(monkeypatch):
    original = optimize_revenue

    def flaky(frame, config, tariffs=None, **kwargs):
        if config.e_usable_kwh > 10:
            raise OptimizerError("boom", status="FAILED")
        return original(frame, config, tariffs, **kwargs)

    monkeypatch.setattr("btm_sim.sweep.runner.optimize_revenue", flaky)
    with pytest.raises(SweepExecutionError, match="c002_10kW_20kWh"):
        run_revenue_sweep(
            _frame(),
            _candidates(),
            battery_cfg(10.0, 5.0, 5.0, 0.95, 0.95),
            TariffConfig(),
            SweepConfig(),
        )


def test_low_cycle_limit_can_bind_in_sweep():
    template = battery_cfg(
        10.0,
        5.0,
        5.0,
        0.95,
        0.95,
        max_equivalent_full_cycles_per_year=0.01,
    )
    candidate = SweepCandidate("c001_5kW_10kWh", 5.0, 10.0, 2.0, False, False, "explicit")
    run = run_revenue_sweep(_frame(), [candidate], template, TariffConfig(), SweepConfig())
    assert run.rows[0]["cycle_limit_binding"] is True
    assert run.rows[0]["equivalent_full_cycles"] <= run.rows[0]["allowed_equivalent_full_cycles"] + 1e-6


def test_candidate_battery_inherits_efficiencies_and_cycle_limit_not_starting_size():
    template = battery_cfg(100.0, 50.0, 50.0, 0.9, 0.8, max_equivalent_full_cycles_per_year=HIGH_CYCLE_LIMIT)
    candidate = SweepCandidate("x", 12.0, 24.0, 2.0, False, False, "explicit")
    built = candidate_battery(template, candidate)
    assert built.e_usable_kwh == 24.0
    assert built.p_charge_kw == 12.0
    assert built.p_discharge_kw == 12.0
    assert built.eta_charge == 0.9
    assert built.eta_discharge == 0.8
    assert built.soc_initial_kwh == 0.0
    assert built.max_equivalent_full_cycles_per_year == HIGH_CYCLE_LIMIT
