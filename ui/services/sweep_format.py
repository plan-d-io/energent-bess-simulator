"""Display-only copy and formatters for battery-size Results."""

from __future__ import annotations

from typing import Any

EM_DASH = "—"
HISTORICAL_NA = "Not available in this historical result"
NOT_APPLICABLE = "Not applicable"

OUTCOME_WITHIN = "one_or_more_candidates_within_screening_period"
OUTCOME_NO_WITHIN = "no_candidate_within_screening_period"
OUTCOME_NO_POSITIVE = "no_candidate_with_positive_annual_revenue"

TAB_NAMES = (
    "Overview",
    "Revenue and payback",
    "Grid peaks",
    "Battery use",
    "Additional details",
    "Downloads",
)

SOURCE_DEMO = "Stored demonstration result. Not recalculated."
SOURCE_LIVE = "Completed simulation result."
DISPATCH_STRATEGY = "Revenue maximisation"
PAGE_TITLE = "Battery-size comparison"
TOP_RESULTS_HEADING = "Top results"
ALL_SIZES_HEADING = "All tested sizes"
TRANSFER_HEADING = "Use a size for the single battery simulation"
TRANSFER_SELECTOR = "Battery size"
TRANSFER_LIVE = "Use this size in the full comparison"
TRANSFER_DEMO = "Use this size in a live full comparison"
TRANSFER_DISABLED_NOTE = (
    "This size cannot be transferred from the saved demonstration because the "
    "Ganda Cars sample files are not available. Turn off Demo mode and upload "
    "the files before running a live comparison."
)

HISTORICAL_SCREENING_NOTE = (
    "The payback-focused screening summary is not available in this historical result."
)
NO_POSITIVE_HEADLINE = (
    "No tested battery has a positive annual revenue increase under these "
    "assumptions. Simple payback is therefore not available."
)
RANGE_NOTE_SHORT = "Highest tested revenue occurs at the upper end of this range."
RANGE_BOUNDARY_CONSOLIDATED = (
    "Revenue is still increasing at the largest tested size for one or more "
    "durations. Extending the range may help map where revenue levels off."
)
CYCLE_LIMIT_EXPLANATION = (
    "Cycle limit reached means the simulation restricted battery use to the "
    "configured annual cycle allowance. The reported revenue already accounts "
    "for that restriction."
)
CAPTURE_MARKER_NOTE = (
    "The revenue-capture marker is the smallest tested size reaching the configured "
    "percentage of the highest revenue found within that duration's tested range. "
    "If the range boundary was reached, extending the range may move this marker."
)
SHORTEST_PAYBACK_METRIC = "Shortest simple payback"
HIGHEST_REVENUE_METRIC = "Highest annual revenue increase among the tested sizes"
LARGEST_PEAK_METRIC = "Largest average monthly peak reduction among the tested sizes"
PEAK_NOT_IN_REVENUE_NOTE = "Not included in revenue or payback."
HISTORICAL_PEAK_NOTE = "Peak-reduction summary is not available in this historical result."
HISTORICAL_PEAK_CHART_NOTE = (
    "Reduction charts and fields require a newer saved run. Stored absolute peak "
    "values are shown where they exist."
)
PEAK_COBENEFIT_UNAVAILABLE = "Average monthly peak reduction: not available for this period"
AVERAGE_MONTHLY_PEAK_DEFINITION = (
    "Average monthly peak is the mean of the highest average grid-import power in "
    "a 15-minute interval for each complete calendar month in the selected period."
)
ZERO_COMPLETE_MONTH_NOTE = (
    "Average monthly peak is not available because this selected period has no "
    "complete local calendar month."
)
NO_POSITIVE_PEAK_NOTE = (
    "No tested battery has a positive average monthly peak reduction under Revenue "
    "maximisation dispatch."
)
PEAK_EXPLANATION_FALLBACK = (
    "These are physical peak reductions under Revenue maximisation dispatch. The "
    "sweep does not optimise peak reduction. Customer demand tariffs are not "
    "modelled, so these results are not bill savings and are not included in "
    "Energent revenue or simple payback."
)
PARTIAL_PERIOD_WARNING = (
    "Battery sizing works best with a complete calendar year. This period will be "
    "scaled to one year for the sizing estimate, but seasonal differences may "
    "materially change the result."
)
REVENUE_PHRASE = "Annual revenue increase"
REVENUE_PHRASE_PARTIAL = "Estimated annual revenue increase"

