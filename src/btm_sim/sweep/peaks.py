"""Physical peak-reduction reporting for a revenue-maximisation sweep."""

from __future__ import annotations

import math
from typing import Any, Sequence

DISPATCH_STRATEGY = "revenue_maximisation"
PEAK_EXPLANATION = (
    "These are physical peak reductions under Revenue maximisation dispatch. The "
    "sweep does not optimise peak reduction. Customer demand tariffs are not "
    "modelled, so these results are not bill savings and are not included in "
    "Energent revenue or simple payback."
)
LARGEST_AVERAGE_MONTHLY_PEAK_REDUCTION_LABEL = (
    "Largest average monthly peak reduction among the tested sizes"
)
AVERAGE_MONTHLY_PEAK_LABEL = "Average monthly peak"
AVERAGE_MONTHLY_PEAK_REDUCTION_LABEL = "Average monthly peak reduction"
HIGHEST_INTERVAL_PEAK_LABEL = "Highest 15-minute grid import during the selected period"
HIGHEST_INTERVAL_PEAK_REDUCTION_LABEL = "Reduction in highest 15-minute grid import"

PEAK_CANDIDATE_KEYS = (
    "baseline_annual_peak_kw",
    "annual_peak_kw",
    "annual_peak_reduction_kw",
    "annual_peak_reduction_pct",
    "baseline_average_monthly_peak_kw",
    "average_monthly_peak_kw",
    "average_monthly_peak_reduction_kw",
    "average_monthly_peak_reduction_pct",
    "average_monthly_peak_n_complete_months",
)

PEAK_METRIC_SNAPSHOT_KEYS = PEAK_CANDIDATE_KEYS

PEAK_SNAPSHOT_KEYS = (
    "candidate_id",
    "power_kw",
    "usable_energy_kwh",
    "duration_hours",
    "estimated_capex_eur",
    "annual_revenue_uplift_eur",
    "simple_payback_years",
    "equivalent_full_cycles",
    "cycle_limit_binding",
    *PEAK_METRIC_SNAPSHOT_KEYS,
)


def candidate_peak_fields_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Copy comparison peak fields onto a sweep candidate row."""
    payload = {key: metrics[key] for key in PEAK_CANDIDATE_KEYS}
    payload["average_monthly_peak_n_complete_months"] = int(
        metrics["average_monthly_peak_n_complete_months"]
    )
    return payload


def baseline_peak_fields_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Peak fields for the top-level no-battery baseline object."""
    return {
        "annual_peak_kw": metrics["annual_peak_kw"],
        "baseline_annual_peak_kw": metrics["baseline_annual_peak_kw"],
        "average_monthly_peak_kw": metrics["average_monthly_peak_kw"],
        "baseline_average_monthly_peak_kw": metrics["baseline_average_monthly_peak_kw"],
        "average_monthly_peak_n_complete_months": int(
            metrics["average_monthly_peak_n_complete_months"]
        ),
    }


def build_peak_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Rank tested sizes by physical peak reduction under Revenue maximisation."""
    n_complete = _complete_month_count(rows)
    baseline_annual = _first_finite(rows, "baseline_annual_peak_kw")
    baseline_monthly = _first_value(rows, "baseline_average_monthly_peak_kw")
    monthly_available = n_complete > 0 and _finite_number(baseline_monthly) is not None
    monthly_winner = (
        select_largest_average_monthly_peak_reduction(rows) if monthly_available else None
    )
    interval_winner = select_largest_highest_interval_peak_reduction(rows)
    positive_monthly_count = 0
    if monthly_available:
        positive_monthly_count = sum(
            1
            for row in rows
            if _positive_finite_reduction(row.get("average_monthly_peak_reduction_kw")) is not None
        )
    return {
        "dispatch_strategy": DISPATCH_STRATEGY,
        "financial_value_modelled": False,
        "baseline_annual_peak_kw": baseline_annual,
        "baseline_average_monthly_peak_kw": None
        if not monthly_available
        else _finite_number(baseline_monthly),
        "average_monthly_peak_n_complete_months": n_complete,
        "average_monthly_peak_available": monthly_available,
        "candidates_with_positive_average_monthly_peak_reduction_count": positive_monthly_count,
        "largest_average_monthly_peak_reduction_candidate": None
        if monthly_winner is None
        else candidate_peak_snapshot(monthly_winner),
        "largest_highest_interval_peak_reduction_candidate": None
        if interval_winner is None
        else candidate_peak_snapshot(interval_winner),
        "explanation": PEAK_EXPLANATION,
        "labels": {
            "largest_average_monthly_peak_reduction": LARGEST_AVERAGE_MONTHLY_PEAK_REDUCTION_LABEL,
            "average_monthly_peak": AVERAGE_MONTHLY_PEAK_LABEL,
            "average_monthly_peak_reduction": AVERAGE_MONTHLY_PEAK_REDUCTION_LABEL,
            "highest_interval_peak": HIGHEST_INTERVAL_PEAK_LABEL,
            "highest_interval_peak_reduction": HIGHEST_INTERVAL_PEAK_REDUCTION_LABEL,
        },
    }


def candidate_peak_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in PEAK_SNAPSHOT_KEYS}


def select_largest_average_monthly_peak_reduction(
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    available = [
        row
        for row in rows
        if _complete_month_count([row]) > 0
        and _finite_number(row.get("average_monthly_peak_kw")) is not None
    ]
    return _select_largest_reduction(available, "average_monthly_peak_reduction_kw")


def select_largest_highest_interval_peak_reduction(
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    return _select_largest_reduction(rows, "annual_peak_reduction_kw")


def _select_largest_reduction(
    rows: Sequence[dict[str, Any]],
    field: str,
) -> dict[str, Any] | None:
    eligible = [
        row for row in rows if _positive_finite_reduction(row.get(field)) is not None
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda row: _largest_reduction_sort_key(row, field))


def _largest_reduction_sort_key(row: dict[str, Any], field: str) -> tuple:
    reduction = _positive_finite_reduction(row.get(field))
    return (
        math.inf if reduction is None else -reduction,
        float(row["estimated_capex_eur"]),
        float(row["usable_energy_kwh"]),
        float(row["power_kw"]),
        float(row["duration_hours"]),
        str(row["candidate_id"]),
    )


def _complete_month_count(rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    value = rows[0].get("average_monthly_peak_n_complete_months")
    number = _finite_number(value)
    if number is None:
        return 0
    return int(number)


def _first_finite(rows: Sequence[dict[str, Any]], key: str) -> float | None:
    for row in rows:
        number = _finite_number(row.get(key))
        if number is not None:
            return number
    return None


def _first_value(rows: Sequence[dict[str, Any]], key: str) -> Any:
    if not rows:
        return None
    return rows[0].get(key)


def _positive_finite_reduction(value: Any) -> float | None:
    number = _finite_number(value)
    if number is None or number <= 0:
        return None
    return number


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
