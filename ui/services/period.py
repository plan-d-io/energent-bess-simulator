"""Period choice, acknowledgement facts and Step 3 continue gating."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ui.services.check_files import KIND_LABELS, dst_transition_rows, snapshot_is_renderable
from ui.services.uploads import format_row_count

SITE_BOUNDARY_CODES = frozenset({"NEGATIVE_LOAD", "EXPORT_EXCEEDS_PV"})
INSPECT_DURATIONS = (2.0, 4.0)

REASON_STALE = "Return to Data verification and check the files again."
REASON_NO_PERIOD = "Select a valid simulation period."
REASON_INSPECTION = "Wait until the selected period has been checked."
REASON_UNVALIDATED = "Acknowledge the unvalidated readings."
REASON_BOUNDARY = "Acknowledge the meter-boundary mismatch."
REASON_OTHER = "Resolve the selected-period issues above."
REASON_DEMO = "The saved demo is not available."

UNVALIDATED_EMPTY_NOTE = "Only non-empty readings are used."
SITE_BOUNDARY_CHECKBOX = (
    "Continue using these readings as a known meter-boundary mismatch."
)
PARTIAL_BODY = "Results cover only the selected window."
PASSED_BODY = "The selected period is ready for configuration."


def valid_periods(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, Mapping):
        return []
    items: list[dict[str, Any]] = []
    for item in snapshot.get("periods") or []:
        if isinstance(item, Mapping) and item.get("id"):
            items.append(dict(item))
    return items


def complete_year_periods(periods: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in periods if item.get("complete_calendar_year")]


def ordered_period_ids(periods: Sequence[Mapping[str, Any]]) -> list[str]:
    years = complete_year_periods(periods)
    year_ids = {str(item["id"]) for item in years}
    complete_ids = [
        str(item["id"])
        for item in sorted(years, key=lambda item: str(item.get("id") or ""), reverse=True)
    ]
    rest = [str(item["id"]) for item in periods if str(item.get("id")) not in year_ids]
    return complete_ids + rest


def ordered_periods(periods: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): dict(item) for item in periods if item.get("id")}
    return [by_id[period_id] for period_id in ordered_period_ids(periods) if period_id in by_id]


def default_period_id(periods: Sequence[Mapping[str, Any]]) -> str | None:
    years = complete_year_periods(periods)
    if years:
        latest = sorted(years, key=lambda item: str(item.get("id") or ""), reverse=True)
        return str(latest[0]["id"])
    if not periods:
        return None
    return str(periods[0]["id"])


def period_by_id(periods: Sequence[Mapping[str, Any]], period_id: str | None) -> dict[str, Any] | None:
    if period_id is None:
        return None
    wanted = str(period_id)
    for item in periods:
        if str(item.get("id")) == wanted:
            return dict(item)
    return None


def _kind_label(period: Mapping[str, Any]) -> str:
    kind = str(period.get("kind") or "")
    return KIND_LABELS.get(kind, kind or "Period")


def _option_type_fragment(period: Mapping[str, Any]) -> str:
    if period.get("complete_calendar_year") or period.get("kind") == "full_calendar_year":
        return "complete year"
    return _kind_label(period).lower()


def period_option_label(period: Mapping[str, Any]) -> str:
    label = str(period.get("label") or period.get("id") or "Period")
    parts = [label, _option_type_fragment(period)]
    n_intervals = period.get("n_intervals")
    if n_intervals is not None:
        parts.append(f"{format_row_count(int(n_intervals))} quarter-hours")
    n_unvalidated = int(period.get("n_unvalidated") or 0)
    if n_unvalidated:
        parts.append(f"{format_row_count(n_unvalidated)} unvalidated")
    return " · ".join(parts)


def classification_line(period: Mapping[str, Any]) -> str:
    count = format_row_count(int(period.get("n_intervals") or 0))
    if period.get("complete_calendar_year") or period.get("kind") == "full_calendar_year":
        return f"Complete calendar year · {count} quarter-hours"
    return f"{_kind_label(period)} · {count} quarter-hours"


def is_complete_year(period: Mapping[str, Any] | None) -> bool:
    if not period:
        return False
    return bool(period.get("complete_calendar_year") or period.get("kind") == "full_calendar_year")


def local_date_key(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def unvalidated_dates_in_period(
    period: Mapping[str, Any] | None,
    dates: Sequence[Any] | None,
) -> tuple[str, ...]:
    if not dates:
        return ()
    start = local_date_key((period or {}).get("start_local"))
    end = local_date_key((period or {}).get("end_local_exclusive"))
    found: list[str] = []
    seen: set[str] = set()
    for item in dates:
        day = local_date_key(item)
        if not day or day in seen:
            continue
        if start and day < start:
            continue
        if end and day >= end:
            continue
        seen.add(day)
        found.append(day)
    return tuple(found)


def unvalidated_dates_from_inspection(
    period: Mapping[str, Any] | None,
    inspection: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    report = (inspection or {}).get("report") if isinstance(inspection, Mapping) else None
    policy = report.get("unvalidated_policy") if isinstance(report, Mapping) else None
    dates = policy.get("dates") if isinstance(policy, Mapping) else None
    return unvalidated_dates_in_period(period, dates if isinstance(dates, list) else None)


def unvalidated_checkbox_label(count: int, dates: Sequence[str]) -> str:
    date_text = f" on {', '.join(dates)}" if dates else ""
    return (
        f"Use {format_row_count(int(count))} unvalidated readings{date_text}. "
        f"{UNVALIDATED_EMPTY_NOTE}"
    )


def unvalidated_warning_title(count: int) -> str:
    return f"Data contains {format_row_count(int(count))} unvalidated quarter-hours"


def unvalidated_detail(dates: Sequence[str]) -> str:
    lines: list[str] = []
    if len(dates) == 1:
        lines.append(f"Affected local date: {dates[0]}")
    elif dates:
        lines.append("Affected local dates: " + ", ".join(dates))
    lines.append(UNVALIDATED_EMPTY_NOTE)
    return "\n\n".join(lines)


def _issue_groups(inspection: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(inspection, Mapping):
        return []
    items: list[Mapping[str, Any]] = []
    for group in ("fatal", "warnings"):
        for item in inspection.get(group) or []:
            if isinstance(item, Mapping):
                items.append(item)
    return items


def issue_by_code(inspection: Mapping[str, Any] | None, code: str) -> Mapping[str, Any] | None:
    for item in _issue_groups(inspection):
        if item.get("code") == code:
            return item
    return None


def site_boundary_codes_present(inspection: Mapping[str, Any] | None) -> bool:
    return any(str(item.get("code") or "") in SITE_BOUNDARY_CODES for item in _issue_groups(inspection))


def requires_site_boundary_ack(inspection: Mapping[str, Any] | None) -> bool:
    if not isinstance(inspection, Mapping):
        return False
    return bool(inspection.get("requires_site_boundary_acknowledgement"))


def site_boundary_was_acknowledged(inspection: Mapping[str, Any] | None) -> bool:
    for item in _issue_groups(inspection):
        if str(item.get("code") or "") not in SITE_BOUNDARY_CODES:
            continue
        details = item.get("details") or {}
        if isinstance(details, Mapping) and details.get("acknowledged_site_boundary"):
            return True
    return False


def non_acknowledgeable_fatals(inspection: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(inspection, Mapping):
        return ()
    if requires_site_boundary_ack(inspection):
        return ()
    found: list[dict[str, Any]] = []
    for item in inspection.get("fatal") or []:
        if isinstance(item, Mapping):
            found.append(dict(item))
    return tuple(found)


def show_meter_boundary_panel(inspection: Mapping[str, Any] | None) -> bool:
    if non_acknowledgeable_fatals(inspection):
        return False
    return requires_site_boundary_ack(inspection) or site_boundary_was_acknowledged(inspection)


def needs_meter_boundary_ack(inspection: Mapping[str, Any] | None) -> bool:
    return show_meter_boundary_panel(inspection) and not site_boundary_was_acknowledged(inspection)


def message_cannot_create_ack(inspection: Mapping[str, Any] | None) -> bool:
    """True when only message text would suggest a boundary issue, not structured codes."""
    if not isinstance(inspection, Mapping):
        return False
    if requires_site_boundary_ack(inspection) or site_boundary_codes_present(inspection):
        return False
    return True


def _fmt_kwh(value: Any) -> str:
    number = abs(float(value))
    text = f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{text} kWh"


def meter_boundary_facts(inspection: Mapping[str, Any] | None) -> tuple[str, ...]:
    lines: list[str] = []
    negative = issue_by_code(inspection, "NEGATIVE_LOAD")
    export = issue_by_code(inspection, "EXPORT_EXCEEDS_PV")
    if negative:
        details = negative.get("details") or {}
        count = details.get("count")
        if count is not None:
            lines.append(f"Affected quarter-hours: {format_row_count(int(count))}")
        total = details.get("total_negative_load_kwh")
        if total is not None:
            lines.append(f"Total energy difference: {_fmt_kwh(total)}")
        min_kwh = details.get("min_kwh")
        if min_kwh is not None:
            lines.append(f"Largest interval difference: {_fmt_kwh(min_kwh)}")
        lines.extend(_timestamp_lines(details))
    if export:
        details = export.get("details") or {}
        count = details.get("count")
        if count is not None:
            lines.append(
                f"Quarter-hours where export exceeds measured PV: {format_row_count(int(count))}"
            )
        max_excess = details.get("max_excess_kwh")
        if max_excess is not None:
            lines.append(f"Maximum interval excess: {_fmt_kwh(max_excess)}")
        if negative is None:
            lines.extend(_timestamp_lines(details))
    return tuple(lines)


def meter_boundary_detail(inspection: Mapping[str, Any] | None) -> str:
    return "\n\n".join(meter_boundary_facts(inspection))


def _timestamp_lines(details: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    first = details.get("first_local_timestamp")
    last = details.get("last_local_timestamp")
    if first or last:
        lines.append(f"From {first or '—'} to {last or '—'}")
    dates = [str(item) for item in (details.get("affected_local_dates") or [])]
    if dates:
        if len(dates) <= 8:
            lines.append("Affected dates: " + ", ".join(dates))
        else:
            lines.append(f"Affected dates: {dates[0]} to {dates[-1]} ({len(dates)} dates)")
    examples = [str(item) for item in (details.get("examples") or [])[:5]]
    if examples:
        lines.append("Examples: " + ", ".join(examples))
    return lines


def simultaneous_diagnostic(inspection: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(inspection, Mapping):
        return None
    report = inspection.get("report") or {}
    payload = report.get("simultaneous_import_export") if isinstance(report, Mapping) else None
    if isinstance(payload, Mapping) and payload:
        return payload
    return None


def inspection_belongs_to_period(inspection: Mapping[str, Any] | None, period_id: str | None) -> bool:
    if not inspection or not period_id:
        return False
    return str(inspection.get("period_id") or "") == str(period_id)


def inspection_ok(inspection: Mapping[str, Any] | None) -> bool:
    if not isinstance(inspection, Mapping):
        return False
    return bool(inspection.get("ok")) and inspection.get("site_analysis") is not None


def discovery_allow_unvalidated(period: Mapping[str, Any] | None) -> bool:
    return int((period or {}).get("n_unvalidated") or 0) > 0


def final_allow_unvalidated(period: Mapping[str, Any] | None, unvalidated_ack: bool) -> bool:
    n_unvalidated = int((period or {}).get("n_unvalidated") or 0)
    return n_unvalidated == 0 or bool(unvalidated_ack)


def period_detail_rows(
    period: Mapping[str, Any] | None,
    inspection: Mapping[str, Any] | None = None,
) -> tuple[dict[str, str], ...]:
    if not period:
        return ()
    rows = [
        {
            "Start (local)": str(period.get("start_local") or "—"),
            "End (local, exclusive)": str(period.get("end_local_exclusive") or "—"),
            "Unvalidated": format_row_count(int(period.get("n_unvalidated") or 0)),
        }
    ]
    return tuple(rows)


def selected_dst_rows(inspection: Mapping[str, Any] | None) -> tuple[dict[str, str], ...]:
    if not isinstance(inspection, Mapping):
        return ()
    report = inspection.get("report") or {}
    dst = report.get("dst") if isinstance(report, Mapping) else None
    if not isinstance(dst, Mapping):
        return ()
    return dst_transition_rows({"dst": dst})


def snapshot_is_stale(snapshot: Mapping[str, Any] | None) -> bool:
    return not snapshot_is_renderable(snapshot)


def continue_disabled_reason(
    *,
    stale: bool,
    demo_blocked: bool,
    selected: Mapping[str, Any] | None,
    inspection: Mapping[str, Any] | None,
    inspection_running: bool,
    needs_unvalidated: bool,
    unvalidated_ack: bool,
    needs_boundary: bool,
    boundary_ack: bool,
    inspection_usable: bool,
) -> str | None:
    if stale or demo_blocked:
        return REASON_DEMO if demo_blocked else REASON_STALE
    if selected is None:
        return REASON_NO_PERIOD
    if inspection_running or inspection is None:
        return REASON_INSPECTION
    if isinstance(inspection, Mapping) and inspection.get("error"):
        return REASON_OTHER
    if needs_unvalidated and not unvalidated_ack:
        return REASON_UNVALIDATED
    if needs_boundary and not boundary_ack:
        return REASON_BOUNDARY
    if non_acknowledgeable_fatals(inspection) or not inspection_usable:
        return REASON_OTHER
    return None
