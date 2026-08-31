"""Simplified sweep economics and the suggested-size recommendation."""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from btm_sim.economics import (
    annual_revenue_uplift_eur as _annual_revenue_uplift_eur,
    annualized_from_partial_period,
    estimated_capex_eur,
    period_revenue_uplift_eur,
    simple_payback_years,
)
from btm_sim.sweep.exceptions import SweepRequestError
from btm_sim.sweep.peaks import (
    PEAK_METRIC_SNAPSHOT_KEYS,
    build_peak_summary,
    candidate_peak_snapshot,
    select_largest_average_monthly_peak_reduction,
)

CAPEX_EXPLANATION = (
    "Battery cost is estimated from usable capacity and the configured EUR/kWh "
    "value. The estimate excludes financing, discounting, degradation, operating "
    "costs, inflation, and future tariff changes."
)
VALUE_EXPLANATION = (
    "Estimated value is the annualised Energent revenue increase multiplied by "
    "the evaluation period, minus estimated battery cost. It is not profit, NPV, "
    "or a complete business case."
)
PARTIAL_PERIOD_WARNING = (
    "This period is not a complete calendar year. Its revenue was scaled to one "
    "year for the sizing estimate. Seasonal differences may materially change the "
    "result."
)
SUGGESTED_SIZE_LABEL = "Suggested battery size"
HIGHEST_VALUE_LABEL = "Highest estimated value among the tested sizes"
NO_BATTERY_LABEL = "No battery"
RANGE_BOUNDARY_NOTE = (
    "The highest-value or highest-revenue size for this duration is the largest "
    "tested power. A larger range may be worth testing."
)
SCREENING_PERIOD_LABEL = "Screening period"
SHORTEST_PAYBACK_LABEL = "Shortest simple payback among the tested sizes"
HIGHEST_ANNUAL_REVENUE_LABEL = "Highest annual revenue increase among the tested sizes"
NO_PAYBACK_WITHIN_SCREENING_LABEL = (
    "No tested battery pays back within the configured screening period"
)
NO_POSITIVE_ANNUAL_REVENUE_LABEL = (
    "No tested battery has a positive annual revenue increase under these assumptions"
)
OUTCOME_WITHIN_SCREENING_PERIOD = "one_or_more_candidates_within_screening_period"
OUTCOME_NO_CANDIDATE_WITHIN_SCREENING_PERIOD = "no_candidate_within_screening_period"
OUTCOME_NO_POSITIVE_ANNUAL_REVENUE = "no_candidate_with_positive_annual_revenue"
SCREENING_SNAPSHOT_KEYS = (
    "candidate_id",
    "power_kw",
    "usable_energy_kwh",
    "duration_hours",
    "estimated_capex_eur",
    "annual_revenue_uplift_eur",
    "simple_payback_years",
    "equivalent_full_cycles",
    "cycle_limit_binding",
    "payback_within_evaluation_period",
    *PEAK_METRIC_SNAPSHOT_KEYS,
)

FORMULAS = {
    "estimated_capex_eur": "usable_energy_kwh × estimated_battery_cost_eur_per_kwh",
    "period_revenue_uplift_eur": (
        "candidate Energent PV revenue − no-battery Energent PV revenue"
    ),
    "annual_revenue_uplift_eur": "period_revenue_uplift_eur ÷ selected_period_year_fraction",
    "simple_payback_years": "estimated_capex_eur ÷ annual_revenue_uplift_eur (null if uplift ≤ 0)",
    "estimated_value_eur": (
        "annual_revenue_uplift_eur × evaluation_period_years − estimated_capex_eur"
    ),
}


def estimated_value_eur(annual_uplift_eur: float, evaluation_period_years: float, capex_eur: float) -> float:
    return float(annual_uplift_eur) * float(evaluation_period_years) - float(capex_eur)


def annual_revenue_uplift_eur(period_uplift_eur: float, year_fraction: float) -> float:
    try:
        return _annual_revenue_uplift_eur(period_uplift_eur, year_fraction)
    except ValueError as exc:
        raise SweepRequestError(
            "selected_period_year_fraction must be positive to annualise sweep revenue",
            category="invalid_period",
        ) from exc


