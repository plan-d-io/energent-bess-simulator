"""Display-only formatters and case names for full-comparison Results."""

from __future__ import annotations

from typing import Any, Mapping

EM_DASH = "—"
HISTORICAL_NA = "Not available in this historical result"
NO_PAYBACK = "No payback under these assumptions"

CASE_LABELS = {
    "no_battery": "No battery",
    "reference": "Rule-based control",
    "self_consumption": "Self-consumption",
    "peak_reduction": "Peak reduction",
    "revenue": "Revenue maximisation",
    "dynamic_injection": "Dynamic injection tariff",
}

HIGHLIGHT_COLUMNS = (
    "Comparison case",
    "Additional useful PV (MWh)",
    "Average monthly peak reduction (kW)",
    "Revenue increase (EUR)",
    "Simple payback period",
)

TAB_NAMES = (
    "Overview",
    "PV and grid energy",
    "Grid peaks",
    "Energent revenue",
    "Data explorer",
    "Technical details",
    "Downloads",
)

OVERVIEW_GROUPS = (
    "Site totals",
    "Energy and PV use",
    "Energent revenue and payback",
    "Grid peaks",
    "Battery use and limits",
)

FINANCIAL_CAPTION = (
    "Euro values are Energent PV revenue, not customer bill savings or profit."
)
METHOD_CAPTION = (
    "Rule-based control uses no foresight. The optimised cases use the complete "
    "selected period in advance and are best-case results, not operating forecasts."
)
REVENUE_LIMITATION = (
    "Energent PV revenue includes PV sold to the customer and PV injected into "
    "the grid. It is not profit, customer bill savings or NPV."
)
PEAK_DEFINITION = (
    "A monthly peak is the highest average grid-import power recorded during a "
    "15-minute interval in that local calendar month."
)
PEAK_COMPLETE_MONTHS = (
    "Average monthly peak uses complete local calendar months only."
)
PEAK_FINANCIAL_NOTE = (
    "No financial value is assigned to peak reduction in this version. A kW "
    "reduction is not customer bill savings because customer demand tariffs "
    "are not modelled."
)
DYNAMIC_BASELINE_NOTE = (
    "The comparison with the fixed-tariff no-battery baseline combines the "
    "injection tariff and battery dispatch. It is not the isolated battery value."
)
SOURCE_DEMO = "Stored demonstration result. Not recalculated."
SOURCE_LIVE = "Completed simulation result."
HISTORICAL_DYNAMIC_NOTE = (
    "This historical result does not include Dynamic injection tariff."
)
DISPLAY_ERROR_TITLE = "Results could not be displayed"
DISPLAY_ERROR_BODY = "The stored result files could not be read."
SEASON_LABELS = {
    "winter": "Winter",
    "spring": "Spring",
    "summer": "Summer",
    "autumn": "Autumn",
}


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number != number


def zero_if_tiny(value: float, limit: float = 1e-6) -> float:
    return 0.0 if abs(value) < limit else value


def as_mwh(kwh: Any) -> float:
    return float(kwh) / 1000.0


def fmt_mwh(kwh: Any, *, digits: int = 2, unit: bool = True) -> str:
    if is_missing(kwh):
        return EM_DASH
    text = f"{as_mwh(kwh):,.{digits}f}"
    return f"{text} MWh" if unit else text


def fmt_kw(kw: Any, *, digits: int = 1, unit: bool = True) -> str:
    if is_missing(kw):
        return EM_DASH
    text = f"{zero_if_tiny(float(kw)):,.{digits}f}"
    return f"{text} kW" if unit else text


def fmt_pct(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return EM_DASH
    return f"{zero_if_tiny(float(value)):.{digits}f}%"


def fmt_pp(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return EM_DASH
    return f"{zero_if_tiny(float(value)):+.{digits}f} pp"


def fmt_eur(value: Any, *, digits: int = 0) -> str:
    if is_missing(value):
        return EM_DASH
    return f"EUR\u00a0{zero_if_tiny(float(value)):,.{digits}f}"


def fmt_years(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return EM_DASH
    return f"{float(value):.{digits}f} years"


def fmt_cycles(value: Any, *, digits: int = 1) -> str:
    if is_missing(value):
        return HISTORICAL_NA
    return f"{float(value):,.{digits}f}"


def fmt_count(value: Any) -> str:
    return f"{int(value):,}"


def case_label(scenario: str) -> str:
    return CASE_LABELS.get(scenario, scenario.replace("_", " ").capitalize())


def has_economics(summary: Mapping[str, Any] | None) -> bool:
    payload = (summary or {}).get("economics")
    if not isinstance(payload, Mapping) or not payload:
        return False
    scenarios = (summary or {}).get("scenarios") or {}
    for key, case in scenarios.items():
        if key == "no_battery" or not isinstance(case, Mapping):
            continue
        if "simple_payback_years" in case or "payback_applicable" in case:
            return True
    return "estimated_battery_capex_eur" in payload


def format_payback(case: Mapping[str, Any] | None, *, scenario: str, economics: bool) -> str:
    if scenario == "no_battery":
        return EM_DASH
    if not economics:
        return HISTORICAL_NA
    years = None if case is None else case.get("simple_payback_years")
    applicable = None if case is None else case.get("payback_applicable")
    if is_missing(years) or applicable is False:
        return NO_PAYBACK
    return fmt_years(years)


def format_capex(case: Mapping[str, Any] | None, *, scenario: str, economics: bool) -> str:
    if scenario == "no_battery":
        return EM_DASH
    if not economics:
        return HISTORICAL_NA
    value = None if case is None else case.get("estimated_battery_capex_eur")
    if is_missing(value):
        return EM_DASH
    return fmt_eur(value)


def cycle_constrained(case: Mapping[str, Any] | None) -> str:
    flag = None if case is None else case.get("cycle_limit_binding")
    if flag is None:
        return HISTORICAL_NA
    return "Yes, the throughput limit constrained this case" if flag else "No"


def is_dynamic_case(case: Mapping[str, Any] | None, *, scenario: str = "") -> bool:
    if scenario == "dynamic_injection":
        return True
    if not case:
        return False
    if case.get("scenario") == "dynamic_injection":
        return True
    revenue = case.get("revenue") if isinstance(case.get("revenue"), Mapping) else {}
    return revenue.get("settlement_mode") == "dynamic_injection"


def nested_value(case: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in case:
            return case[name]
        revenue = case.get("revenue")
        if isinstance(revenue, Mapping) and name in revenue:
            return revenue[name]
    return None


SWEEP_SOLVER_PROVENANCE_UNAVAILABLE = (
    "Solver provenance is not available in this historical result."
)


def _stored_solver_version_value(record: Mapping[str, Any]) -> Any:
    name = str(record.get("name") or "").strip()
    if name == "HiGHS":
        return record.get("highs_version") or record.get("highspy_version")
    if name == "Gurobi":
        return record.get("gurobipy_version")
    return (
        record.get("highs_version")
        or record.get("highspy_version")
        or record.get("gurobipy_version")
    )


def solver_version_from_record(record: Mapping[str, Any] | None) -> str | None:
    if not isinstance(record, Mapping):
        return None
    value = _stored_solver_version_value(record)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def solver_provenance_line(record: Mapping[str, Any] | None) -> str | None:
    if not isinstance(record, Mapping):
        return None
    name = str(record.get("name") or "").strip()
    if not name:
        return None
    version = solver_version_from_record(record)
    if version:
        return f"Solver: {name} {version}"
    return f"Solver: {name}"
