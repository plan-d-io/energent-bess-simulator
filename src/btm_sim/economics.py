"""Shared battery-cost and simple-payback formulas for comparison and sweep."""

from __future__ import annotations

import math
from typing import Any

FINANCIAL_BASELINE = "fixed_tariff_no_battery"
ESTIMATED_BATTERY_CAPEX_LABEL = "Estimated battery CAPEX"
ANNUALISED_REVENUE_INCREASE_LABEL = "Annualised Energent PV-revenue increase"
SIMPLE_PAYBACK_LABEL = "Simple payback period"
NO_PAYBACK_LABEL = "No payback under these assumptions"

SIMPLE_PAYBACK_EXPLANATION = (
    "Simple payback is estimated battery CAPEX divided by annualised Energent "
    "PV-revenue increase. It excludes financing, discounting, operating costs, "
    "degradation, replacement, tax, inflation, and future tariff changes."
)
PARTIAL_PERIOD_PAYBACK_WARNING = (
    "Revenue from this partial period was scaled to one year. Seasonal effects "
    "may make the payback estimate unrepresentative."
)


def estimated_capex_eur(usable_energy_kwh: float, cost_eur_per_kwh: float) -> float:
    energy = float(usable_energy_kwh)
    cost = float(cost_eur_per_kwh)
    if not math.isfinite(energy) or energy < 0:
        raise ValueError("usable battery capacity must be a finite number >= 0")
    if not math.isfinite(cost) or cost <= 0:
        raise ValueError("estimated_battery_cost_eur_per_kwh must be a finite number > 0")
    return energy * cost


def period_revenue_uplift_eur(candidate_revenue_eur: float, baseline_revenue_eur: float) -> float:
    return float(candidate_revenue_eur) - float(baseline_revenue_eur)


def annual_revenue_uplift_eur(period_uplift_eur: float, year_fraction: float) -> float:
    fraction = float(year_fraction)
    if not math.isfinite(fraction) or fraction <= 0:
        raise ValueError("selected_period_year_fraction must be positive to annualise revenue")
    return float(period_uplift_eur) / fraction


def simple_payback_years(capex_eur: float, annual_uplift_eur: float) -> float | None:
    if annual_uplift_eur <= 0:
        return None
    return float(capex_eur) / float(annual_uplift_eur)


def annualized_from_partial_period(year_fraction: float) -> bool:
    """True when the selected period is shorter than one local calendar year."""
    return float(year_fraction) < 1.0 - 1e-9


def payback_from_uplift(
    *,
    capex_eur: float,
    period_uplift_eur: float,
    year_fraction: float,
) -> dict[str, float | None | bool]:
    annual = annual_revenue_uplift_eur(period_uplift_eur, year_fraction)
    payback = simple_payback_years(capex_eur, annual)
    return {
        "period_revenue_uplift_eur": float(period_uplift_eur),
        "annual_revenue_uplift_eur": annual,
        "simple_payback_years": payback,
        "payback_applicable": payback is not None,
    }


def comparison_economics_payload(
    *,
    cost_eur_per_kwh: float,
    capex_eur: float,
    year_fraction: float,
    cost_source: str | None,
) -> dict[str, Any]:
    partial = annualized_from_partial_period(year_fraction)
    return {
        "estimated_battery_cost_eur_per_kwh": float(cost_eur_per_kwh),
        "estimated_battery_cost_source": cost_source,
        "estimated_battery_capex_eur": float(capex_eur),
        "selected_period_year_fraction": float(year_fraction),
        "annualised_from_partial_period": partial,
        "financial_baseline": FINANCIAL_BASELINE,
        "simple_payback_explanation": SIMPLE_PAYBACK_EXPLANATION,
        "partial_period_warning": PARTIAL_PERIOD_PAYBACK_WARNING if partial else None,
        "labels": {
            "estimated_battery_capex": ESTIMATED_BATTERY_CAPEX_LABEL,
            "annualised_energent_pv_revenue_increase": ANNUALISED_REVENUE_INCREASE_LABEL,
            "simple_payback_period": SIMPLE_PAYBACK_LABEL,
            "no_payback": NO_PAYBACK_LABEL,
        },
    }


def attach_comparison_payback(
    scenarios: dict[str, dict[str, Any]],
    *,
    usable_energy_kwh: float,
    cost_eur_per_kwh: float,
    year_fraction: float,
) -> dict[str, dict[str, Any]]:
    """Attach CAPEX, annualised uplift, and simple payback to comparison scenarios."""
    capex = estimated_capex_eur(usable_energy_kwh, cost_eur_per_kwh)
    attached: dict[str, dict[str, Any]] = {}
    for name, row in scenarios.items():
        updated = dict(row)
        if name == "no_battery":
            updated["period_revenue_uplift_eur"] = 0.0
            updated["annual_revenue_uplift_eur"] = 0.0
            updated["simple_payback_years"] = None
            updated["payback_applicable"] = False
            updated["estimated_battery_capex_eur"] = None
        else:
            period_uplift = float((row.get("revenue") or {}).get("revenue_change_eur") or 0.0)
            updated.update(
                payback_from_uplift(
                    capex_eur=capex,
                    period_uplift_eur=period_uplift,
                    year_fraction=year_fraction,
                )
            )
            updated["estimated_battery_capex_eur"] = capex
        attached[name] = updated
    return attached


def economics_cost_source(audit: dict[str, Any] | None) -> str | None:
    if not audit:
        return None
    sources = audit.get("value_sources") or {}
    economics = sources.get("economics") or {}
    if "estimated_battery_cost_eur_per_kwh" in economics:
        return economics["estimated_battery_cost_eur_per_kwh"]
    sweep = sources.get("sweep") or {}
    return sweep.get("estimated_battery_cost_eur_per_kwh")
