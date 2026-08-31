"""Step 3 Simulation period. Live inspection and Demo artifact projection."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.flow import (
    PERIOD_SELECT_KEY,
    SITE_BOUNDARY_ACK_WIDGET_KEY,
    UNVALIDATED_ACK_WIDGET_KEY,
    analysis_mode_or_none,
    apply_period_change,
    apply_site_boundary_ack,
    apply_unvalidated_ack,
    back_to_step2,
    continue_to_step4,
    is_saved_example,
    store_period_inspection,
    store_price_coverage,
    store_saved_period_context,
)
from ui.services.period import (
    PASSED_BODY,
    PARTIAL_BODY,
    REASON_DEMO,
    SITE_BOUNDARY_CHECKBOX,
    classification_line,
    continue_disabled_reason,
    default_period_id,
    discovery_allow_unvalidated,
    final_allow_unvalidated,
    inspection_belongs_to_period,
    inspection_ok,
    is_complete_year,
    meter_boundary_detail,
    needs_meter_boundary_ack,
    non_acknowledgeable_fatals,
    ordered_period_ids,
    ordered_periods,
    period_by_id,
    period_detail_rows,
    period_option_label,
    selected_dst_rows,
    show_meter_boundary_panel,
    snapshot_is_stale,
    simultaneous_diagnostic,
    unvalidated_checkbox_label,
    unvalidated_dates_from_inspection,
    unvalidated_detail,
    unvalidated_warning_title,
    valid_periods,
)
from ui.services.period_inspection import (
    inspect_period_payloads,
    period_inspection_cache_key,
)
from ui.services.price_coverage import (
    price_coverage_cache_key,
    price_coverage_for_payloads,
)
from ui.services.saved_example import load_saved_period_context
from ui.services.uploads import format_row_count
from ui.presentation.components import (
    render_acknowledgement_panel,
    render_action_row,
    render_display_table,
    render_page_header,
    render_status_detail_group,
    render_status_panel,
)
from ui.presentation.shell import app_shell

_LEAD = "Select a calendar period for the simulation. A complete calendar year is recommended."
_PRICE_UNAVAILABLE = (
    "Find a battery size can continue. Evaluate one battery will be unavailable."
)


def render_choose_period(state: dict[str, Any]) -> None:
    demo = is_saved_example(state)
    with app_shell(
        current_step=3,
        max_available=max(int(state.get("max_step") or 1), 3),
        width="form",
        demo=demo,
        mode=analysis_mode_or_none(state),
        state=state,
    ):
        render_page_header("Step 3 of 6", "Simulation period", _LEAD)
        can_continue, reason = _render_body(state, demo=demo)
        events = render_action_row(
            back="Back",
            primary="Continue",
            primary_disabled=not can_continue,
            disabled_reason=None if can_continue else reason,
            key="v2-choose-period-actions",
        )
        if events.back:
            back_to_step2(state)
            st.rerun()
        if events.primary and can_continue:
            continue_to_step4(state)
            st.rerun()


def _render_body(state: dict[str, Any], *, demo: bool) -> tuple[bool, str | None]:
    if demo:
        return _render_demo(state)
    return _render_live(state)


def _render_demo(state: dict[str, Any]) -> tuple[bool, str | None]:
    context = load_saved_period_context()
    if not context.get("ok"):
        render_status_panel(
            "danger",
            "Saved example unavailable",
            "The saved Ganda Cars example is not available.",
        )
        return False, REASON_DEMO
    store_saved_period_context(state, context)
    period = context.get("selected_period") or {}
    inspection = context.get("period_inspection")
    coverage = context.get("price_coverage")
    dates = tuple(context.get("unvalidated_dates") or ())
    n_unvalidated = int(period.get("n_unvalidated") or 0)
    st.session_state[PERIOD_SELECT_KEY] = "2024"
    with st.container(key="v2-period-select"):
        st.selectbox(
            "Simulation period",
            options=["2024"],
            format_func=lambda _key: period_option_label(period),
            key=PERIOD_SELECT_KEY,
            disabled=True,
        )
        st.caption(classification_line(period))
        _render_period_details(period, inspection)
    _render_unvalidated_ack(
        n_unvalidated,
        dates,
        checked=True,
        disabled=True,
    )
    _render_price_coverage(coverage if isinstance(coverage, Mapping) else None)
    render_status_panel("success", "Period checks passed", PASSED_BODY)
    return True, None


def _blocked(title: str, body: str, reason: str) -> tuple[bool, str | None]:
    render_status_panel("danger", title, body)
    return False, reason


def _render_live(state: dict[str, Any]) -> tuple[bool, str | None]:
    snapshot = state.get("ingest_snapshot")
    periods = valid_periods(snapshot if isinstance(snapshot, Mapping) else None)
    stale = snapshot_is_stale(snapshot if isinstance(snapshot, Mapping) else None)
    if stale:
        return _blocked(
            "The files must be checked again",
            "Return to Data verification and continue from a successful file check.",
            continue_disabled_reason(
                stale=True,
                demo_blocked=False,
                selected=None,
                inspection=None,
                inspection_running=False,
                needs_unvalidated=False,
                unvalidated_ack=False,
                needs_boundary=False,
                boundary_ack=False,
                inspection_usable=False,
            )
            or "",
        )
    if not periods:
        return _blocked(
            "No usable simulation period was found",
            "Return to Data verification. These files do not share a candidate simulation period.",
            continue_disabled_reason(
                stale=False,
                demo_blocked=False,
                selected=None,
                inspection=None,
                inspection_running=False,
                needs_unvalidated=False,
                unvalidated_ack=False,
                needs_boundary=False,
                boundary_ack=False,
                inspection_usable=False,
            )
            or "",
        )

    option_ids = ordered_period_ids(periods)
    by_id = {str(item["id"]): item for item in ordered_periods(periods)}
    stored = state.get("period_id")
    if stored is not None and str(stored) not in by_id:
        return _blocked(
            "The selected period is no longer available",
            "Return to Data verification and choose a period from the current files.",
            continue_disabled_reason(
                stale=False,
                demo_blocked=False,
                selected=None,
                inspection=None,
                inspection_running=False,
                needs_unvalidated=False,
                unvalidated_ack=False,
                needs_boundary=False,
                boundary_ack=False,
                inspection_usable=False,
            )
            or "",
        )
    if stored is None:
        default = default_period_id(periods)
        if default is None:
            return _blocked(
                "No usable simulation period was found",
                "Return to Data verification. These files do not share a candidate simulation period.",
                continue_disabled_reason(
                    stale=False,
                    demo_blocked=False,
                    selected=None,
                    inspection=None,
                    inspection_running=False,
                    needs_unvalidated=False,
                    unvalidated_ack=False,
                    needs_boundary=False,
                    boundary_ack=False,
                    inspection_usable=False,
                )
                or "",
            )
        apply_period_change(state, default)
        stored = default

    current_id = str(stored)
    if PERIOD_SELECT_KEY not in st.session_state:
        st.session_state[PERIOD_SELECT_KEY] = current_id
    widget_id = str(st.session_state.get(PERIOD_SELECT_KEY) or "")
    if widget_id != current_id and widget_id in by_id:
        apply_period_change(state, widget_id)
        st.session_state.pop(UNVALIDATED_ACK_WIDGET_KEY, None)
        st.session_state.pop(SITE_BOUNDARY_ACK_WIDGET_KEY, None)
        st.rerun()
    elif widget_id != current_id:
        st.session_state[PERIOD_SELECT_KEY] = current_id

    labels = {period_id: period_option_label(by_id[period_id]) for period_id in option_ids}
    with st.container(key="v2-period-select"):
        chosen = st.selectbox(
            "Simulation period",
            options=option_ids,
            format_func=lambda period_id: labels.get(period_id, str(period_id)),
            key=PERIOD_SELECT_KEY,
        )
        if str(chosen) != current_id:
            apply_period_change(state, str(chosen))
            st.session_state.pop(UNVALIDATED_ACK_WIDGET_KEY, None)
            st.session_state.pop(SITE_BOUNDARY_ACK_WIDGET_KEY, None)
            st.rerun()

        period = period_by_id(periods, current_id) or by_id[current_id]
        st.caption(classification_line(period))
        if not is_complete_year(period):
            render_status_panel("warning", "Partial period", PARTIAL_BODY)

        payloads = tuple(state.get("upload_payloads") or ())
        signature = tuple(state.get("upload_signature") or ())
        discovery_unvalidated = discovery_allow_unvalidated(period)
        with st.spinner("Checking the selected period"):
            discovery = inspect_period_payloads(
                payloads,
                current_id,
                allow_unvalidated=discovery_unvalidated,
                acknowledge_site_boundary=False,
                signature=signature,
            )

        _render_period_details(period, discovery)

    n_unvalidated = int(period.get("n_unvalidated") or 0)
    dates = unvalidated_dates_from_inspection(period, discovery)
    unvalidated_ack = bool(state.get("unvalidated_ack"))
    if n_unvalidated > 0:
        checked = _render_unvalidated_ack(
            n_unvalidated,
            dates,
            checked=unvalidated_ack,
            disabled=False,
        )
        apply_unvalidated_ack(state, checked)
        unvalidated_ack = bool(state.get("unvalidated_ack"))

    fatals = non_acknowledgeable_fatals(discovery)
    for item in fatals:
        render_status_panel(
            "danger",
            str(item.get("code") or "Selected period cannot be used"),
            str(item.get("message") or "Resolve the selected-period issues above."),
        )

    show_boundary = show_meter_boundary_panel(discovery)
    needs_boundary = needs_meter_boundary_ack(discovery)
    boundary_ack = bool(state.get("site_boundary_ack"))
    if show_boundary:
        with st.container(key="v2-period-boundary"):
            checked_boundary = render_acknowledgement_panel(
                title="Meter-boundary mismatch",
                facts=(),
                checkbox_label=SITE_BOUNDARY_CHECKBOX,
                checked=boundary_ack,
                detail=meter_boundary_detail(discovery),
                disabled=False,
                key=SITE_BOUNDARY_ACK_WIDGET_KEY,
            )
        apply_site_boundary_ack(state, checked_boundary)
        boundary_ack = bool(state.get("site_boundary_ack"))

    simultaneous = simultaneous_diagnostic(discovery)
    if simultaneous:
        with st.expander("Simultaneous import and export"):
            count = simultaneous.get("n_intervals")
            if count is not None:
                st.write(f"Quarter-hours: {format_row_count(int(count))}")
            note = simultaneous.get("note")
            if note:
                st.write(str(note))

    acks_ready = (n_unvalidated == 0 or unvalidated_ack) and (not needs_boundary or boundary_ack)
    final = discovery
    allow_unvalidated = final_allow_unvalidated(period, unvalidated_ack)
    ack_boundary = bool(needs_boundary and boundary_ack)
    if acks_ready and not fatals:
        if ack_boundary:
            with st.spinner("Checking the selected period"):
                final = inspect_period_payloads(
                    payloads,
                    current_id,
                    allow_unvalidated=allow_unvalidated,
                    acknowledge_site_boundary=True,
                    signature=signature,
                )
        store_period_inspection(
            state,
            final,
            cache_key=period_inspection_cache_key(
                signature,
                current_id,
                allow_unvalidated=allow_unvalidated,
                acknowledge_site_boundary=ack_boundary,
            ),
        )
    else:
        store_period_inspection(
            state,
            discovery,
            cache_key=period_inspection_cache_key(
                signature,
                current_id,
                allow_unvalidated=discovery_unvalidated,
                acknowledge_site_boundary=False,
            ),
        )

    usable = (
        acks_ready
        and not fatals
        and inspection_ok(final)
        and inspection_belongs_to_period(final, current_id)
    )
    if usable:
        price_key = price_coverage_cache_key(
            signature,
            current_id,
            allow_unvalidated=allow_unvalidated,
            acknowledge_site_boundary=ack_boundary,
        )
        stored_coverage = state.get("price_coverage")
        if state.get("price_coverage_key") == price_key and isinstance(stored_coverage, Mapping):
            coverage = stored_coverage
        else:
            with st.spinner("Checking day-ahead price coverage"):
                coverage = price_coverage_for_payloads(
                    payloads,
                    current_id,
                    allow_unvalidated=allow_unvalidated,
                    acknowledge_site_boundary=ack_boundary,
                    signature=signature,
                )
            store_price_coverage(state, coverage, cache_key=price_key)
        _render_price_coverage(coverage)
        render_status_panel("success", "Period checks passed", PASSED_BODY)

    reason = continue_disabled_reason(
        stale=False,
        demo_blocked=False,
        selected=period,
        inspection=discovery,
        inspection_running=False,
        needs_unvalidated=n_unvalidated > 0,
        unvalidated_ack=unvalidated_ack,
        needs_boundary=needs_boundary,
        boundary_ack=boundary_ack,
        inspection_usable=usable,
    )
    return usable, reason


def _render_unvalidated_ack(
    count: int,
    dates: tuple[str, ...],
    *,
    checked: bool,
    disabled: bool,
) -> bool:
    with st.container(key="v2-period-ack"):
        return render_acknowledgement_panel(
            title=unvalidated_warning_title(count),
            facts=(),
            checkbox_label=unvalidated_checkbox_label(count, dates),
            checked=checked,
            detail=unvalidated_detail(dates),
            disabled=disabled,
            key=UNVALIDATED_ACK_WIDGET_KEY,
        )


def _render_period_details(period: Mapping[str, Any], inspection: Mapping[str, Any] | None) -> None:
    with st.expander("Period details"):
        rows = period_detail_rows(period, inspection)
        if rows:
            render_display_table(list(rows))
        st.caption("Local timestamps")
        dst_rows = selected_dst_rows(inspection)
        if dst_rows:
            render_display_table(list(dst_rows))


def _render_price_coverage(coverage: Mapping[str, Any] | None) -> None:
    if not isinstance(coverage, Mapping):
        return
    with render_status_detail_group("price-coverage"):
        if coverage.get("covered"):
            count = format_row_count(int(coverage.get("selected_row_count") or 0))
            render_status_panel(
                "success",
                "Day-ahead prices cover this period",
                f"{count} quarter-hours matched exactly.",
            )
        else:
            render_status_panel(
                "warning",
                "Day-ahead prices do not cover this period",
                _PRICE_UNAVAILABLE,
            )
        with st.expander("Price coverage detail"):
            st.write(f"Dataset: {coverage.get('source_basename') or '—'}")
            count = coverage.get("selected_row_count")
            st.write(
                "Selected row count: "
                f"{format_row_count(int(count)) if count is not None else '—'}"
            )
            bounds = coverage.get("coverage_utc") or []
            if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                st.write(f"Dataset UTC coverage: {bounds[0]} to {bounds[1]}")
            native = coverage.get("native_resolution_counts") or {}
            if isinstance(native, Mapping) and native:
                parts = [
                    f"{key}: {format_row_count(int(value))}" for key, value in native.items()
                ]
                st.write("Native resolution: " + ", ".join(parts))
            repeated = coverage.get("hourly_values_repeated")
            if repeated is not None:
                st.write(f"Hourly values repeated: {'yes' if repeated else 'no'}")
            error = coverage.get("error") if isinstance(coverage.get("error"), Mapping) else None
            if error and error.get("exception_type"):
                with st.expander("Diagnostic"):
                    st.write(str(error.get("exception_type")))
                    if error.get("message"):
                        st.write(str(error.get("message")))
