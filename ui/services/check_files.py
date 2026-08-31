"""Step 2 view-model over a serialisable ingest or saved-example snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ui.services.uploads import (
    ROLE_LABELS,
    ROLE_ORDER,
    blocking_panels,
    format_row_count,
)

DST_PENDING = "Selected-period clock-change detail is checked in the next step."
EAN_JOIN_NOTE = "The offtake, injection and PV series are joined by timestamp, not meter EAN."
SIMULTANEOUS_NOTE = (
    "Simultaneous grid import and export in one quarter-hour is informational. "
    "The directional series are kept separate."
)

REASON_STALE = "Return to Upload data and check the files again."
REASON_FATALS = "Resolve the blocking file errors above."
REASON_NO_PERIODS = "No usable simulation period was found."

KIND_LABELS = {
    "full_calendar_year": "Complete calendar year",
    "partial_calendar_year": "Partial calendar year",
    "rolling_twelve_months": "Rolling twelve-month window",
    "common_overlap": "Common overlap",
}
KIND_RANK = {
    "full_calendar_year": 0,
    "partial_calendar_year": 1,
    "rolling_twelve_months": 2,
    "common_overlap": 3,
}
DST_DIRECTION = {
    "spring_forward": "Forward",
    "autumn_backward": "Back",
}
_SKIP_WARNING_CODES = frozenset(
    {"UNUSED_REGISTERS", "PARTIAL_CALENDAR_YEARS", "UNVALIDATED_USED"}
)


@dataclass(frozen=True)
class CheckFilesModel:
    stale: bool
    usable: bool
    can_continue: bool
    disabled_reason: str | None
    fatal_panels: tuple[tuple[str, str], ...]
    role_rows: tuple[dict[str, str], ...]
    ignored_rows: tuple[dict[str, str], ...]
    coverage_metrics: tuple[tuple[str, str], ...]
    period_rows: tuple[dict[str, str], ...]
    period_details: tuple[dict[str, str], ...]
    no_periods: bool
    dst_converted: bool
    dst_transition_rows: tuple[dict[str, str], ...]
    dst_pending_detail: bool
    file_checks_passed: bool
    no_complete_year: bool
    check_detail_notes: tuple[str, ...]
    price_available: bool
    price_filename: str


def snapshot_is_renderable(snapshot: Mapping[str, Any] | None) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    error = snapshot.get("error")
    roles = snapshot.get("roles")
    issues = snapshot.get("issues")
    periods = snapshot.get("periods")
    if error and not roles and not issues and not periods:
        return False
    if not isinstance(roles, Mapping):
        return False
    complete = all(
        isinstance(roles.get(role), Mapping) and roles[role].get("register")
        for role in ROLE_ORDER
    )
    if complete:
        return True
    if isinstance(issues, list) and issues:
        return True
    return False


def fatal_issues(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for item in snapshot.get("issues") or []:
        if isinstance(item, Mapping) and item.get("severity") == "fatal":
            items.append(item)
    return items


def warning_issues(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for item in snapshot.get("issues") or []:
        if isinstance(item, Mapping) and item.get("severity") == "warning":
            items.append(item)
    return items


def common_overlap_period(periods: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for item in periods:
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") == "common_overlap" or str(item.get("id") or "") == "common":
            return item
    return None


def ordered_periods(periods: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    grouped: dict[int, list[Mapping[str, Any]]] = {0: [], 1: [], 2: [], 3: [], 4: []}
    for item in periods:
        if not isinstance(item, Mapping):
            continue
        rank = KIND_RANK.get(str(item.get("kind") or ""), 4)
        grouped[rank].append(item)
    ordered: list[Mapping[str, Any]] = []
    for rank in range(5):
        ordered.extend(grouped[rank])
    return ordered


def role_rows(snapshot: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    roles = snapshot.get("roles") or {}
    rows: list[dict[str, str]] = []
    for role in ROLE_ORDER:
        meta = roles.get(role) if isinstance(roles, Mapping) else None
        meta = meta if isinstance(meta, Mapping) else {}
        rows.append(
            {
                "Role": ROLE_LABELS[role],
                "Register": str(meta.get("register") or ""),
                "Unit": str(meta.get("unit") or ""),
                "Rows": format_row_count(int(meta.get("n_rows") or 0)),
            }
        )
    return tuple(rows)


def ignored_register_rows(snapshot: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in warning_issues(snapshot):
        if issue.get("code") != "UNUSED_REGISTERS":
            continue
        details = issue.get("details") or {}
        unused = details.get("unused") if isinstance(details, Mapping) else None
        if not isinstance(unused, list):
            continue
        for item in unused:
            if not isinstance(item, Mapping):
                continue
            register = str(item.get("register") or "")
            units = item.get("units") or []
            unit = ", ".join(str(part) for part in units) if isinstance(units, list) else str(units)
            count = format_row_count(int(item.get("n_rows") or 0))
            key = (register, unit, count)
            if not register or key in seen:
                continue
            seen.add(key)
            rows.append({"Register": register, "Unit": unit, "Rows": count})
    return tuple(rows)


def dst_transition_rows(snapshot: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    dst = snapshot.get("dst") if isinstance(snapshot.get("dst"), Mapping) else {}
    transitions = dst.get("transitions") if isinstance(dst, Mapping) else None
    if not isinstance(transitions, list):
        return ()
    rows: list[dict[str, str]] = []
    for item in transitions:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        rows.append(
            {
                "Local date": str(item.get("date_local") or ""),
                "Direction": DST_DIRECTION.get(kind, kind),
                "Quarter-hours in local day": format_row_count(
                    int(item.get("physical_quarter_hours_in_local_day") or 0)
                ),
            }
        )
    return tuple(rows)


def coverage_metrics(
    snapshot: Mapping[str, Any],
    periods: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    overlap = common_overlap_period(periods)
    n_fatal = len(fatal_issues(snapshot))
    fatal_value = format_row_count(n_fatal)
    if overlap is None:
        return (
            ("Quarter-hours in common coverage", "Not available"),
            ("Unvalidated in that coverage", "Not available"),
            ("Fatal errors", fatal_value),
        )
    return (
        ("Quarter-hours in common coverage", format_row_count(int(overlap.get("n_intervals") or 0))),
        ("Unvalidated in that coverage", format_row_count(int(overlap.get("n_unvalidated") or 0))),
        ("Fatal errors", fatal_value),
    )


def period_table_rows(periods: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for item in ordered_periods(periods):
        kind = str(item.get("kind") or "")
        rows.append(
            {
                "Period": str(item.get("label") or item.get("id") or ""),
                "Type": KIND_LABELS.get(kind, kind),
                "Quarter-hours": format_row_count(int(item.get("n_intervals") or 0)),
                "Unvalidated": format_row_count(int(item.get("n_unvalidated") or 0)),
            }
        )
    return tuple(rows)


def period_detail_rows(periods: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for item in ordered_periods(periods):
        start = str(item.get("start_local") or "")
        end = str(item.get("end_local_exclusive") or "")
        if not start and not end:
            continue
        rows.append(
            {
                "Period": str(item.get("label") or item.get("id") or ""),
                "Start (local)": start,
                "End (local, exclusive)": end,
            }
        )
    return tuple(rows)


def check_detail_notes(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    notes: list[str] = []
    seen: set[str] = set()

    def add(note: str) -> None:
        if note in seen:
            return
        seen.add(note)
        notes.append(note)

    for issue in warning_issues(snapshot):
        code = str(issue.get("code") or "")
        if code in _SKIP_WARNING_CODES or code == "NO_COMPLETE_CALENDAR_YEAR":
            continue
        if code == "EAN_MISMATCH":
            add(EAN_JOIN_NOTE)
    simultaneous = snapshot.get("simultaneous_import_export")
    if isinstance(simultaneous, Mapping) and simultaneous:
        add(SIMULTANEOUS_NOTE)
    return tuple(notes)


def has_no_complete_year(snapshot: Mapping[str, Any]) -> bool:
    return any(
        issue.get("code") == "NO_COMPLETE_CALENDAR_YEAR" for issue in warning_issues(snapshot)
    )


def step2_disabled_reason(model_like: Mapping[str, Any] | CheckFilesModel) -> str | None:
    if isinstance(model_like, CheckFilesModel):
        stale = model_like.stale
        fatals = bool(model_like.fatal_panels)
        no_periods = model_like.no_periods
    else:
        stale = bool(model_like.get("stale"))
        fatals = bool(model_like.get("fatal_panels"))
        no_periods = bool(model_like.get("no_periods"))
    if stale:
        return REASON_STALE
    if fatals:
        return REASON_FATALS
    if no_periods:
        return REASON_NO_PERIODS
    return None


def build_check_files_model(
    snapshot: Mapping[str, Any] | None,
    *,
    price_filename: str | None,
) -> CheckFilesModel:
    stale = not snapshot_is_renderable(snapshot)
    payload: Mapping[str, Any] = snapshot if isinstance(snapshot, Mapping) else {}
    fatals = fatal_issues(payload) if not stale else []
    fatal_panels = tuple(blocking_panels(payload)) if not stale else ()
    periods = [item for item in (payload.get("periods") or []) if isinstance(item, Mapping)]
    no_periods = not periods
    file_checks_passed = not stale and not fatals
    usable = not stale and not fatals and not no_periods
    disabled = REASON_STALE if stale else REASON_FATALS if fatals else REASON_NO_PERIODS if no_periods else None
    return CheckFilesModel(
        stale=stale,
        usable=usable,
        can_continue=usable,
        disabled_reason=disabled,
        fatal_panels=fatal_panels,
        role_rows=role_rows(payload) if not stale else (),
        ignored_rows=ignored_register_rows(payload) if not stale else (),
        coverage_metrics=coverage_metrics(payload, periods) if not stale else (),
        period_rows=period_table_rows(periods) if not stale else (),
        period_details=period_detail_rows(periods) if not stale else (),
        no_periods=True if stale else no_periods,
        dst_converted=file_checks_passed,
        dst_transition_rows=(),
        dst_pending_detail=not stale,
        file_checks_passed=file_checks_passed,
        no_complete_year=has_no_complete_year(payload) if not stale else False,
        check_detail_notes=check_detail_notes(payload) if not stale else (),
        price_available=bool(price_filename),
        price_filename=str(price_filename or ""),
    )
