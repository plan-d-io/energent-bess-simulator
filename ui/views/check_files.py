"""Step 2 Data verification. Renders only from the stored serialisable snapshot."""

from __future__ import annotations

from typing import Any

import streamlit as st

from ui.flow import analysis_mode_or_none, back_to_step1, continue_to_step3, is_saved_example
from ui.services.check_files import DST_PENDING, CheckFilesModel, build_check_files_model
from ui.services.day_ahead import STANDARD_BASENAME, day_ahead_filename
from ui.presentation.components import (
    render_action_row,
    render_display_table,
    render_metric_group,
    render_page_header,
    render_section_heading,
    render_status_detail_group,
    render_status_panel,
)
from ui.presentation.shell import app_shell

_LEAD = "Reviews the detected meter roles, common coverage and available simulation periods."
_PERIOD_LEAD = "Detected time periods that can be used for a simulation"
_PRICE_METHOD = (
    "Wholesale day-ahead prices apply to grid injection only. "
    "Exact coverage for the selected period is checked after period selection."
)


def render_check_files(state: dict[str, Any]) -> None:
    model = build_check_files_model(
        state.get("ingest_snapshot"),
        price_filename=day_ahead_filename(),
    )
    with app_shell(
        current_step=2,
        max_available=max(int(state.get("max_step") or 1), 2),
        width="form",
        demo=is_saved_example(state),
        mode=analysis_mode_or_none(state),
        state=state,
    ):
        render_page_header("Step 2 of 6", "Data verification", _LEAD)
        _render_body(model)
        events = render_action_row(
            back="Back",
            primary="Continue",
            primary_disabled=not model.can_continue,
            disabled_reason=model.disabled_reason,
            key="v2-check-files-actions",
        )
        if events.back:
            back_to_step1(state)
            st.rerun()
        if events.primary and model.can_continue:
            continue_to_step3(state)
            st.rerun()


def _render_body(model: CheckFilesModel) -> None:
    if model.stale:
        render_status_panel(
            "danger",
            "The files must be checked again",
            "Return to Upload data and check the three Fluvius files.",
        )
        return
    if model.usable:
        render_status_panel(
            "success",
            "Files usable",
            "Offtake, injection and PV production share a common quarter-hour coverage.",
        )
    for title, body in model.fatal_panels:
        render_status_panel("danger", title, body)
    if model.no_periods:
        render_status_panel(
            "danger",
            "No usable simulation period was found",
            "These files do not share a candidate simulation period.",
        )

    render_section_heading("Detected meter roles")
    render_display_table(list(model.role_rows))
    if model.ignored_rows:
        with st.expander(f"Ignored registers ({len(model.ignored_rows)})"):
            render_display_table(list(model.ignored_rows))
            st.caption("Auxiliary and reactive registers are not used as PV production.")

    render_section_heading("Common coverage")
    render_metric_group(model.coverage_metrics)

    render_section_heading("Candidate periods", _PERIOD_LEAD)
    if model.period_rows:
        render_display_table(list(model.period_rows))
        if model.period_details:
            with st.expander("Period details"):
                render_display_table(list(model.period_details))

    with render_status_detail_group("dst"):
        if model.dst_converted:
            render_status_panel(
                "success",
                "Timestamps converted",
                "Local timestamps were converted to UTC.",
            )
            st.caption(DST_PENDING)

    with render_status_detail_group("checks"):
        if model.file_checks_passed:
            render_status_panel(
                "success",
                "File checks passed",
                "No blocking file or common-coverage errors were found.",
            )
        if model.no_complete_year:
            render_status_panel(
                "warning",
                "No complete calendar year",
                "Only partial years are available. This can affect analysis quality.",
            )
        if model.check_detail_notes:
            with st.expander("Check details"):
                for note in model.check_detail_notes:
                    st.write(note)

    with render_status_detail_group("prices"):
        if model.price_available:
            render_status_panel(
                "success",
                "Day-ahead price dataset available",
                f"Standard project dataset · {model.price_filename or STANDARD_BASENAME}",
            )
        else:
            render_status_panel(
                "warning",
                "Day-ahead price dataset unavailable",
                "Battery sizing can continue. One-battery analysis will require this dataset.",
            )
        with st.expander("Price dataset detail"):
            st.write(_PRICE_METHOD)