def attach_economics(
    *,
    usable_energy_kwh: float,
    candidate_revenue_eur: float,
    baseline_revenue_eur: float,
    year_fraction: float,
    cost_eur_per_kwh: float,
    evaluation_period_years: float,
) -> dict[str, float | None]:
    capex = estimated_capex_eur(usable_energy_kwh, cost_eur_per_kwh)
    period_uplift = period_revenue_uplift_eur(candidate_revenue_eur, baseline_revenue_eur)
    annual_uplift = annual_revenue_uplift_eur(period_uplift, year_fraction)
    payback = simple_payback_years(capex, annual_uplift)
    return {
        "estimated_capex_eur": capex,
        "period_revenue_uplift_eur": period_uplift,
        "annual_revenue_uplift_eur": annual_uplift,
        "simple_payback_years": payback,
        "estimated_value_eur": estimated_value_eur(annual_uplift, evaluation_period_years, capex),
        "payback_within_evaluation_period": payback_within_evaluation_period(
            payback, evaluation_period_years
        ),
    }


def payback_within_evaluation_period(
    simple_payback_years: Any,
    evaluation_period_years: float,
) -> bool:
    """True when unrounded simple payback is finite and at most the screening period."""
    payback = _finite_number(simple_payback_years)
    if payback is None or payback < 0:
        return False
    return payback <= float(evaluation_period_years)


def recommend(
    rows: Sequence[dict[str, Any]],
    *,
    revenue_capture_threshold_pct: float,
    evaluation_period_years: float = 10.0,
) -> dict[str, Any]:
    """Apply the documented screening recommendation rules."""
    if not rows:
        raise SweepRequestError("Cannot recommend a size from an empty candidate list")
    ranked = sorted(rows, key=_recommendation_sort_key)
    best = ranked[0]
    best_value = float(best["estimated_value_eur"])
    if best_value > 0:
        recommendation = {
            "recommendation_kind": "tested_candidate",
            "label": SUGGESTED_SIZE_LABEL,
            "explanation": HIGHEST_VALUE_LABEL,
            "candidate_id": best["candidate_id"],
            "power_kw": best["power_kw"],
            "usable_energy_kwh": best["usable_energy_kwh"],
            "duration_hours": best["duration_hours"],
            "estimated_capex_eur": best["estimated_capex_eur"],
            "annual_revenue_uplift_eur": best["annual_revenue_uplift_eur"],
            "estimated_value_eur": best["estimated_value_eur"],
            "simple_payback_years": best["simple_payback_years"],
        }
    else:
        recommendation = {
            "recommendation_kind": "no_battery",
            "label": NO_BATTERY_LABEL,
            "explanation": (
                "No tested battery size has a positive estimated value under the "
                "configured assumptions. No battery is the suggested result."
            ),
            "candidate_id": None,
            "power_kw": None,
            "usable_energy_kwh": None,
            "duration_hours": None,
            "estimated_capex_eur": 0.0,
            "annual_revenue_uplift_eur": 0.0,
            "estimated_value_eur": 0.0,
            "simple_payback_years": None,
        }
    per_duration = _per_duration_results(
        rows,
        revenue_capture_threshold_pct,
        evaluation_period_years=evaluation_period_years,
    )
    return {
        "recommendation": recommendation,
        "best_per_duration": per_duration,
        "no_battery_estimated_value_eur": 0.0,
        "screening_summary": build_screening_summary(
            rows, evaluation_period_years=evaluation_period_years
        ),
        "peak_summary": build_peak_summary(rows),
    }


