"""Read-only full-comparison display model. No solvers or session state."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from ui.services.compare_format import (
    DYNAMIC_BASELINE_NOTE,
    EM_DASH,
    FINANCIAL_CAPTION,
    HIGHLIGHT_COLUMNS,
    HISTORICAL_DYNAMIC_NOTE,
    HISTORICAL_NA,
    METHOD_CAPTION,
    OVERVIEW_GROUPS,
    PEAK_COMPLETE_MONTHS,
    PEAK_DEFINITION,
    PEAK_FINANCIAL_NOTE,
    REVENUE_LIMITATION,
    SOURCE_DEMO,
    SOURCE_LIVE,
    as_mwh,
    case_label,
    cycle_constrained,
    format_capex,
    format_payback,
    fmt_cycles,
    fmt_count,
    fmt_eur,
    fmt_kw,
    fmt_mwh,
    fmt_pct,
    fmt_pp,
    has_economics,
    is_dynamic_case,
    is_missing,
    nested_value,
    solver_version_from_record,
)
from ui.services.paths import KIND_COMPARISON
from ui.services.results import SOURCE_DEMO as RESULTS_SOURCE_DEMO
from ui.services.results import SOURCE_LIVE as RESULTS_SOURCE_LIVE
from ui.services.results import results_are_valid
from ui.presentation.tokens import CHART_EXPLORER, CHART_SCENARIO, CHART_SERIES

REQUIRED_DISPLAY_FILES = (
    "comparison_summary.json",
    "monthly_summary.csv",
    "monthly_peaks.csv",
    "run_metadata.json",
)
OPTIONAL_DISPLAY_FILES = ("validation_report.json",)
REQUIRED_SUMMARY_KEYS = ("selected_period", "battery", "scenario_order", "scenarios")
REQUIRED_PERIOD_KEYS = ("n_intervals", "label")
REQUIRED_BATTERY_KEYS = ("e_usable_kwh", "p_charge_kw", "p_discharge_kw")
REQUIRED_SCENARIO_FIELDS = (
    "useful_pv_delivered_kwh",
    "additional_useful_pv_kwh",
    "additional_useful_pv_pct_of_total_pv",
    "useful_self_consumption_pct_after",
    "useful_self_consumption_change_pp",
    "self_sufficiency_pct",
    "grid_import_kwh",
    "grid_export_kwh",
    "annual_peak_kw",
    "annual_peak_reduction_kw",
    "annual_peak_reduction_pct",
    "average_monthly_peak_kw",
    "average_monthly_peak_reduction_kw",
    "average_monthly_peak_reduction_pct",
)
REQUIRED_REVENUE_FIELDS = (
    "total_customer_sales_eur",
    "total_energent_pv_revenue_eur",
    "revenue_change_eur",
    "revenue_change_pct",
    "total_export_eur",
)
MONTHLY_COLUMNS = (
    "month",
    "complete_local_month",
    "scenario",
    "total_pv_production_kwh",
    "site_load_kwh",
    "useful_pv_delivered_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
    "monthly_peak_kw",
    "monthly_peak_reduction_kw",
    "monthly_peak_reduction_pct",
    "total_customer_sales_eur",
    "direct_pv_customer_sales_eur",
    "battery_customer_sales_eur",
    "export_peak_eur",
    "export_offpeak_eur",
    "total_energent_pv_revenue_eur",
    "revenue_change_eur",
)
HIGHEST_PEAK_LABEL = "Highest 15-minute grid import during the selected period (kW)"
AVERAGE_PEAK_LABEL = "Average monthly peak (kW)"
PAYBACK_DEFINITION_FALLBACK = (
    "Simple payback compares estimated battery CAPEX with annualised Energent "
    "PV-revenue increase. It excludes financing, discounting, operating costs, "
    "degradation, replacement, tax, inflation, and future tariff changes."
)
PARTIAL_PAYBACK_FALLBACK = (
    "Revenue from this partial period was scaled to one year. Seasonal effects "
    "may make the payback estimate unrepresentative."
)


class ComparisonDisplayError(Exception):
    """Required comparison display files could not be read as one result."""


@dataclass(frozen=True)
class ChartSpec:
    title: str
    x_title: str
    y_title: str
    kind: str
    value_format: str
    series_order: tuple[str, ...]
    colours: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, Any], ...]
    x_type: str = "category"


@dataclass(frozen=True)
class ComparisonDisplay:
    folder: str
    cache_key: tuple[Any, ...]
    header: dict[str, Any]
    cases: tuple[tuple[str, str], ...]
    overview: dict[str, Any]
    energy: dict[str, Any]
    peaks: dict[str, Any]
    revenue: dict[str, Any]
    technical: dict[str, Any]
    downloads: dict[str, Any]
    explorer: dict[str, Any]
    monthly: pd.DataFrame
    monthly_peaks: pd.DataFrame
    summary: dict[str, Any]
    notes: tuple[str, ...] = field(default_factory=tuple)


def display_cache_key(folder: Path | str) -> tuple[Any, ...]:
    root = Path(folder)
    parts: list[Any] = [str(root.resolve())]
    for name in REQUIRED_DISPLAY_FILES + OPTIONAL_DISPLAY_FILES:
        path = root / name
        try:
            if path.is_file():
                stat = path.stat()
                parts.append((name, int(stat.st_size), int(stat.st_mtime_ns)))
            else:
                parts.append((name, None, None))
        except OSError:
            parts.append((name, None, None))
    return tuple(parts)


def comparison_display_guard(results: Mapping[str, Any] | None) -> str | None:
    if not results_are_valid(results):
        return "invalid"
    assert results is not None
    if str(results.get("kind") or "") != KIND_COMPARISON:
        return "kind"
    source = str(results.get("source") or "")
    if source not in {RESULTS_SOURCE_LIVE, RESULTS_SOURCE_DEMO}:
        return "source"
    raw = str(results.get("result_dir") or "").strip()
    if not raw:
        return "directory"
    folder = Path(raw)
    try:
        if not folder.is_dir():
            return "directory"
    except OSError:
        return "directory"
    return None


def visible_cases(summary: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    scenarios = summary.get("scenarios") if isinstance(summary.get("scenarios"), Mapping) else {}
    order = summary.get("scenario_order")
    if isinstance(order, list) and order:
        keys = [str(key) for key in order if str(key) in scenarios]
    else:
        keys = [key for key in scenarios]
    return tuple((key, case_label(key)) for key in keys)


def battery_cases(summary: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(item for item in visible_cases(summary) if item[0] != "no_battery")


def optimized_cases(summary: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    skip = {"no_battery", "reference"}
    return tuple(item for item in visible_cases(summary) if item[0] not in skip)


def default_strategy(cases: Sequence[tuple[str, str]], preferred: str) -> str:
    keys = [key for key, _label in cases]
    if preferred in keys:
        return preferred
    for key, _label in cases:
        if key != "no_battery":
            return key
    return keys[0] if keys else preferred


def scenario_colour(scenario: str) -> str:
    return CHART_SCENARIO.get(scenario, CHART_SCENARIO["reference"])


def series_colour(name: str, scenario: str | None = None) -> str:
    if name in CHART_SERIES:
        return CHART_SERIES[name]
    if scenario:
        return scenario_colour(scenario)
    return CHART_SCENARIO["reference"]


def battery_to_grid_kwh(case: Mapping[str, Any]) -> Any:
    if "battery_discharge_to_grid_kwh" not in case:
        return None
    return case.get("battery_discharge_to_grid_kwh")


def pv_injected_same_qh_kwh(case: Mapping[str, Any]) -> Any:
    export = case.get("grid_export_kwh")
    grid = battery_to_grid_kwh(case)
    if is_missing(export):
        return None
    extra = 0.0 if is_missing(grid) else float(grid)
    return float(export) - extra


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ComparisonDisplayError(f"Could not read {path.name}.") from exc
    if not isinstance(payload, dict):
        raise ComparisonDisplayError(f"{path.name} is not a mapping.")
    return payload


def _read_csv(path: Path, *, comment: str | None = None) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, comment=comment)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
        raise ComparisonDisplayError(f"Could not read {path.name}.") from exc
    if not isinstance(frame, pd.DataFrame):
        raise ComparisonDisplayError(f"{path.name} is not a table.")
    return frame.copy()


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], *, name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ComparisonDisplayError(f"{name} is missing columns: {', '.join(missing)}.")


def _validate_summary(summary: Mapping[str, Any]) -> None:
    for key in REQUIRED_SUMMARY_KEYS:
        if key not in summary:
            raise ComparisonDisplayError(f"comparison_summary.json is missing {key}.")
    period = summary.get("selected_period")
    battery = summary.get("battery")
    scenarios = summary.get("scenarios")
    order = summary.get("scenario_order")
    if not isinstance(period, Mapping):
        raise ComparisonDisplayError("selected_period is not a mapping.")
    if not isinstance(battery, Mapping):
        raise ComparisonDisplayError("battery is not a mapping.")
    if not isinstance(scenarios, Mapping) or not scenarios:
        raise ComparisonDisplayError("scenarios is not a mapping.")
    if not isinstance(order, list) or not order:
        raise ComparisonDisplayError("scenario_order is missing.")
    for key in REQUIRED_PERIOD_KEYS:
        if key not in period:
            raise ComparisonDisplayError(f"selected_period is missing {key}.")
    for key in REQUIRED_BATTERY_KEYS:
        if key not in battery:
            raise ComparisonDisplayError(f"battery is missing {key}.")
    cases = visible_cases(summary)
    if not cases:
        raise ComparisonDisplayError("No stored comparison cases.")
    if "no_battery" not in {key for key, _label in cases}:
        raise ComparisonDisplayError("The no-battery baseline is missing.")
    for key, _label in cases:
        case = scenarios.get(key)
        if not isinstance(case, Mapping):
            raise ComparisonDisplayError(f"Scenario {key} is missing.")
        for field in REQUIRED_SCENARIO_FIELDS:
            if field not in case:
                raise ComparisonDisplayError(f"Scenario {key} is missing {field}.")
        revenue = case.get("revenue")
        if not isinstance(revenue, Mapping):
            raise ComparisonDisplayError(f"Scenario {key} is missing revenue.")
        for field in REQUIRED_REVENUE_FIELDS:
            if field not in revenue:
                raise ComparisonDisplayError(f"Scenario {key} is missing {field}.")


def load_comparison_files(folder: Path | str) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any] | None]:
    root = Path(folder)
    missing = [name for name in REQUIRED_DISPLAY_FILES if not (root / name).is_file()]
    if missing:
        raise ComparisonDisplayError("Required result files are missing.")
    summary = _read_json(root / "comparison_summary.json")
    metadata = _read_json(root / "run_metadata.json")
    monthly = _read_csv(root / "monthly_summary.csv")
    peaks = _read_csv(root / "monthly_peaks.csv", comment="#")
    validation = None
    report_path = root / "validation_report.json"
    if report_path.is_file():
        validation = _read_json(report_path)
    _validate_summary(summary)
    _require_columns(monthly, MONTHLY_COLUMNS, name="monthly_summary.csv")
    _require_columns(peaks, ("month",), name="monthly_peaks.csv")
    for key, _label in visible_cases(summary):
        column = f"{key}_kw"
        if column not in peaks.columns:
            raise ComparisonDisplayError(f"monthly_peaks.csv is missing {column}.")
    return copy.deepcopy(summary), monthly.copy(), peaks.copy(), copy.deepcopy(metadata), (
        copy.deepcopy(validation) if validation is not None else None
    )


def _cases_as_columns(
    summary: Mapping[str, Any],
    rows: Sequence[tuple[str, Callable[[Mapping[str, Any], str], Any]]],
) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for metric, getter in rows:
        row: dict[str, Any] = {"Metric": metric}
        for key, label in visible_cases(summary):
            row[label] = getter(summary["scenarios"][key], key)
        table.append(row)
    return table


def _delta_or_dash(scenario: str, formatted: str) -> str:
    return EM_DASH if scenario == "no_battery" else formatted


def highlight_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    economics = has_economics(summary)
    rows: list[dict[str, str]] = []
    for key, label in visible_cases(summary):
        case = summary["scenarios"][key]
        revenue = case["revenue"]
        rows.append(
            {
                HIGHLIGHT_COLUMNS[0]: label,
                HIGHLIGHT_COLUMNS[1]: _delta_or_dash(key, fmt_mwh(case["additional_useful_pv_kwh"], unit=False)),
                HIGHLIGHT_COLUMNS[2]: _delta_or_dash(
                    key, fmt_kw(case["average_monthly_peak_reduction_kw"], unit=False)
                ),
                HIGHLIGHT_COLUMNS[3]: _delta_or_dash(key, fmt_eur(revenue["revenue_change_eur"])),
                HIGHLIGHT_COLUMNS[4]: format_payback(case, scenario=key, economics=economics),
            }
        )
    return rows


def site_totals_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    no_batt = summary["scenarios"]["no_battery"]
    return [
        {"Metric": "PV production (MWh)", "Value": fmt_mwh(no_batt["total_pv_production_kwh"])},
        {"Metric": "Site load (MWh)", "Value": fmt_mwh(no_batt["site_load_kwh"])},
        {
            "Metric": "Useful PV supplied to the customer before the battery (MWh)",
            "Value": fmt_mwh(no_batt["useful_pv_delivered_kwh"]),
        },
    ]


def overview_energy_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            ("Useful PV supplied to the customer (MWh)", lambda case, _key: fmt_mwh(case["useful_pv_delivered_kwh"])),
            ("Additional useful PV (MWh)", lambda case, _key: fmt_mwh(case["additional_useful_pv_kwh"])),
            (
                "Additional useful PV share of total PV (%)",
                lambda case, _key: fmt_pct(case["additional_useful_pv_pct_of_total_pv"]),
            ),
            (
                "Useful self-consumption (%)",
                lambda case, _key: fmt_pct(case["useful_self_consumption_pct_after"]),
            ),
            (
                "Self-consumption change (pp)",
                lambda case, _key: fmt_pp(case["useful_self_consumption_change_pp"]),
            ),
            ("Self-sufficiency (%)", lambda case, _key: fmt_pct(case["self_sufficiency_pct"])),
            ("Grid electricity imported (MWh)", lambda case, _key: fmt_mwh(case["grid_import_kwh"])),
            ("Total energy injected into the grid (MWh)", lambda case, _key: fmt_mwh(case["grid_export_kwh"])),
            (
                "Battery energy injected into the grid (MWh)",
                lambda case, _key: HISTORICAL_NA
                if "battery_discharge_to_grid_kwh" not in case
                else fmt_mwh(case.get("battery_discharge_to_grid_kwh")),
            ),
        ],
    )


def _grid_injection_revenue(case: Mapping[str, Any], key: str) -> str:
    if is_dynamic_case(case, scenario=key):
        value = nested_value(case, "dynamic_grid_injection_revenue_eur", "total_export_eur")
        return EM_DASH if is_missing(value) else fmt_eur(value)
    return fmt_eur(nested_value(case, "total_export_eur"))


def overview_revenue_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    economics = has_economics(summary)
    return _cases_as_columns(
        summary,
        [
            (
                "PV sold to the customer (EUR)",
                lambda case, _key: fmt_eur(case["revenue"]["total_customer_sales_eur"]),
            ),
            ("Grid-injection revenue (EUR)", _grid_injection_revenue),
            (
                "Total Energent PV revenue (EUR)",
                lambda case, _key: fmt_eur(case["revenue"]["total_energent_pv_revenue_eur"]),
            ),
            ("Revenue increase (EUR)", lambda case, _key: fmt_eur(case["revenue"]["revenue_change_eur"])),
            (
                "Revenue increase versus no battery (%)",
                lambda case, _key: fmt_pct(case["revenue"]["revenue_change_pct"]),
            ),
            (
                "Estimated battery CAPEX (EUR)",
                lambda case, key: format_capex(case, scenario=key, economics=economics),
            ),
            (
                "Simple payback period (years)",
                lambda case, key: format_payback(case, scenario=key, economics=economics),
            ),
        ],
    )


def overview_peaks_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            (AVERAGE_PEAK_LABEL, lambda case, _key: fmt_kw(case["average_monthly_peak_kw"])),
            (
                "Reduction in average monthly peak (kW)",
                lambda case, _key: fmt_kw(case["average_monthly_peak_reduction_kw"]),
            ),
            (
                "Reduction in average monthly peak (%)",
                lambda case, _key: fmt_pct(case["average_monthly_peak_reduction_pct"]),
            ),
            (HIGHEST_PEAK_LABEL, lambda case, _key: fmt_kw(case["annual_peak_kw"])),
            (
                "Reduction in highest 15-minute grid import (kW)",
                lambda case, _key: fmt_kw(case["annual_peak_reduction_kw"]),
            ),
            (
                "Reduction in highest 15-minute grid import (%)",
                lambda case, _key: fmt_pct(case["annual_peak_reduction_pct"]),
            ),
        ],
    )


def overview_battery_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            (
                "Equivalent full cycles",
                lambda case, _key: fmt_cycles(case.get("equivalent_full_cycles")),
            ),
            (
                "Allowed equivalent full cycles",
                lambda case, _key: fmt_cycles(case.get("allowed_equivalent_full_cycles")),
            ),
            ("Cycle limit constrained this case", lambda case, _key: cycle_constrained(case)),
        ],
    )


def partial_period_warning(summary: Mapping[str, Any]) -> str | None:
    economics = summary.get("economics") if isinstance(summary.get("economics"), Mapping) else {}
    if not economics.get("annualised_from_partial_period"):
        return None
    text = economics.get("partial_period_warning")
    if isinstance(text, str) and text.strip():
        return text
    return PARTIAL_PAYBACK_FALLBACK


def payback_definition(summary: Mapping[str, Any]) -> str | None:
    if not has_economics(summary):
        return None
    economics = summary.get("economics") if isinstance(summary.get("economics"), Mapping) else {}
    text = economics.get("simple_payback_explanation")
    if isinstance(text, str) and text.strip():
        return text
    return PAYBACK_DEFINITION_FALLBACK


def _battery_fact(battery: Mapping[str, Any]) -> str:
    usable = battery["e_usable_kwh"]
    charge = battery["p_charge_kw"]
    discharge = battery["p_discharge_kw"]
    if charge == discharge:
        return f"{float(usable):g} kWh · {float(charge):g} kW charge and discharge"
    return (
        f"{float(usable):g} kWh · {float(charge):g} kW charge / {float(discharge):g} kW discharge"
    )


def _header(summary: Mapping[str, Any], *, site: str, source: str) -> dict[str, Any]:
    period = summary["selected_period"]
    battery = summary["battery"]
    cases = visible_cases(summary)
    notes: list[str] = []
    if "dynamic_injection" not in {key for key, _label in cases}:
        notes.append(HISTORICAL_DYNAMIC_NOTE)
    return {
        "title": f"{site}: results" if site else "Results",
        "source_line": SOURCE_DEMO if source == RESULTS_SOURCE_DEMO else SOURCE_LIVE,
        "period_label": str(period.get("label") or ""),
        "battery_fact": _battery_fact(battery),
        "case_count": str(len(cases)),
        "notes": tuple(notes),
    }


def monthly_rows_for(monthly: pd.DataFrame, scenario: str) -> pd.DataFrame:
    frame = monthly.loc[monthly["scenario"] == scenario].copy()
    return frame.sort_values("month", kind="mergesort")


def monthly_pair(monthly: pd.DataFrame, scenario: str) -> pd.DataFrame:
    left = monthly_rows_for(monthly, "no_battery")
    right = monthly_rows_for(monthly, scenario)
    return left.merge(right, on="month", suffixes=("_nb", "_sel"))


def _chart(
    *,
    title: str,
    x_title: str,
    y_title: str,
    kind: str,
    value_format: str,
    series_order: Sequence[str],
    colours: Mapping[str, str],
    rows: Sequence[dict[str, Any]],
    x_type: str = "category",
) -> ChartSpec:
    return ChartSpec(
        title=title,
        x_title=x_title,
        y_title=y_title,
        kind=kind,
        value_format=value_format,
        series_order=tuple(series_order),
        colours=tuple((name, colours[name]) for name in series_order if name in colours),
        rows=tuple(dict(row) for row in rows),
        x_type=x_type,
    )


def energy_monthly_models(
    monthly: pd.DataFrame,
    *,
    scenario: str,
    label: str,
) -> tuple[list[dict[str, Any]], tuple[ChartSpec, ChartSpec]]:
    pair = monthly_pair(monthly, scenario)
    months = [str(value) for value in pair["month"].tolist()]
    table = [
        {
            "Month": month,
            "PV production (MWh)": round(as_mwh(pair["total_pv_production_kwh_sel"].iloc[index]), 2),
            "Site use (MWh)": round(as_mwh(pair["site_load_kwh_sel"].iloc[index]), 2),
            "Useful PV - no battery (MWh)": round(as_mwh(pair["useful_pv_delivered_kwh_nb"].iloc[index]), 2),
            "Useful PV - battery (MWh)": round(as_mwh(pair["useful_pv_delivered_kwh_sel"].iloc[index]), 2),
            "Grid import - no battery (MWh)": round(as_mwh(pair["grid_import_kwh_nb"].iloc[index]), 2),
            "Grid import - battery (MWh)": round(as_mwh(pair["grid_import_kwh_sel"].iloc[index]), 2),
            "PV injection - no battery (MWh)": round(as_mwh(pair["grid_export_kwh_nb"].iloc[index]), 2),
            "PV injection - battery (MWh)": round(as_mwh(pair["grid_export_kwh_sel"].iloc[index]), 2),
        }
        for index, month in enumerate(months)
    ]
    pv_rows: list[dict[str, Any]] = []
    grid_rows: list[dict[str, Any]] = []
    pv_series = (
        ("PV production", "total_pv_production_kwh_sel"),
        ("Site use", "site_load_kwh_sel"),
        ("Useful PV - no battery", "useful_pv_delivered_kwh_nb"),
        ("Useful PV - battery", "useful_pv_delivered_kwh_sel"),
    )
    grid_series = (
        ("Grid import - no battery", "grid_import_kwh_nb"),
        ("Grid import - battery", "grid_import_kwh_sel"),
        ("PV injection - no battery", "grid_export_kwh_nb"),
        ("PV injection - battery", "grid_export_kwh_sel"),
    )
    for month_index, month in enumerate(months):
        for name, column in pv_series:
            pv_rows.append({"Month": month, "Series": name, "Value": as_mwh(pair[column].iloc[month_index])})
        for name, column in grid_series:
            grid_rows.append({"Month": month, "Series": name, "Value": as_mwh(pair[column].iloc[month_index])})
    pv_colours = {name: CHART_EXPLORER.get(name, series_colour(name)) for name, _column in pv_series}
    grid_colours = {name: CHART_EXPLORER.get(name, series_colour(name)) for name, _column in grid_series}
    charts = (
        _chart(
            title="PV production, site use and useful PV",
            x_title="Month",
            y_title="Energy (MWh)",
            kind="line",
            value_format=",.2f",
            series_order=[name for name, _column in pv_series],
            colours=pv_colours,
            rows=pv_rows,
        ),
        _chart(
            title="Grid electricity",
            x_title="Month",
            y_title="Energy (MWh)",
            kind="line",
            value_format=",.2f",
            series_order=[name for name, _column in grid_series],
            colours=grid_colours,
            rows=grid_rows,
        ),
    )
    del label
    return table, charts


def peaks_chart_model(summary: Mapping[str, Any], peaks: pd.DataFrame) -> ChartSpec:
    months = [str(value) for value in peaks["month"].tolist()]
    series_order: list[str] = []
    colours: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for key, label in visible_cases(summary):
        column = f"{key}_kw"
        if column not in peaks.columns:
            continue
        series_order.append(label)
        colours[label] = scenario_colour(key)
        for month, value in zip(months, peaks[column].tolist(), strict=False):
            rows.append({"Month": month, "Series": label, "Value": float(value)})
    return _chart(
        title="Monthly peaks for all cases",
        x_title="Month",
        y_title="Monthly peak (kW)",
        kind="line",
        value_format=",.1f",
        series_order=series_order,
        colours=colours,
        rows=rows,
    )


def peaks_monthly_table(monthly: pd.DataFrame, scenario: str) -> list[dict[str, Any]]:
    pair = monthly_pair(monthly, scenario)
    rows = []
    for index, month in enumerate(pair["month"].tolist()):
        rows.append(
            {
                "Month": str(month),
                "Monthly peak - no battery (kW)": round(float(pair["monthly_peak_kw_nb"].iloc[index]), 1),
                "Monthly peak - battery (kW)": round(float(pair["monthly_peak_kw_sel"].iloc[index]), 1),
                "Reduction (kW)": round(float(pair["monthly_peak_reduction_kw_sel"].iloc[index]), 1),
                "Reduction (%)": round(float(pair["monthly_peak_reduction_pct_sel"].iloc[index]), 1),
            }
        )
    return rows


def _blank_if_dynamic(case: Mapping[str, Any], key: str, field: str, formatter) -> str:
    if is_dynamic_case(case, scenario=key):
        return EM_DASH
    value = nested_value(case, field)
    return EM_DASH if is_missing(value) else formatter(value)


def _blank_if_fixed(case: Mapping[str, Any], key: str, formatter) -> str:
    if not is_dynamic_case(case, scenario=key):
        return EM_DASH
    value = nested_value(case, "dynamic_grid_injection_revenue_eur", "total_export_eur")
    return EM_DASH if is_missing(value) else formatter(value)


def revenue_comparison_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            (
                "PV sold to the customer (EUR)",
                lambda case, _key: fmt_eur(case["revenue"]["total_customer_sales_eur"]),
            ),
            (
                "Battery-delivered share of those sales (EUR)",
                lambda case, _key: fmt_eur(case["revenue"].get("battery_customer_sales_eur")),
            ),
            (
                "Peak-period PV injection (EUR)",
                lambda case, key: _blank_if_dynamic(case, key, "export_peak_eur", fmt_eur),
            ),
            (
                "Off-peak PV injection (EUR)",
                lambda case, key: _blank_if_dynamic(case, key, "export_offpeak_eur", fmt_eur),
            ),
            (
                "Dynamic grid-injection revenue (EUR)",
                lambda case, key: _blank_if_fixed(case, key, fmt_eur),
            ),
            (
                "Total grid-injection revenue (EUR)",
                lambda case, key: EM_DASH
                if is_dynamic_case(case, scenario=key)
                else fmt_eur(case["revenue"]["total_export_eur"]),
            ),
            (
                "Total Energent PV revenue (EUR)",
                lambda case, _key: fmt_eur(case["revenue"]["total_energent_pv_revenue_eur"]),
            ),
            ("Increase (EUR)", lambda case, _key: fmt_eur(case["revenue"]["revenue_change_eur"])),
            ("Increase (%)", lambda case, _key: fmt_pct(case["revenue"]["revenue_change_pct"])),
        ],
    )


def revenue_detail_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            (
                "Direct PV sales (MWh)",
                lambda case, _key: EM_DASH
                if is_missing(case["revenue"].get("direct_pv_customer_sales_mwh"))
                else f"{float(case['revenue']['direct_pv_customer_sales_mwh']):,.2f}",
            ),
            (
                "Battery-delivered sales (MWh)",
                lambda case, _key: EM_DASH
                if is_missing(case["revenue"].get("battery_customer_sales_mwh"))
                else f"{float(case['revenue']['battery_customer_sales_mwh']):,.2f}",
            ),
            (
                "Peak injection (MWh)",
                lambda case, key: _blank_if_dynamic(
                    case,
                    key,
                    "export_peak_mwh",
                    lambda value: f"{float(value):,.2f}",
                ),
            ),
            (
                "Off-peak injection (MWh)",
                lambda case, key: _blank_if_dynamic(
                    case,
                    key,
                    "export_offpeak_mwh",
                    lambda value: f"{float(value):,.2f}",
                ),
            ),
            (
                "Export revenue given up (EUR)",
                lambda case, _key: fmt_eur(case["revenue"].get("foregone_export_eur")),
            ),
            (
                "Additional customer-sale revenue (EUR)",
                lambda case, _key: fmt_eur(case["revenue"].get("extra_customer_sale_eur")),
            ),
        ],
    )


def cost_payback_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    economics = has_economics(summary)
    rows: list[dict[str, str]] = []
    for key, label in visible_cases(summary):
        case = summary["scenarios"][key]
        period_change = nested_value(case, "period_revenue_uplift_eur", "revenue_change_eur")
        annual = case.get("annual_revenue_uplift_eur")
        row = {
            "Comparison case": label,
            "Period revenue increase (EUR)": EM_DASH if key == "no_battery" else fmt_eur(period_change),
            "Simple payback period": format_payback(case, scenario=key, economics=economics),
        }
        if economics and not is_missing(annual) and key != "no_battery":
            row["Annualised revenue increase (EUR)"] = fmt_eur(annual)
        elif economics:
            row["Annualised revenue increase (EUR)"] = EM_DASH
        else:
            row["Annualised revenue increase (EUR)"] = HISTORICAL_NA
        rows.append(row)
    return rows


def revenue_monthly_models(
    monthly: pd.DataFrame,
    *,
    scenario: str,
    label: str,
) -> tuple[list[dict[str, Any]], tuple[ChartSpec, ChartSpec, ChartSpec]]:
    pair = monthly_pair(monthly, scenario)
    months = [str(value) for value in pair["month"].tolist()]
    table = []
    total_rows: list[dict[str, Any]] = []
    increase_rows: list[dict[str, Any]] = []
    composition_rows: list[dict[str, Any]] = []
    dynamic = scenario == "dynamic_injection"
    for index, month in enumerate(months):
        table.append(
            {
                "Month": month,
                "No battery (EUR)": round(float(pair["total_energent_pv_revenue_eur_nb"].iloc[index]), 2),
                f"{label} (EUR)": round(float(pair["total_energent_pv_revenue_eur_sel"].iloc[index]), 2),
                "Increase (EUR)": round(float(pair["revenue_change_eur_sel"].iloc[index]), 2),
            }
        )
        total_rows.append({"Month": month, "Series": "No battery", "Value": float(pair["total_energent_pv_revenue_eur_nb"].iloc[index])})
        total_rows.append({"Month": month, "Series": label, "Value": float(pair["total_energent_pv_revenue_eur_sel"].iloc[index])})
        increase_rows.append(
            {
                "Month": month,
                "Series": "Increase vs no battery",
                "Value": float(pair["revenue_change_eur_sel"].iloc[index]),
            }
        )
        composition_rows.append(
            {
                "Month": month,
                "Series": "PV sold directly",
                "Value": float(pair["direct_pv_customer_sales_eur_sel"].iloc[index]),
            }
        )
        composition_rows.append(
            {
                "Month": month,
                "Series": "PV sold through battery",
                "Value": float(pair["battery_customer_sales_eur_sel"].iloc[index]),
            }
        )
        if dynamic:
            composition_rows.append(
                {
                    "Month": month,
                    "Series": "Dynamic grid-injection revenue",
                    "Value": float(pair["export_peak_eur_sel"].iloc[index])
                    + float(pair["export_offpeak_eur_sel"].iloc[index]),
                }
            )
        else:
            composition_rows.append(
                {
                    "Month": month,
                    "Series": "Peak-period PV injection",
                    "Value": float(pair["export_peak_eur_sel"].iloc[index]),
                }
            )
            composition_rows.append(
                {
                    "Month": month,
                    "Series": "Off-peak PV injection",
                    "Value": float(pair["export_offpeak_eur_sel"].iloc[index]),
                }
            )
    composition_order = (
        ("PV sold directly", "PV sold through battery", "Dynamic grid-injection revenue")
        if dynamic
        else ("PV sold directly", "PV sold through battery", "Peak-period PV injection", "Off-peak PV injection")
    )
    charts = (
        _chart(
            title="Monthly total Energent PV revenue",
            x_title="Month",
            y_title="Energent PV revenue (EUR)",
            kind="line",
            value_format=",.2f",
            series_order=("No battery", label),
            colours={"No battery": scenario_colour("no_battery"), label: scenario_colour(scenario)},
            rows=total_rows,
        ),
        _chart(
            title="Monthly revenue increase compared with no battery",
            x_title="Month",
            y_title="Revenue increase (EUR)",
            kind="bar",
            value_format=",.2f",
            series_order=("Increase vs no battery",),
            colours={"Increase vs no battery": series_colour("Increase vs no battery")},
            rows=increase_rows,
        ),
        _chart(
            title="Monthly revenue composition",
            x_title="Month",
            y_title="Energent PV revenue (EUR)",
            kind="stacked_bar",
            value_format=",.2f",
            series_order=composition_order,
            colours={name: series_colour(name) for name in composition_order},
            rows=composition_rows,
        ),
    )
    return table, charts


def pv_use_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            ("Useful PV (MWh)", lambda case, _key: fmt_mwh(case["useful_pv_delivered_kwh"])),
            ("Additional useful PV (MWh)", lambda case, _key: fmt_mwh(case["additional_useful_pv_kwh"])),
            (
                "Share of total PV (%)",
                lambda case, _key: fmt_pct(case["additional_useful_pv_pct_of_total_pv"]),
            ),
            (
                "Useful self-consumption (%)",
                lambda case, _key: fmt_pct(case["useful_self_consumption_pct_after"]),
            ),
            (
                "Change (pp)",
                lambda case, _key: fmt_pp(case["useful_self_consumption_change_pp"]),
            ),
            ("Self-sufficiency (%)", lambda case, _key: fmt_pct(case["self_sufficiency_pct"])),
            (
                "Battery energy to customer (MWh)",
                lambda case, _key: HISTORICAL_NA
                if "discharge_load_kwh" not in case
                else fmt_mwh(case.get("discharge_load_kwh")),
            ),
        ],
    )


def grid_energy_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _cases_as_columns(
        summary,
        [
            ("Grid electricity imported (MWh)", lambda case, _key: fmt_mwh(case["grid_import_kwh"])),
            (
                "PV injected into the grid in the same quarter-hour (MWh)",
                lambda case, _key: fmt_mwh(pv_injected_same_qh_kwh(case)),
            ),
            (
                "Stored PV later discharged to the grid (MWh)",
                lambda case, _key: HISTORICAL_NA
                if "battery_discharge_to_grid_kwh" not in case
                else fmt_mwh(case.get("battery_discharge_to_grid_kwh")),
            ),
            ("Total energy injected into the grid (MWh)", lambda case, _key: fmt_mwh(case["grid_export_kwh"])),
        ],
    )


def compact_peak_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = []
    for key, label in visible_cases(summary):
        case = summary["scenarios"][key]
        rows.append(
            {
                "Comparison case": label,
                HIGHEST_PEAK_LABEL: fmt_kw(case["annual_peak_kw"]),
                "Reduction in highest 15-minute grid import (kW)": fmt_kw(case["annual_peak_reduction_kw"]),
                "Reduction in highest 15-minute grid import (%)": fmt_pct(case["annual_peak_reduction_pct"]),
                AVERAGE_PEAK_LABEL: fmt_kw(case["average_monthly_peak_kw"]),
                "Reduction in average monthly peak (kW)": fmt_kw(case["average_monthly_peak_reduction_kw"]),
                "Reduction in average monthly peak (%)": fmt_pct(case["average_monthly_peak_reduction_pct"]),
            }
        )
    return rows


def technical_battery_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    def _mwh(case: Mapping[str, Any], field: str) -> str:
        if field not in case:
            return HISTORICAL_NA
        return fmt_mwh(case.get(field))

    return _cases_as_columns(
        summary,
        [
            ("PV charged (MWh)", lambda case, _key: _mwh(case, "charge_pv_kwh")),
            ("Useful discharge to customer (MWh)", lambda case, _key: _mwh(case, "discharge_load_kwh")),
            (
                "Battery discharge to grid (MWh)",
                lambda case, _key: _mwh(case, "battery_discharge_to_grid_kwh")
                if "battery_discharge_to_grid_kwh" in case
                else HISTORICAL_NA,
            ),
            ("Charge losses (MWh)", lambda case, _key: _mwh(case, "charge_loss_kwh")),
            ("Discharge losses (MWh)", lambda case, _key: _mwh(case, "discharge_loss_kwh")),
            ("Total conversion losses (MWh)", lambda case, _key: _mwh(case, "total_loss_kwh")),
            ("Stored-energy throughput (MWh)", lambda case, _key: _mwh(case, "stored_throughput_kwh")),
            ("Equivalent full cycles", lambda case, _key: fmt_cycles(case.get("equivalent_full_cycles"))),
            (
                "Allowed equivalent full cycles",
                lambda case, _key: fmt_cycles(case.get("allowed_equivalent_full_cycles")),
            ),
            ("Cycle limit constrained this case", lambda case, _key: cycle_constrained(case)),
            (
                "Ending charge (kWh)",
                lambda case, _key: EM_DASH
                if is_missing(case.get("soc_final_kwh"))
                else f"{float(case['soc_final_kwh']):,.1f}",
            ),
        ],
    )


def solver_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    solvers = summary.get("solvers") if isinstance(summary.get("solvers"), Mapping) else {}
    checks = summary.get("checks") if isinstance(summary.get("checks"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    for key, label in optimized_cases(summary):
        solver = solvers.get(key) if isinstance(solvers.get(key), Mapping) else None
        check = checks.get(key) if isinstance(checks.get(key), Mapping) else {}
        if solver is None:
            rows.append(
                {
                    "Comparison case": label,
                    "Solver": HISTORICAL_NA,
                    "Version": HISTORICAL_NA,
                    "Status": HISTORICAL_NA,
                    "Runtime (s)": HISTORICAL_NA,
                    "Battery limits and balances": HISTORICAL_NA,
                }
            )
            continue
        balance = check.get("battery_limits_and_balances") if isinstance(check, Mapping) else None
        version = solver_version_from_record(solver)
        rows.append(
            {
                "Comparison case": label,
                "Solver": str(solver.get("name") or HISTORICAL_NA),
                "Version": HISTORICAL_NA if version is None else version,
                "Status": str(solver.get("status") or HISTORICAL_NA),
                "Runtime (s)": HISTORICAL_NA
                if is_missing(solver.get("runtime_s"))
                else f"{float(solver['runtime_s']):.3f}",
                "Battery limits and balances": HISTORICAL_NA if balance is None else str(balance),
            }
        )
    return rows


def da_price_facts(summary: Mapping[str, Any]) -> dict[str, Any] | None:
    prices = summary.get("dynamic_injection_prices")
    if not isinstance(prices, Mapping) or not prices:
        return None
    source = str(prices.get("source_path") or "")
    basename = Path(source).name if source else "standard project dataset"
    coverage = prices.get("coverage_utc") or []
    native = prices.get("native_resolution_counts") if isinstance(prices.get("native_resolution_counts"), Mapping) else {}
    facts = {
        "source_basename": basename,
        "sha256": str(prices.get("source_sha256") or "not recorded"),
        "coverage": (
            f"{coverage[0]} to {coverage[1]}" if isinstance(coverage, list) and len(coverage) == 2 else None
        ),
        "selected_row_count": fmt_count(int(prices.get("selected_row_count") or 0)),
        "hourly_rows": fmt_count(int(native.get("PT60M") or native.get("hourly") or 0)),
        "quarter_rows": fmt_count(int(native.get("PT15M") or native.get("quarter_hour") or 0)),
        "hourly_repeated": bool(prices.get("hourly_values_repeated")),
        "min": prices.get("selected_min_eur_mwh"),
        "max": prices.get("selected_max_eur_mwh"),
        "mean": prices.get("selected_mean_eur_mwh"),
    }
    return facts


def time_grouping_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    if "dynamic_injection" not in {key for key, _label in visible_cases(summary)}:
        return None
    return _cases_as_columns(
        summary,
        [
            (
                "Peak-window injection (MWh)",
                lambda case, _key: EM_DASH
                if is_missing(nested_value(case, "export_peak_mwh"))
                else f"{float(nested_value(case, 'export_peak_mwh')):,.2f}",
            ),
            (
                "Off-peak-window injection (MWh)",
                lambda case, _key: EM_DASH
                if is_missing(nested_value(case, "export_offpeak_mwh"))
                else f"{float(nested_value(case, 'export_offpeak_mwh')):,.2f}",
            ),
        ],
    )


def load_comparison_display(
    folder: Path | str,
    *,
    site: str = "",
    source: str = RESULTS_SOURCE_LIVE,
) -> ComparisonDisplay:
    summary, monthly, peaks, metadata, _validation = load_comparison_files(folder)
    del metadata
    cases = visible_cases(summary)
    economics = has_economics(summary)
    warning = partial_period_warning(summary)
    header = _header(summary, site=site, source=source)
    groups = [
        (OVERVIEW_GROUPS[0], site_totals_rows(summary)),
        (OVERVIEW_GROUPS[1], overview_energy_rows(summary)),
        (OVERVIEW_GROUPS[2], overview_revenue_rows(summary)),
        (OVERVIEW_GROUPS[3], overview_peaks_rows(summary)),
        (OVERVIEW_GROUPS[4], overview_battery_rows(summary)),
    ]
    no_batt = summary["scenarios"]["no_battery"]
    energy_default = default_strategy(cases, "self_consumption")
    peaks_default = default_strategy(cases, "peak_reduction")
    revenue_default = default_strategy(cases, "revenue")
    explorer_default = default_strategy(optimized_cases(summary) or cases, "self_consumption")
    cost = None
    capex = None
    econ = summary.get("economics") if isinstance(summary.get("economics"), Mapping) else {}
    if economics:
        cost = econ.get("estimated_battery_cost_eur_per_kwh")
        capex = econ.get("estimated_battery_capex_eur")
    return ComparisonDisplay(
        folder=str(Path(folder)),
        cache_key=display_cache_key(folder),
        header=header,
        cases=cases,
        overview={
            "financial_caption": FINANCIAL_CAPTION,
            "method_caption": METHOD_CAPTION,
            "partial_warning": warning,
            "highlight_columns": HIGHLIGHT_COLUMNS,
            "highlight_rows": highlight_rows(summary),
            "groups": groups,
            "payback_definition": payback_definition(summary),
        },
        energy={
            "pv_production": fmt_mwh(no_batt["total_pv_production_kwh"]),
            "site_load": fmt_mwh(no_batt["site_load_kwh"]),
            "useful_before": fmt_mwh(no_batt["useful_pv_delivered_kwh"]),
            "pv_use": pv_use_rows(summary),
            "grid_energy": grid_energy_rows(summary),
            "default_strategy": energy_default,
            "import_injection_caption": (
                "Grid electricity imported and energy injected into the grid stay separate. "
                "They are not netted."
            ),
        },
        peaks={
            "definition": PEAK_DEFINITION,
            "complete_months": PEAK_COMPLETE_MONTHS,
            "financial_note": PEAK_FINANCIAL_NOTE,
            "compact": compact_peak_rows(summary),
            "all_cases_chart": peaks_chart_model(summary, peaks),
            "default_strategy": peaks_default,
        },
        revenue={
            "limitation": REVENUE_LIMITATION,
            "dynamic_note": DYNAMIC_BASELINE_NOTE if "dynamic_injection" in dict(cases) else None,
            "comparison": revenue_comparison_rows(summary),
            "detail": revenue_detail_rows(summary),
            "cost_text": None
            if not economics
            else (
                (f"Estimated battery cost: EUR {float(cost):g}/kWh usable capacity. " if cost is not None else "")
                + (f"Estimated battery CAPEX: {fmt_eur(float(capex))}." if capex is not None else "")
            ).strip()
            or None,
            "historical_cost": None if economics else HISTORICAL_NA,
            "payback_rows": cost_payback_rows(summary),
            "payback_definition": payback_definition(summary),
            "default_strategy": revenue_default,
        },
        technical={
            "schema_version": summary.get("artifact_schema_version"),
            "battery": technical_battery_rows(summary),
            "solvers": solver_rows(summary),
            "has_solver_records": any(
                isinstance((summary.get("solvers") or {}).get(key), Mapping)
                for key, _label in optimized_cases(summary)
            ),
            "da_prices": da_price_facts(summary),
            "time_groupings": time_grouping_rows(summary),
        },
        downloads={
            "demo": source == RESULTS_SOURCE_DEMO,
            "folder_name": Path(folder).name,
        },
        explorer={
            "default_strategy": explorer_default,
            "has_seasonal": bool(
                isinstance(summary.get("seasonal_plots"), Mapping)
                and (summary.get("seasonal_plots") or {}).get("included")
            ),
            "period_start_local": str((summary.get("selected_period") or {}).get("start_local") or ""),
            "period_end_local": str((summary.get("selected_period") or {}).get("end_local_exclusive") or ""),
        },
        monthly=monthly,
        monthly_peaks=peaks,
        summary=summary,
        notes=header["notes"],
    )
