"""Step 5 Review and run. Frozen snapshot; live launch and Demo open from this page."""

from __future__ import annotations

from typing import Any, Sequence

import streamlit as st

from ui.flow import (
    REVIEW_WIDGET_PREFIX,
    analysis_mode_or_none,
    back_to_step4,
    continue_to_step6,
    is_saved_example,
)
from ui.services.job import job_blocks_new_launch, launch_live_job
from ui.services.results import open_demo_results
from ui.services.review import (
    ACTION_DEMO,
    ACTION_ONE,
    ACTION_SIZE,
    REASON_STALE,
    SOLVER_CHECKBOX,
    SOLVER_HELP,
    STALE_BODY,
    STALE_TITLE,
    apply_review_fields,
    build_display_model,
    ensure_review_initialized,
    review_action_reason,
    snapshot_is_stale,
    stored_snapshot,
)
from ui.presentation.components import (
    render_action_row,
    render_display_table,
    render_metric_group,
    render_page_header,
    render_status_panel,
    render_text_table,
)
from ui.presentation.shell import app_shell
from ui.presentation.tokens import STEPS

_PAGE_TITLE = STEPS[4]
_SOLVER_KEY = f"{REVIEW_WIDGET_PREFIX}solver"
_PARTIAL_KEY = f"{REVIEW_WIDGET_PREFIX}partial"
_FINGERPRINT_KEY = f"{REVIEW_WIDGET_PREFIX}fingerprint"


def _pairs_table(rows: Sequence[tuple[str, str]]) -> None:
    render_display_table([{"Setting": label, "Value": value} for label, value in rows])


def _sync_review_widgets(fingerprint: str | None, review: dict[str, Any]) -> None:
    if st.session_state.get(_FINGERPRINT_KEY) == fingerprint:
        return
    st.session_state[_SOLVER_KEY] = bool(review.get("detailed_solver_output"))
    st.session_state[_PARTIAL_KEY] = bool(review.get("partial_period_ack"))
    st.session_state[_FINGERPRINT_KEY] = fingerprint


def _render_one_battery(display: dict[str, Any]) -> None:
    st.markdown("**Dispatch strategies**")
    render_text_table(
        [{"Dispatch strategy": name, "Method": method} for name, method in display.get("cases") or []],
        columns=("Dispatch strategy", "Method"),
    )
    caption = display.get("cases_caption")
    if caption:
        st.caption(caption)
    st.markdown("**Battery and limits**")
    _pairs_table(display.get("battery_rows") or [])
    capex = display.get("capex_caption")
    if capex:
        st.caption(capex)


def _render_sizing(state: dict[str, Any], display: dict[str, Any]) -> None:
    summary = display.get("candidate_summary") or {}
    st.markdown("**Battery sizes**")
    rows = [
        ("Candidate mode", str(summary.get("mode") or "")),
        ("Durations", str(summary.get("durations") or "")),
        ("Candidate count", str(summary.get("count") or "")),
        ("Tested power range", str(summary.get("power_range") or "")),
        ("Dispatch strategy", str(summary.get("dispatch") or "Revenue maximisation")),
    ]
    if summary.get("manual"):
        rows.append(("Manual range", str(summary["manual"])))
    _pairs_table(rows)
    if display.get("partial_required"):
        st.warning(str(display.get("partial_warning") or ""))
        checked = st.checkbox(
            str(display.get("partial_label") or ""),
            key=_PARTIAL_KEY,
        )
        apply_review_fields(state, partial_period_ack=bool(checked))
    count_label = str(summary.get("count") or "0")
    with st.expander(f"Battery sizes ({count_label})", expanded=False):
        rows_data = display.get("candidate_rows") or []
        if rows_data:
            render_display_table(rows_data)
        note = display.get("candidate_note")
        if note:
            st.caption(note)
    st.markdown("**Screening assumptions**")
    _pairs_table(display.get("screening_rows") or [])
    screening = display.get("screening_caption")
    if screening:
        st.caption(screening)


def _render_shared(display: dict[str, Any]) -> None:
    st.markdown("**Energent revenue**")
    _pairs_table(display.get("revenue_rows") or [])
    st.markdown("**Data and recorded decisions**")
    _pairs_table(display.get("data_rows") or [])
    for line in display.get("ack_records") or []:
        st.write(line)
    detail = display.get("ack_boundary_detail") or []
    if detail:
        with st.expander("Detail"):
            for line in detail:
                st.write(line)


def _render_diagnostics(state: dict[str, Any], display: dict[str, Any], *, demo: bool) -> None:
    with st.expander("Diagnostics", expanded=False):
        if demo:
            st.write("Detailed solver output: No")
        else:
            checked = st.checkbox(
                SOLVER_CHECKBOX,
                key=_SOLVER_KEY,
                help=SOLVER_HELP,
            )
            apply_review_fields(state, detailed_solver_output=bool(checked))
        _pairs_table(display.get("reporting_rows") or [])


def render_review(state: dict[str, Any]) -> None:
    demo = is_saved_example(state)
    with app_shell(
        current_step=5,
        max_available=max(int(state.get("max_step") or 1), 5),
        width="wide",
        demo=demo,
        mode=analysis_mode_or_none(state),
        state=state,
    ):
        ensure_review_initialized(state)
        snapshot = stored_snapshot(state)
        review = state.get("review") if isinstance(state.get("review"), dict) else {}
        _sync_review_widgets(review.get("fingerprint"), review)
        stale = snapshot_is_stale(state) or snapshot is None
        if stale:
            render_page_header("Step 5 of 6", _PAGE_TITLE)
            render_status_panel("danger", STALE_TITLE, STALE_BODY)
            mode = analysis_mode_or_none(state)
            if demo:
                primary = ACTION_DEMO
            elif mode == "size":
                primary = ACTION_SIZE
            else:
                primary = ACTION_ONE
            events = render_action_row(
                back="Back",
                primary=primary,
                primary_disabled=True,
                disabled_reason=REASON_STALE,
                key="v2-review-actions",
            )
            if events.back:
                back_to_step4(state)
                st.rerun()
            return
        display = build_display_model(state)
        with st.container(key="v2-review"):
            render_page_header("Step 5 of 6", _PAGE_TITLE, str(display.get("lead") or ""))
            if display.get("demo_note"):
                st.caption(str(display["demo_note"]))
            render_metric_group([(str(label), str(value)) for label, value in display.get("summary") or []])
            if display.get("mode") == "size":
                _render_sizing(state, display)
            else:
                _render_one_battery(display)
            _render_shared(display)
            _render_diagnostics(state, display, demo=demo)
        launch_error = str(state.get("launch_error") or "").strip()
        if launch_error:
            render_status_panel("danger", "The simulation could not be started.", launch_error)
        reason = review_action_reason(state)
        if job_blocks_new_launch(state):
            reason = reason or "A simulation is already running."
        events = render_action_row(
            back="Back",
            primary=str(display.get("primary_label") or "Continue"),
            primary_disabled=reason is not None,
            disabled_reason=reason,
            key="v2-review-actions",
        )
        if events.back:
            back_to_step4(state)
            st.rerun()
        if events.primary and reason is None:
            if demo:
                outcome = open_demo_results(state)
            else:
                outcome = launch_live_job(state)
            if outcome.get("ok"):
                continue_to_step6(state)
                st.rerun()
            state["launch_error"] = str(outcome.get("error") or "The simulation could not be started.")
            st.rerun()