def build_screening_summary(
    rows: Sequence[dict[str, Any]],
    *,
    evaluation_period_years: float,
) -> dict[str, Any]:
    """Payback-focused screening summary over already-computed candidate rows."""
    positive_count = sum(1 for row in rows if _positive_annual_revenue(row))
    within_count = sum(
        1 for row in rows if candidate_within_screening_period(row, evaluation_period_years)
    )
    if positive_count == 0:
        outcome = OUTCOME_NO_POSITIVE_ANNUAL_REVENUE
        outcome_label = NO_POSITIVE_ANNUAL_REVENUE_LABEL
    elif within_count > 0:
        outcome = OUTCOME_WITHIN_SCREENING_PERIOD
        outcome_label = None
    else:
        outcome = OUTCOME_NO_CANDIDATE_WITHIN_SCREENING_PERIOD
        outcome_label = NO_PAYBACK_WITHIN_SCREENING_LABEL
    shortest = select_shortest_payback_candidate(rows)
    highest = select_highest_annual_revenue_candidate(rows)
    return {
        "screening_period_years": float(evaluation_period_years),
        "candidate_count": len(rows),
        "candidates_with_positive_annual_revenue_count": positive_count,
        "candidates_with_payback_within_screening_period_count": within_count,
        "screening_outcome": outcome,
        "screening_outcome_label": outcome_label,
        "shortest_payback_candidate": None
        if shortest is None
        else candidate_screening_snapshot(shortest, evaluation_period_years),
        "highest_annual_revenue_candidate": None
        if highest is None
        else candidate_screening_snapshot(highest, evaluation_period_years),
        "labels": {
            "screening_period": SCREENING_PERIOD_LABEL,
            "shortest_simple_payback": SHORTEST_PAYBACK_LABEL,
            "highest_annual_revenue_increase": HIGHEST_ANNUAL_REVENUE_LABEL,
            "no_payback_within_screening_period": NO_PAYBACK_WITHIN_SCREENING_LABEL,
            "no_positive_annual_revenue": NO_POSITIVE_ANNUAL_REVENUE_LABEL,
        },
    }


def candidate_screening_snapshot(
    row: dict[str, Any],
    evaluation_period_years: float,
) -> dict[str, Any]:
    payload = {key: row.get(key) for key in SCREENING_SNAPSHOT_KEYS}
    payload["payback_within_evaluation_period"] = candidate_within_screening_period(
        row, evaluation_period_years
    )
    return payload