FLAG_GLOSSARY = (
    (
        "Cycle-limited",
        "The configured cycle allowance constrained this candidate. Its reported "
        "revenue already respects that limit.",
    ),
    (
        "Revenue-capture",
        "For this duration, this is the smallest tested battery reaching the "
        "configured percentage of the highest annual revenue found in the tested range.",
    ),
    (
        "Range-boundary",
        "A highest-revenue result for this duration occurs at the largest tested "
        "power. Larger batteries were not tested, so the curve may continue to rise.",
    ),
    (
        "None",
        "No special sweep diagnostic applies to this candidate.",
    ),
)

DURATION_TABLE_COLUMNS = (
    "Duration",
    "Shortest-payback battery",
    "Shortest payback (years)",
    "Highest-revenue battery",
    "Highest annual revenue increase (EUR/year)",
)
CANDIDATE_TABLE_COLUMNS = (
    "Power (kW)",
    "Usable energy (kWh)",
    "Duration (h)",
    "Annual revenue increase (EUR)",
    "Simple payback (years)",
    "Estimated battery CAPEX (EUR)",
    "Equivalent full cycles",
    "Cycle limit reached",
    "Flags",
)
PEAK_TABLE_COLUMNS = (
    "Power (kW)",
    "Usable energy (kWh)",
    "Duration (h)",
    "Average monthly peak (kW)",
    "Average monthly peak reduction (kW)",
    "Average monthly peak reduction (%)",
    "Highest 15-minute grid import (kW)",
    "Reduction in highest 15-minute grid import (kW)",
    "Reduction in highest 15-minute grid import (%)",
)

FORBIDDEN_UI_PHRASES = (
    "Estimated value",
    "No battery is the suggested result",
    "annualised",
    "annualized",
)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number != number


def duration_label(hours: Any) -> str:
    number = float(hours)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number))} h"
    return f"{number:g} h"


def battery_spec(row: Any) -> str:
    if not row:
        return NOT_APPLICABLE
    return (
        f"{float(row['power_kw']):g} kW / {float(row['usable_energy_kwh']):g} kWh / "
        f"{duration_label(row['duration_hours'])}"
    )


def candidate_selector_label(row: Any, fallback: str) -> str:
    if not row:
        return fallback
    return (
        f"{float(row['power_kw']):g} kW / {float(row['usable_energy_kwh']):g} kWh "
        f"({duration_label(row['duration_hours'])})"
    )


def fmt_payback_years(value: Any, *, with_unit: bool = True) -> str:
    if is_missing(value):
        return NOT_APPLICABLE
    text = f"{float(value):.1f}"
    return f"{text} years" if with_unit else text


def fmt_eur_plain(value: Any) -> str:
    if is_missing(value):
        return NOT_APPLICABLE
    return f"EUR\u00a0{float(value):,.0f}"


def fmt_eur_year(value: Any) -> str:
    if is_missing(value):
        return NOT_APPLICABLE
    return f"EUR\u00a0{float(value):,.0f}/year"


def fmt_kw(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return NOT_APPLICABLE
    return f"{float(value):,.{digits}f} kW"


def fmt_pct_value(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return NOT_APPLICABLE
    return f"{float(value):.{digits}f}%"


def fmt_cycles(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return HISTORICAL_NA
    return f"{float(value):,.{digits}f}"


def payback_is_applicable(value: Any) -> bool:
    if is_missing(value):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number > 0
