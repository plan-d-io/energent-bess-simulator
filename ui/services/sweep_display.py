"""Read-only battery-size display model. No solvers or session state."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from ui.services.compare_format import (
    SWEEP_SOLVER_PROVENANCE_UNAVAILABLE,
    solver_provenance_line,
)
from ui.services.paths import KIND_SWEEP
from ui.services.results import SOURCE_DEMO as RESULTS_SOURCE_DEMO
from ui.services.results import SOURCE_LIVE as RESULTS_SOURCE_LIVE
from ui.services.results import results_are_valid
from ui.services.sweep_format import (
    AVERAGE_MONTHLY_PEAK_DEFINITION,
    CANDIDATE_TABLE_COLUMNS,
    CAPTURE_MARKER_NOTE,
    CYCLE_LIMIT_EXPLANATION,
    DISPATCH_STRATEGY,
    DURATION_TABLE_COLUMNS,
    FLAG_GLOSSARY,
    HIGHEST_REVENUE_METRIC,
    HISTORICAL_NA,
    HISTORICAL_PEAK_CHART_NOTE,
    HISTORICAL_PEAK_NOTE,
    HISTORICAL_SCREENING_NOTE,
    LARGEST_PEAK_METRIC,
    NO_POSITIVE_HEADLINE,
    NO_POSITIVE_PEAK_NOTE,
    NOT_APPLICABLE,
    OUTCOME_NO_POSITIVE,
    OUTCOME_WITHIN,
    PAGE_TITLE,
    PARTIAL_PERIOD_WARNING,
    PEAK_COBENEFIT_UNAVAILABLE,
    PEAK_EXPLANATION_FALLBACK,
    PEAK_NOT_IN_REVENUE_NOTE,
    PEAK_TABLE_COLUMNS,
    REVENUE_PHRASE,
    REVENUE_PHRASE_PARTIAL,
    SHORTEST_PAYBACK_METRIC,
    SOURCE_DEMO,
    SOURCE_LIVE,
    ZERO_COMPLETE_MONTH_NOTE,
    battery_spec,
    candidate_selector_label,
    duration_label,
    fmt_cycles,
    fmt_eur_plain,
    fmt_eur_year,
    fmt_kw,
    fmt_payback_years,
    fmt_pct_value,
    is_missing,
    payback_is_applicable,
)

REQUIRED_DISPLAY_FILES = (
    "sweep_summary.json",
    "sweep_summary.csv",
    "sweep_metadata.json",
)
REQUIRED_CANDIDATE_KEYS = ("candidate_id", "power_kw", "usable_energy_kwh", "duration_hours")
CSV_IDENTITY = "candidate_id"


class SweepDisplayError(Exception):
    """Required sweep display files could not be read as one result."""


@dataclass(frozen=True)
class SweepDisplay:
    folder: str
    cache_key: tuple[Any, ...]
    header: dict[str, Any]
    overview: dict[str, Any]
    revenue: dict[str, Any]
    peaks: dict[str, Any]
    battery_use: dict[str, Any]
    sizes: dict[str, Any]
    transfer: dict[str, Any]
    downloads: dict[str, Any]
    notes: tuple[str, ...] = field(default_factory=tuple)
    summary: dict[str, Any] = field(default_factory=dict)
    candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)


_CACHE: dict[tuple[Any, ...], SweepDisplay] = {}


def display_cache_key(folder: Path | str) -> tuple[Any, ...]:
    root = Path(folder)
    parts: list[Any] = [str(root.resolve())]
    for name in REQUIRED_DISPLAY_FILES:
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


def sweep_display_guard(results: Mapping[str, Any] | None) -> str | None:
    if not results_are_valid(results):
        return "invalid"
    assert results is not None
    if str(results.get("kind") or "") != KIND_SWEEP:
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


def load_sweep_display(
    folder: Path | str,
    *,
    site: str = "",
    source: str = RESULTS_SOURCE_LIVE,
) -> SweepDisplay:
    root = Path(folder)
    identity = display_cache_key(root) + (str(site), str(source))
    cached = _CACHE.get(identity)
    if cached is not None:
        return copy.deepcopy(cached)
    model = _load_uncached(root, site=site, source=source, identity=identity)
    _CACHE[identity] = model
    return copy.deepcopy(model)


def clear_sweep_display_cache() -> None:
    _CACHE.clear()


def _load_uncached(
    root: Path,
    *,
    site: str,
    source: str,
    identity: tuple[Any, ...],
) -> SweepDisplay:
    for name in REQUIRED_DISPLAY_FILES:
        if not (root / name).is_file():
            raise SweepDisplayError(f"Missing required file {name}.")
    summary = _read_json(root / "sweep_summary.json")
    metadata = _read_json(root / "sweep_metadata.json")
    del metadata
    table = _read_csv(root / "sweep_summary.csv")
    candidates = _validate_candidates(summary, table)
    summary = dict(summary)
    summary["candidates"] = [dict(item) for item in candidates]
    demo = source == RESULTS_SOURCE_DEMO
    header = _header(summary, site=site, demo=demo)
    notes = []
    warning = partial_period_warning(summary)
    if warning:
        notes.append(warning)
    return SweepDisplay(
        folder=str(root),
        cache_key=identity,
        header=header,
        overview=_overview(summary),
        revenue=_revenue(summary),
        peaks=_peaks(summary),
        battery_use=_battery_use(summary),
        sizes=_sizes(summary),
        transfer=_transfer(summary),
        downloads={
            "demo": demo,
            "folder_name": root.name,
        },
        notes=tuple(notes),
        summary=copy.deepcopy(summary),
        candidates=tuple(copy.deepcopy(item) for item in candidates),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SweepDisplayError(f"Could not read {path.name}.") from exc
    if not isinstance(payload, dict):
        raise SweepDisplayError(f"{path.name} is not a mapping.")
    return payload


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
        raise SweepDisplayError(f"Could not read {path.name}.") from exc
    if not isinstance(frame, pd.DataFrame):
        raise SweepDisplayError(f"{path.name} is not a table.")
    return frame.copy()


def _validate_candidates(summary: Mapping[str, Any], table: pd.DataFrame) -> list[dict[str, Any]]:
    raw = summary.get("candidates")
    if not isinstance(raw, list) or not raw:
        raise SweepDisplayError("sweep_summary.json is missing candidates.")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise SweepDisplayError("A candidate row is not a mapping.")
        missing = [key for key in REQUIRED_CANDIDATE_KEYS if key not in item]
        if missing:
            raise SweepDisplayError(f"A candidate is missing {', '.join(missing)}.")
        cid = str(item.get("candidate_id") or "").strip()
        if not cid:
            raise SweepDisplayError("A candidate is missing candidate_id.")
        if cid in seen:
            raise SweepDisplayError("Duplicate candidate ids.")
        seen.add(cid)
        rows.append(dict(item))
    expected = summary.get("n_candidates")
    if expected is not None and int(expected) != len(rows):
        raise SweepDisplayError("Candidate count does not match n_candidates.")
    if CSV_IDENTITY not in table.columns:
        raise SweepDisplayError("sweep_summary.csv is missing candidate_id.")
    csv_ids = [str(value) for value in table[CSV_IDENTITY].tolist()]
    json_ids = [str(item["candidate_id"]) for item in rows]
    if csv_ids != json_ids:
        raise SweepDisplayError("JSON and CSV candidate order do not match.")
    return rows


def screening_summary(summary: Mapping[str, Any] | None) -> dict[str, Any] | None:
    payload = (summary or {}).get("screening_summary")
    return dict(payload) if isinstance(payload, Mapping) else None


def has_screening_summary(summary: Mapping[str, Any] | None) -> bool:
    screening = screening_summary(summary)
    return bool(screening) and "screening_outcome" in screening


def peak_summary(summary: Mapping[str, Any] | None) -> dict[str, Any] | None:
    payload = (summary or {}).get("peak_summary")
    return dict(payload) if isinstance(payload, Mapping) else None


def has_peak_summary(summary: Mapping[str, Any] | None) -> bool:
    return peak_summary(summary) is not None


def average_monthly_peak_available(summary: Mapping[str, Any] | None) -> bool:
    peaks = peak_summary(summary) or {}
    if "average_monthly_peak_available" in peaks:
        return bool(peaks.get("average_monthly_peak_available"))
    months = peaks.get("average_monthly_peak_n_complete_months")
    return months is not None and int(months) > 0


def complete_month_count(summary: Mapping[str, Any] | None) -> int | None:
    peaks = peak_summary(summary) or {}
    if peaks.get("average_monthly_peak_n_complete_months") is not None:
        return int(peaks["average_monthly_peak_n_complete_months"])
    return None


def is_partial_sweep_period(summary: Mapping[str, Any] | None) -> bool:
    payload = summary or {}
    if payload.get("annualized_from_partial_period"):
        return True
    period = payload.get("period") or {}
    return not bool(period.get("complete_calendar_year", True))


def revenue_increase_phrase(summary: Mapping[str, Any] | None) -> str:
    if is_partial_sweep_period(summary):
        return REVENUE_PHRASE_PARTIAL
    return REVENUE_PHRASE


def partial_period_warning(summary: Mapping[str, Any] | None) -> str | None:
    payload = summary or {}
    stored = payload.get("partial_period_warning")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    if is_partial_sweep_period(payload):
        return PARTIAL_PERIOD_WARNING
    return None


def peak_explanation(summary: Mapping[str, Any] | None) -> str:
    peaks = peak_summary(summary) or {}
    stored = peaks.get("explanation")
    if isinstance(stored, str) and stored.strip():
        return stored
    return PEAK_EXPLANATION_FALLBACK


def peak_label(summary: Mapping[str, Any] | None, key: str, fallback: str) -> str:
    peaks = peak_summary(summary) or {}
    labels = peaks.get("labels") or {}
    stored = labels.get(key)
    return stored if isinstance(stored, str) and stored.strip() else fallback


def lookup_candidate(summary: Mapping[str, Any], candidate_id: str | None) -> dict[str, Any] | None:
    if not candidate_id:
        return None
    for item in summary.get("candidates") or []:
        if str(item.get("candidate_id")) == str(candidate_id):
            return dict(item)
    return None


def capture_candidate_ids(summary: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("revenue_capture_candidate_id"))
        for item in summary.get("best_per_duration") or []
        if item.get("revenue_capture_candidate_id")
    }


def boundary_candidate_ids(summary: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for entry in summary.get("best_per_duration") or []:
        if not entry.get("range_boundary_reached"):
            continue
        largest = float(entry.get("largest_tested_power_kw") or 0.0)
        duration = float(entry["duration_hours"])
        for row in summary.get("candidates") or []:
            if abs(float(row["duration_hours"]) - duration) > 1e-9:
                continue
            if abs(float(row["power_kw"]) - largest) <= 1e-9:
                ids.add(str(row["candidate_id"]))
    return ids


def table_flag_labels(summary: Mapping[str, Any]) -> list[str]:
    capture_ids = capture_candidate_ids(summary)
    boundary_ids = boundary_candidate_ids(summary)
    flags: list[str] = []
    for row in summary.get("candidates") or []:
        marks: list[str] = []
        if row.get("cycle_limit_binding"):
            marks.append("Cycle-limited")
        if str(row.get("candidate_id")) in capture_ids:
            marks.append("Revenue-capture")
        if str(row.get("candidate_id")) in boundary_ids:
            marks.append("Range-boundary")
        flags.append(", ".join(marks) if marks else "None")
    return flags


def screening_headline(summary: Mapping[str, Any]) -> str:
    screening = screening_summary(summary)
    if not screening:
        return HISTORICAL_SCREENING_NOTE
    outcome = str(screening.get("screening_outcome") or "")
    years = screening.get("screening_period_years")
    years_text = f"{float(years):g}" if years is not None else ""
    shortest = screening.get("shortest_payback_candidate") or {}
    payback = fmt_payback_years(shortest.get("simple_payback_years")) if shortest else None
    if outcome == OUTCOME_NO_POSITIVE:
        stored = screening.get("screening_outcome_label")
        if isinstance(stored, str) and stored.strip():
            if "therefore not available" in stored.lower():
                return stored
            return stored.rstrip(".") + ". Simple payback is therefore not available."
        return NO_POSITIVE_HEADLINE
    if outcome == OUTCOME_WITHIN:
        count = int(screening.get("candidates_with_payback_within_screening_period_count") or 0)
        sentence = (
            f"{count} tested battery sizes pay back within the configured "
            f"{years_text}-year screening period."
        )
        if payback and payback != NOT_APPLICABLE:
            sentence += f" The shortest tested payback is {payback}."
        return sentence
    sentence = (
        f"No tested battery pays back within the configured {years_text}-year screening period."
    )
    if payback and payback != NOT_APPLICABLE:
        sentence += f" The shortest tested payback is {payback}."
    return sentence


def screening_period_years(summary: Mapping[str, Any]) -> float | None:
    screening = screening_summary(summary) or {}
    if screening.get("screening_period_years") is not None:
        return float(screening["screening_period_years"])
    sweep = summary.get("sweep") or {}
    if sweep.get("evaluation_period_years") is not None:
        return float(sweep["evaluation_period_years"])
    return None


def shortest_payback_id(summary: Mapping[str, Any]) -> str | None:
    screening = screening_summary(summary) or {}
    snap = screening.get("shortest_payback_candidate") or {}
    if snap.get("candidate_id"):
        return str(snap["candidate_id"])
    return None


def allowed_cycles(summary: Mapping[str, Any]) -> float | None:
    template = summary.get("battery_template") or {}
    if template.get("max_equivalent_full_cycles_per_year") is not None:
        return float(template["max_equivalent_full_cycles_per_year"])
    candidates = summary.get("candidates") or []
    if candidates and candidates[0].get("allowed_equivalent_full_cycles") is not None:
        return float(candidates[0]["allowed_equivalent_full_cycles"])
    return None


def largest_monthly_peak_id(summary: Mapping[str, Any]) -> str | None:
    peaks = peak_summary(summary) or {}
    snap = peaks.get("largest_average_monthly_peak_reduction_candidate") or {}
    if isinstance(snap, Mapping) and snap.get("candidate_id"):
        return str(snap["candidate_id"])
    return None


def largest_interval_peak_id(summary: Mapping[str, Any]) -> str | None:
    peaks = peak_summary(summary) or {}
    snap = peaks.get("largest_highest_interval_peak_reduction_candidate") or {}
    if isinstance(snap, Mapping) and snap.get("candidate_id"):
        return str(snap["candidate_id"])
    return None


def baseline_annual_peak_kw(summary: Mapping[str, Any]) -> float | None:
    peaks = peak_summary(summary) or {}
    if not is_missing(peaks.get("baseline_annual_peak_kw")):
        return float(peaks["baseline_annual_peak_kw"])
    return None


def default_transfer_candidate_id(summary: Mapping[str, Any]) -> str | None:
    screening = screening_summary(summary) or {}
    shortest = screening.get("shortest_payback_candidate") or {}
    if shortest.get("candidate_id"):
        return str(shortest["candidate_id"])
    highest = screening.get("highest_annual_revenue_candidate") or {}
    if highest.get("candidate_id"):
        return str(highest["candidate_id"])
    candidates = summary.get("candidates") or []
    if candidates:
        return str(candidates[0].get("candidate_id"))
    return None


def duration_hours_list(summary: Mapping[str, Any]) -> list[float]:
    hours = sorted(
        {
            float(item["duration_hours"])
            for item in summary.get("candidates") or []
            if item.get("duration_hours") is not None
        }
    )
    if hours:
        return hours
    sweep = summary.get("sweep") or {}
    return [float(item) for item in (sweep.get("default_durations_hours") or [])]


def assumptions_line(summary: Mapping[str, Any]) -> str:
    screening = screening_summary(summary) or {}
    sweep = summary.get("sweep") or {}
    count = screening.get("candidate_count") or summary.get("n_candidates") or 0
    hours = duration_hours_list(summary)
    duration_text = " and ".join(duration_label(item) for item in hours) or "—"
    cost = sweep.get("estimated_battery_cost_eur_per_kwh")
    years = screening.get("screening_period_years") or sweep.get("evaluation_period_years")
    template = summary.get("battery_template") or {}
    cycles = template.get("max_equivalent_full_cycles_per_year")
    if cycles is None:
        candidates = summary.get("candidates") or []
        if candidates:
            cycles = candidates[0].get("allowed_equivalent_full_cycles")
    cost_text = f"EUR {float(cost):g}/kWh usable" if cost is not None else "cost not recorded"
    years_text = (
        f"{float(years):g}-year screening period" if years is not None else "screening period not recorded"
    )
    cycle_text = (
        f"maximum {float(cycles):g} equivalent full cycles/year"
        if cycles is not None
        else "cycle allowance not recorded"
    )
    return f"{int(count)} sizes tested · {duration_text} · {cost_text} · {years_text} · {cycle_text}"


def cycle_support_line(row: Mapping[str, Any] | None) -> str:
    if not row:
        return NOT_APPLICABLE
    cycles = float(row.get("equivalent_full_cycles") or 0.0)
    reached = "cycle limit reached" if row.get("cycle_limit_binding") else "cycle limit not reached"
    return f"{cycles:,.1f} equivalent full cycles · {reached}"


def peak_cobenefit_line(row: Mapping[str, Any] | None) -> str:
    if not row or is_missing(row.get("average_monthly_peak_reduction_kw")):
        return PEAK_COBENEFIT_UNAVAILABLE
    reduction = fmt_kw(row.get("average_monthly_peak_reduction_kw"))
    percent = fmt_pct_value(row.get("average_monthly_peak_reduction_pct"))
    if percent == NOT_APPLICABLE:
        return f"Average monthly peak reduction: {reduction}"
    return f"Average monthly peak reduction: {reduction} ({percent})"


def _snapshot_lines(
    row: Mapping[str, Any] | None,
    revenue_label: str,
    *,
    include_payback: bool = False,
) -> list[str]:
    if not row:
        return []
    lines = [battery_spec(row)]
    if include_payback:
        lines.append(f"Simple payback: {fmt_payback_years(row.get('simple_payback_years'))}")
    else:
        lines.append(f"{revenue_label}: {fmt_eur_year(row.get('annual_revenue_uplift_eur'))}")
    lines.append(f"CAPEX: {fmt_eur_plain(row.get('estimated_capex_eur'))}")
    lines.append(cycle_support_line(row))
    return lines


def highlight_cards(summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    screening = screening_summary(summary) or {}
    outcome = str(screening.get("screening_outcome") or "")
    revenue_label = revenue_increase_phrase(summary)
    shortest = screening.get("shortest_payback_candidate")
    highest = screening.get("highest_annual_revenue_candidate")
    payback_applicable = outcome != OUTCOME_NO_POSITIVE and isinstance(shortest, Mapping)
    historical = not has_screening_summary(summary)
    payback = {
        "label": SHORTEST_PAYBACK_METRIC,
        "value": (
            HISTORICAL_NA
            if historical
            else (
                fmt_payback_years(shortest.get("simple_payback_years"))
                if payback_applicable
                else NOT_APPLICABLE
            )
        ),
        "lines": (
            [HISTORICAL_SCREENING_NOTE]
            if historical
            else (
                _snapshot_lines(shortest, revenue_label)
                if payback_applicable
                else [screening_headline(summary)]
            )
        ),
    }
    revenue = {
        "label": HIGHEST_REVENUE_METRIC,
        "value": (
            HISTORICAL_NA
            if historical
            else (
                fmt_eur_year((highest or {}).get("annual_revenue_uplift_eur"))
                if highest
                else NOT_APPLICABLE
            )
        ),
        "lines": (
            [HISTORICAL_SCREENING_NOTE]
            if historical
            else (_snapshot_lines(highest, revenue_label, include_payback=True) if highest else [])
        ),
    }
    return (payback, revenue, _peak_highlight(summary, revenue_label))


def _peak_highlight(summary: Mapping[str, Any], revenue_label: str) -> dict[str, Any]:
    label = peak_label(summary, "largest_average_monthly_peak_reduction", LARGEST_PEAK_METRIC)
    if not has_peak_summary(summary):
        return {
            "label": label,
            "value": HISTORICAL_NA,
            "lines": [HISTORICAL_PEAK_NOTE],
        }
    snap = (peak_summary(summary) or {}).get("largest_average_monthly_peak_reduction_candidate")
    if not average_monthly_peak_available(summary) or complete_month_count(summary) == 0:
        return {
            "label": label,
            "value": NOT_APPLICABLE,
            "lines": [ZERO_COMPLETE_MONTH_NOTE],
        }
    if not isinstance(snap, Mapping):
        return {
            "label": label,
            "value": NOT_APPLICABLE,
            "lines": [NO_POSITIVE_PEAK_NOTE],
        }
    reduction = snap.get("average_monthly_peak_reduction_kw")
    percent = snap.get("average_monthly_peak_reduction_pct")
    lines = [
        battery_spec(snap),
        f"Average monthly peak after the battery: {fmt_kw(snap.get('average_monthly_peak_kw'))}",
        f"Baseline average monthly peak: {fmt_kw(snap.get('baseline_average_monthly_peak_kw'))}",
        f"{revenue_label}: {fmt_eur_year(snap.get('annual_revenue_uplift_eur'))}",
        f"Simple payback: {fmt_payback_years(snap.get('simple_payback_years'))}",
        PEAK_NOT_IN_REVENUE_NOTE,
    ]
    if not is_missing(percent):
        lines.insert(1, f"Reduction: {fmt_pct_value(percent)}")
    return {"label": label, "value": fmt_kw(reduction), "lines": lines}


def duration_comparison_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    historical = not has_screening_summary(summary)
    for entry in summary.get("best_per_duration") or []:
        shortest = entry.get("shortest_payback_candidate")
        if not isinstance(shortest, Mapping):
            shortest = None
        highest = lookup_candidate(summary, str(entry.get("highest_revenue_candidate_id") or "") or None)
        if historical and entry.get("shortest_payback_candidate_id") is None and shortest is None:
            payback_battery = HISTORICAL_NA
            payback_years = HISTORICAL_NA
        else:
            payback_battery = battery_spec(shortest)
            payback_years = fmt_payback_years(
                entry.get("shortest_simple_payback_years")
                or (shortest or {}).get("simple_payback_years"),
                with_unit=False,
            )
        revenue_value = entry.get("highest_annual_revenue_uplift_eur")
        if revenue_value is None and highest is not None:
            revenue_value = highest.get("annual_revenue_uplift_eur")
        rows.append(
            {
                "Duration": duration_label(entry["duration_hours"]),
                "Shortest-payback battery": payback_battery,
                "Shortest payback (years)": payback_years,
                "Highest-revenue battery": battery_spec(highest),
                "Highest annual revenue increase (EUR/year)": (
                    f"{float(revenue_value):,.0f}" if revenue_value is not None else NOT_APPLICABLE
                ),
            }
        )
    return rows


def candidate_display_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    flags = table_flag_labels(summary)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(summary.get("candidates") or []):
        payback = item.get("simple_payback_years")
        rows.append(
            {
                "Power (kW)": item.get("power_kw"),
                "Usable energy (kWh)": item.get("usable_energy_kwh"),
                "Duration (h)": item.get("duration_hours"),
                "Annual revenue increase (EUR)": (
                    round(float(item["annual_revenue_uplift_eur"]), 0)
                    if not is_missing(item.get("annual_revenue_uplift_eur"))
                    else NOT_APPLICABLE
                ),
                "Simple payback (years)": (
                    NOT_APPLICABLE if not payback_is_applicable(payback) else round(float(payback), 1)
                ),
                "Estimated battery CAPEX (EUR)": (
                    round(float(item["estimated_capex_eur"]), 0)
                    if not is_missing(item.get("estimated_capex_eur"))
                    else NOT_APPLICABLE
                ),
                "Equivalent full cycles": (
                    round(float(item["equivalent_full_cycles"]), 1)
                    if not is_missing(item.get("equivalent_full_cycles"))
                    else HISTORICAL_NA
                ),
                "Cycle limit reached": "Yes" if item.get("cycle_limit_binding") else "No",
                "Flags": flags[index] if index < len(flags) else "None",
            }
        )
    return rows


def extra_detail_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in summary.get("candidates") or []:
        rows.append(
            {
                "Power (kW)": item.get("power_kw"),
                "Usable energy (kWh)": item.get("usable_energy_kwh"),
                "Duration (h)": item.get("duration_hours"),
                "Useful PV (kWh)": _rounded(item.get("useful_pv_delivered_kwh"), 1),
                "Additional useful PV (kWh)": _rounded(item.get("additional_useful_pv_kwh"), 1),
                "Highest 15-minute grid import during the selected period (kW)": _rounded(
                    item.get("annual_peak_kw"), 1
                ),
                "Exceeds P95 daily PV surplus": item.get("exceeds_p95_daily_pv_surplus"),
                "Exceeds P95 daily import": item.get("exceeds_p95_daily_import"),
            }
        )
    return rows


def _candidate_solver_names_present(summary: Mapping[str, Any]) -> bool:
    return any(str(item.get("solver_name") or "").strip() for item in summary.get("candidates") or [])


def sweep_solver_provenance(summary: Mapping[str, Any]) -> dict[str, str | None]:
    solver = summary.get("solver") if isinstance(summary.get("solver"), Mapping) else None
    line = solver_provenance_line(solver)
    if line:
        return {"line": line, "unavailable_note": None}
    if _candidate_solver_names_present(summary):
        return {"line": None, "unavailable_note": None}
    return {"line": None, "unavailable_note": SWEEP_SOLVER_PROVENANCE_UNAVAILABLE}


def solver_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    include_solver = _candidate_solver_names_present(summary)
    rows: list[dict[str, Any]] = []
    for item in summary.get("candidates") or []:
        row: dict[str, Any] = {
            "Duration (h)": item.get("duration_hours"),
            "Power (kW)": item.get("power_kw"),
            "Usable energy (kWh)": item.get("usable_energy_kwh"),
        }
        if include_solver:
            row["Solver"] = item.get("solver_name") or HISTORICAL_NA
        row.update(
            {
                "Solver status": item.get("solver_status"),
                "Solver runtime (s)": item.get("solver_runtime_s"),
                "Feasible": item.get("feasibility_ok"),
                "Continuous LP": item.get("continuous_lp"),
                "Allowed equivalent full cycles": item.get("allowed_equivalent_full_cycles"),
                "Remaining cycle allowance": item.get("remaining_equivalent_full_cycles_allowance"),
            }
        )
        rows.append(row)
    return rows


def peak_display_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    months = complete_month_count(summary)
    zero_months = months == 0
    include_monthly = average_monthly_peak_available(summary) and not zero_months
    include_reductions = has_peak_summary(summary)
    has_abs_monthly = (not zero_months) and any(
        not is_missing(item.get("average_monthly_peak_kw")) for item in summary.get("candidates") or []
    )
    rows: list[dict[str, Any]] = []
    for item in summary.get("candidates") or []:
        row: dict[str, Any] = {
            "Power (kW)": item.get("power_kw"),
            "Usable energy (kWh)": item.get("usable_energy_kwh"),
            "Duration (h)": item.get("duration_hours"),
            "Highest 15-minute grid import (kW)": _rounded(item.get("annual_peak_kw"), 1),
        }
        if include_monthly or has_abs_monthly:
            row["Average monthly peak (kW)"] = _rounded(item.get("average_monthly_peak_kw"), 1)
            if include_reductions and include_monthly:
                row["Average monthly peak reduction (kW)"] = _rounded(
                    item.get("average_monthly_peak_reduction_kw"), 1
                )
                row["Average monthly peak reduction (%)"] = _rounded(
                    item.get("average_monthly_peak_reduction_pct"), 1
                )
        if include_reductions:
            row["Reduction in highest 15-minute grid import (kW)"] = _rounded(
                item.get("annual_peak_reduction_kw"), 1
            )
            row["Reduction in highest 15-minute grid import (%)"] = _rounded(
                item.get("annual_peak_reduction_pct"), 1
            )
        rows.append(row)
    ordered = [name for name in PEAK_TABLE_COLUMNS if rows and name in rows[0]]
    return [{key: row.get(key) for key in ordered} for row in rows]


def candidate_csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    frame = pd.DataFrame(list(rows), columns=list(CANDIDATE_TABLE_COLUMNS))
    return frame.to_csv(index=False).encode("utf-8")


def _rounded(value: Any, digits: int) -> float | None:
    if is_missing(value):
        return None
    return round(float(value), digits)


def _header(summary: Mapping[str, Any], *, site: str, demo: bool) -> dict[str, Any]:
    period = summary.get("period") if isinstance(summary.get("period"), Mapping) else {}
    hours = duration_hours_list(summary)
    count = int((screening_summary(summary) or {}).get("candidate_count") or summary.get("n_candidates") or 0)
    return {
        "title": PAGE_TITLE,
        "source_line": SOURCE_DEMO if demo else SOURCE_LIVE,
        "site": site,
        "period_label": str(period.get("label") or ""),
        "tested_sizes": str(count) if count else "—",
        "durations": " and ".join(duration_label(item) for item in hours) or "—",
        "strategy": DISPATCH_STRATEGY,
    }


def _overview(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "headline": screening_headline(summary),
        "highlights": highlight_cards(summary),
        "assumptions": assumptions_line(summary),
        "duration_columns": DURATION_TABLE_COLUMNS,
        "duration_rows": duration_comparison_rows(summary),
    }


def _revenue(summary: Mapping[str, Any]) -> dict[str, Any]:
    phrase = revenue_increase_phrase(summary)
    return {
        "partial_warning": partial_period_warning(summary),
        "payback_title": "Simple payback versus battery power",
        "payback_x": "Battery power (kW)",
        "payback_y": "Simple payback period (years)",
        "revenue_title": f"{phrase} versus battery power",
        "revenue_x": "Battery power (kW)",
        "revenue_y": f"{phrase} (EUR/year)",
        "capture_caption": CAPTURE_MARKER_NOTE,
        "phrase": phrase,
        "screening_years": screening_period_years(summary),
        "shortest_id": shortest_payback_id(summary),
        "capture_ids": sorted(capture_candidate_ids(summary)),
    }


def _peaks(summary: Mapping[str, Any]) -> dict[str, Any]:
    historical = not has_peak_summary(summary)
    months = complete_month_count(summary)
    zero_months = months == 0
    monthly_ok = (not historical) and average_monthly_peak_available(summary) and not zero_months
    notices: list[str] = []
    if historical:
        notices.append(HISTORICAL_PEAK_NOTE)
        notices.append(HISTORICAL_PEAK_CHART_NOTE)
    elif zero_months or not average_monthly_peak_available(summary):
        notices.append(ZERO_COMPLETE_MONTH_NOTE)
    return {
        "historical": historical,
        "explanation": None if historical else peak_explanation(summary),
        "definition": None if historical else AVERAGE_MONTHLY_PEAK_DEFINITION,
        "months": months,
        "monthly_ok": monthly_ok,
        "notices": notices,
        "monthly_title": "Average monthly peak reduction versus battery power",
        "monthly_x": "Battery power (kW)",
        "monthly_y": "Average monthly peak reduction (kW)",
        "interval_title": (
            "Highest 15-minute grid import during the selected period versus battery power"
        ),
        "interval_x": "Battery power (kW)",
        "interval_y": "Highest 15-minute grid import (kW)",
        "table_rows": peak_display_rows(summary),
        "monthly_id": largest_monthly_peak_id(summary),
        "interval_id": largest_interval_peak_id(summary),
        "baseline_interval": baseline_annual_peak_kw(summary),
    }


def _battery_use(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": "Equivalent full cycles versus battery power",
        "x_title": "Battery power (kW)",
        "y_title": "Equivalent full cycles",
        "explanation": CYCLE_LIMIT_EXPLANATION,
        "allowance": allowed_cycles(summary),
    }


def _sizes(summary: Mapping[str, Any]) -> dict[str, Any]:
    rows = candidate_display_rows(summary)
    return {
        "columns": CANDIDATE_TABLE_COLUMNS,
        "rows": rows,
        "csv_bytes": candidate_csv_bytes(rows),
        "glossary": FLAG_GLOSSARY,
        "extra_rows": extra_detail_rows(summary),
        "solver_provenance": sweep_solver_provenance(summary),
        "solver_rows": solver_rows(summary),
    }


def _transfer(summary: Mapping[str, Any]) -> dict[str, Any]:
    options: list[tuple[str, str]] = []
    for item in summary.get("candidates") or []:
        cid = str(item.get("candidate_id") or "")
        options.append((cid, candidate_selector_label(item, cid)))
    default = default_transfer_candidate_id(summary)
    if default not in {item[0] for item in options} and options:
        default = options[0][0]
    return {
        "options": options,
        "default": default,
        "labels": {cid: label for cid, label in options},
    }


def ui_text_blob(model: SweepDisplay) -> str:
    parts: list[str] = [
        model.header["title"],
        model.header["source_line"],
        model.header["strategy"],
        model.overview["headline"],
        model.overview["assumptions"],
        model.revenue["payback_title"],
        model.revenue["revenue_title"],
        model.revenue["capture_caption"],
        model.battery_use["title"],
        model.battery_use["explanation"],
    ]
    for card in model.overview["highlights"]:
        parts.append(card["label"])
        parts.append(str(card["value"]))
        parts.extend(card["lines"])
    for row in model.overview["duration_rows"]:
        parts.extend(str(value) for value in row.values())
    for row in model.sizes["rows"]:
        parts.extend(str(value) for value in row.values())
    for name, text in model.sizes["glossary"]:
        parts.append(name)
        parts.append(text)
    for notice in model.peaks["notices"]:
        parts.append(notice)
    if model.peaks.get("explanation"):
        parts.append(str(model.peaks["explanation"]))
    if model.peaks.get("definition"):
        parts.append(str(model.peaks["definition"]))
    return " ".join(parts)