def select_shortest_payback_candidate(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [
        row
        for row in rows
        if _positive_annual_revenue(row)
        and _positive_finite_payback(row.get("simple_payback_years")) is not None
    ]
    if not eligible:
        return None
    return min(eligible, key=_shortest_payback_sort_key)


def select_highest_annual_revenue_candidate(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return min(rows, key=_highest_annual_revenue_sort_key)


def _recommendation_sort_key(row: dict[str, Any]) -> tuple:
    return (
        -float(row["estimated_value_eur"]),
        float(row["estimated_capex_eur"]),
        float(row["usable_energy_kwh"]),
        float(row["power_kw"]),
        float(row["duration_hours"]),
    )


def _shortest_payback_sort_key(row: dict[str, Any]) -> tuple:
    payback = _positive_finite_payback(row.get("simple_payback_years"))
    return (
        math.inf if payback is None else payback,
        float(row["estimated_capex_eur"]),
        float(row["usable_energy_kwh"]),
        float(row["power_kw"]),
        float(row["duration_hours"]),
        str(row["candidate_id"]),
    )


def _highest_annual_revenue_sort_key(row: dict[str, Any]) -> tuple:
    return (
        -float(row["annual_revenue_uplift_eur"]),
        float(row["estimated_capex_eur"]),
        float(row["usable_energy_kwh"]),
        float(row["power_kw"]),
        float(row["duration_hours"]),
        str(row["candidate_id"]),
    )


def _finite_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _positive_finite_payback(value: Any) -> float | None:
    payback = _finite_number(value)
    if payback is None or payback <= 0:
        return None
    return payback


def _positive_annual_revenue(row: dict[str, Any]) -> bool:
    uplift = _finite_number(row.get("annual_revenue_uplift_eur"))
    return uplift is not None and uplift > 0


def candidate_within_screening_period(row: dict[str, Any], evaluation_period_years: float) -> bool:
    """True when the row has positive annual revenue and an applicable payback within the period."""
    if not _positive_annual_revenue(row):
        return False
    return payback_within_evaluation_period(row.get("simple_payback_years"), evaluation_period_years)


def _per_duration_results(
    rows: Sequence[dict[str, Any]],
    threshold_pct: float,
    *,
    evaluation_period_years: float,
) -> list[dict[str, Any]]:
    by_duration: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        by_duration.setdefault(float(row["duration_hours"]), []).append(row)
    results: list[dict[str, Any]] = []
    for duration in sorted(by_duration):
        group = by_duration[duration]
        highest_value = min(group, key=_recommendation_sort_key)
        highest_revenue = min(
            group,
            key=lambda row: (
                -float(row["annual_revenue_uplift_eur"]),
                float(row["power_kw"]),
                float(row["usable_energy_kwh"]),
            ),
        )
        max_uplift = max(float(row["annual_revenue_uplift_eur"]) for row in group)
        target = (float(threshold_pct) / 100.0) * max_uplift
        capture_candidates = [
            row for row in group if float(row["annual_revenue_uplift_eur"]) + 1e-12 >= target
        ]
        capture = min(capture_candidates, key=lambda row: (float(row["power_kw"]), float(row["usable_energy_kwh"])))
        largest_power = max(float(row["power_kw"]) for row in group)
        boundary = (
            abs(float(highest_value["power_kw"]) - largest_power) <= 1e-9
            or abs(float(highest_revenue["power_kw"]) - largest_power) <= 1e-9
        )
        shortest = select_shortest_payback_candidate(group)
        within_count = sum(
            1 for row in group if candidate_within_screening_period(row, evaluation_period_years)
        )
        largest_monthly = select_largest_average_monthly_peak_reduction(group)
        results.append(
            {
                "duration_hours": duration,
                "highest_value_candidate_id": highest_value["candidate_id"],
                "highest_value_eur": highest_value["estimated_value_eur"],
                "highest_revenue_candidate_id": highest_revenue["candidate_id"],
                "highest_annual_revenue_uplift_eur": highest_revenue["annual_revenue_uplift_eur"],
                "revenue_capture_threshold_pct": float(threshold_pct),
                "revenue_capture_target_eur": target,
                "revenue_capture_candidate_id": capture["candidate_id"],
                "revenue_capture_power_kw": capture["power_kw"],
                "revenue_capture_usable_energy_kwh": capture["usable_energy_kwh"],
                "revenue_capture_annual_uplift_eur": capture["annual_revenue_uplift_eur"],
                "largest_tested_power_kw": largest_power,
                "range_boundary_reached": boundary,
                "range_boundary_note": RANGE_BOUNDARY_NOTE if boundary else None,
                "shortest_payback_candidate_id": None if shortest is None else shortest["candidate_id"],
                "shortest_simple_payback_years": None
                if shortest is None
                else shortest["simple_payback_years"],
                "candidates_with_payback_within_screening_period_count": within_count,
                "shortest_payback_candidate": None
                if shortest is None
                else candidate_screening_snapshot(shortest, evaluation_period_years),
                "largest_average_monthly_peak_reduction_candidate_id": None
                if largest_monthly is None
                else largest_monthly["candidate_id"],
                "largest_average_monthly_peak_reduction_candidate": None
                if largest_monthly is None
                else candidate_peak_snapshot(largest_monthly),
            }
        )
    return results


def explanations(*, partial_period: bool) -> dict[str, Any]:
    warnings = [PARTIAL_PERIOD_WARNING] if partial_period else []
    return {
        "capex_explanation": CAPEX_EXPLANATION,
        "estimated_value_explanation": VALUE_EXPLANATION,
        "partial_period_warning": PARTIAL_PERIOD_WARNING if partial_period else None,
        "warnings": warnings,
        "formulas": dict(FORMULAS),
    }


def duration_groups(rows: Iterable[dict[str, Any]]) -> dict[float, list[dict[str, Any]]]:
    groups: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(float(row["duration_hours"]), []).append(row)
    return groups
