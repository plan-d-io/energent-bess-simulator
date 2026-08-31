"""Shared CAPEX, annualisation, and simple-payback formulas."""

from __future__ import annotations

import pytest

from btm_sim.economics import (
    PARTIAL_PERIOD_PAYBACK_WARNING,
    SIMPLE_PAYBACK_EXPLANATION,
    annual_revenue_uplift_eur,
    annualized_from_partial_period,
    attach_comparison_payback,
    estimated_capex_eur,
    payback_from_uplift,
    period_revenue_uplift_eur,
    simple_payback_years,
)
from btm_sim.sweep.economics import (
    annual_revenue_uplift_eur as sweep_annual_revenue_uplift_eur,
    estimated_capex_eur as sweep_estimated_capex_eur,
    period_revenue_uplift_eur as sweep_period_revenue_uplift_eur,
    simple_payback_years as sweep_simple_payback_years,
)


def test_complete_calendar_year_does_not_scale():
    assert estimated_capex_eur(100.0, 300.0) == 30000.0
    assert period_revenue_uplift_eur(1300.0, 1000.0) == 300.0
    assert annual_revenue_uplift_eur(300.0, 1.0) == 300.0
    assert simple_payback_years(30000.0, 300.0) == 100.0
    assert annualized_from_partial_period(1.0) is False


def test_partial_period_is_annualised():
    payload = payback_from_uplift(capex_eur=30000.0, period_uplift_eur=300.0, year_fraction=0.5)
    assert payload["period_revenue_uplift_eur"] == 300.0
    assert payload["annual_revenue_uplift_eur"] == 600.0
    assert payload["simple_payback_years"] == 50.0
    assert payload["payback_applicable"] is True
    assert annualized_from_partial_period(0.5) is True
    assert "scaled to one year" in PARTIAL_PERIOD_PAYBACK_WARNING
    assert "excludes financing" in SIMPLE_PAYBACK_EXPLANATION


def test_multi_year_period_is_annualised():
    assert annual_revenue_uplift_eur(500.0, 2.0) == 250.0
    assert annualized_from_partial_period(2.0) is False
    assert simple_payback_years(30000.0, 250.0) == 120.0


def test_zero_and_negative_uplift_have_no_payback():
    assert simple_payback_years(30000.0, 0.0) is None
    assert simple_payback_years(30000.0, -10.0) is None
    zero = payback_from_uplift(capex_eur=30000.0, period_uplift_eur=0.0, year_fraction=1.0)
    assert zero["simple_payback_years"] is None
    assert zero["payback_applicable"] is False
    negative = payback_from_uplift(capex_eur=30000.0, period_uplift_eur=-100.0, year_fraction=1.0)
    assert negative["annual_revenue_uplift_eur"] == -100.0
    assert negative["simple_payback_years"] is None


def test_invalid_cost_or_year_fraction_is_rejected():
    with pytest.raises(ValueError, match="estimated_battery_cost"):
        estimated_capex_eur(100.0, 0.0)
    with pytest.raises(ValueError, match="estimated_battery_cost"):
        estimated_capex_eur(100.0, float("nan"))
    with pytest.raises(ValueError, match="selected_period_year_fraction"):
        annual_revenue_uplift_eur(100.0, 0.0)
    with pytest.raises(ValueError, match="selected_period_year_fraction"):
        annual_revenue_uplift_eur(100.0, -0.5)


def test_sweep_and_comparison_formulas_agree():
    args = (80.0, 275.0)
    assert estimated_capex_eur(*args) == sweep_estimated_capex_eur(*args)
    assert period_revenue_uplift_eur(1200.0, 900.0) == sweep_period_revenue_uplift_eur(1200.0, 900.0)
    assert annual_revenue_uplift_eur(150.0, 0.25) == sweep_annual_revenue_uplift_eur(150.0, 0.25)
    assert simple_payback_years(22000.0, 1100.0) == sweep_simple_payback_years(22000.0, 1100.0)
    assert simple_payback_years(22000.0, -1.0) == sweep_simple_payback_years(22000.0, -1.0)


def test_no_battery_payback_is_null_and_battery_cases_share_capex():
    scenarios = {
        "no_battery": {"revenue": {"revenue_change_eur": 0.0}},
        "reference": {"revenue": {"revenue_change_eur": 1000.0}},
        "dynamic_injection": {"revenue": {"revenue_change_eur": -50.0}},
    }
    attached = attach_comparison_payback(
        scenarios,
        usable_energy_kwh=100.0,
        cost_eur_per_kwh=300.0,
        year_fraction=1.0,
    )
    assert attached["no_battery"]["estimated_battery_capex_eur"] is None
    assert attached["no_battery"]["simple_payback_years"] is None
    assert attached["no_battery"]["payback_applicable"] is False
    assert attached["reference"]["estimated_battery_capex_eur"] == 30000.0
    assert attached["dynamic_injection"]["estimated_battery_capex_eur"] == 30000.0
    assert attached["reference"]["simple_payback_years"] == 30.0
    assert attached["dynamic_injection"]["simple_payback_years"] is None
    assert attached["dynamic_injection"]["payback_applicable"] is False
