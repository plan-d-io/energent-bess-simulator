"""Battery-size Results for Find a battery size. Read-only artifact display."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import streamlit as st

from ui.flow import back_to_step4, is_saved_example
from ui.services.sweep_charts import (
    PLOTLY_CONFIG,
    build_cycles_chart,
    build_interval_peak_chart,
    build_monthly_peak_chart,
    build_payback_chart,
    build_revenue_chart,
)
from ui.services.sweep_display import (
    SweepDisplay,
    SweepDisplayError,
    display_cache_key,
    load_sweep_display,
    lookup_candidate,
    sweep_display_guard,
)
from ui.services.sweep_downloads import (
    audit_zip_filename,
    build_audit_zip,
    grouped_inventory,
    read_contained_bytes,
    zip_identity,
)
from ui.services.sweep_format import (
    ALL_SIZES_HEADING,
    TAB_NAMES,
    TOP_RESULTS_HEADING,
    TRANSFER_DEMO,
    TRANSFER_DISABLED_NOTE,
    TRANSFER_HEADING,
    TRANSFER_LIVE,
    TRANSFER_SELECTOR,
)
from ui.services.sweep_transfer import (
    clear_transfer_widget_keys,
    demo_transfer_available,
    transfer_demo_candidate,
    transfer_live_candidate,
)
from ui.presentation.components import (
    render_display_table,
    render_metric_group,
    render_page_header,
    render_section_heading,
)


@st.cache_data(show_spinner=False)
def _cached_display(folder: str, identity: tuple, site: str, source: str) -> SweepDisplay:
    del identity
    return load_sweep_display(folder, site=site, source=source)


@st.cache_data(show_spinner=False)
def _cached_zip(folder: str, identity: tuple) -> bytes:
    del identity
    return build_audit_zip(folder)


def _table(rows: Sequence[Mapping[str, Any]], *, columns: Sequence[str] | None = None) -> None:
    if columns:
        render_display_table([{key: row.get(key) for key in columns} for row in rows], hide_index=True)
    else:
        render_display_table(pd.DataFrame(list(rows)), hide_index=True)


def _render_plotly(fig, *, key: str) -> None:
    st.plotly_chart(
        fig,
        width="stretch",
        theme="streamlit",
        config=PLOTLY_CONFIG,
        key=key,
    )


def _render_overview(model: SweepDisplay, state: dict[str, Any]) -> None:
    overview = model.overview
    st.write(overview["headline"])
    with st.container(key="v2-sweep-highlights"):
        cols = st.columns(3)
        for column, card in zip(cols, overview["highlights"], strict=True):
            with column.container(border=True):
                st.caption(card["label"])
                st.markdown(f"**{card['value']}**")
                for line in card["lines"]:
                    st.caption(line)
    st.caption(overview["assumptions"])
    render_section_heading(TOP_RESULTS_HEADING)
    _table(overview["duration_rows"], columns=overview["duration_columns"])
    render_section_heading(ALL_SIZES_HEADING)
    st.dataframe(
        pd.DataFrame(model.sizes["rows"], columns=list(model.sizes["columns"])),
        hide_index=True,
        width="stretch",
    )
    with st.expander("What do the flags mean?", expanded=False):
        for name, text in model.sizes["glossary"]:
            st.markdown(f"**{name}:** {text}")
    st.download_button(
        "Download candidate table as CSV",
        data=model.sizes["csv_bytes"],
        file_name="sweep_candidates.csv",
        mime="text/csv",
        key="v2-sweep-table-csv",
    )
    _render_transfer(model, state)


def _render_transfer(model: SweepDisplay, state: dict[str, Any]) -> None:
    options = list(model.transfer["options"])
    if not options:
        return
    keys = [item[0] for item in options]
    labels = dict(model.transfer["labels"])
    default = model.transfer["default"] if model.transfer["default"] in keys else keys[0]
    render_section_heading(TRANSFER_HEADING)
    chosen = st.selectbox(
        TRANSFER_SELECTOR,
        options=keys,
        index=keys.index(default),
        format_func=lambda item: labels.get(item, item),
        key="v2-sweep-transfer-size",
    )
    demo = is_saved_example(state) or str(state.get("results", {}).get("source") or "") == "demo"
    if demo:
        available, reason = demo_transfer_available()
        disabled = not available
        if st.button(TRANSFER_DEMO, type="primary", disabled=disabled, key="v2-sweep-transfer"):
            candidate = lookup_candidate(model.summary, chosen) or {}
            result = transfer_demo_candidate(state, candidate=candidate, folder=model.folder)
            if result.get("ok"):
                clear_transfer_widget_keys(st.session_state, demo_off=True)
                st.rerun()
            st.caption(str(result.get("reason") or TRANSFER_DISABLED_NOTE))
        if disabled and reason:
            st.caption(reason)
        return
    if st.button(TRANSFER_LIVE, type="primary", key="v2-sweep-transfer"):
        candidate = lookup_candidate(model.summary, chosen) or {}
        result = transfer_live_candidate(state, candidate=candidate)
        if result.get("ok"):
            clear_transfer_widget_keys(st.session_state, demo_off=False)
            st.rerun()


def _render_revenue(model: SweepDisplay) -> None:
    if model.revenue.get("partial_warning"):
        st.warning(model.revenue["partial_warning"])
    st.subheader(model.revenue["payback_title"])
    _render_plotly(build_payback_chart(model.summary), key="v2-sweep-payback")
    st.subheader(model.revenue["revenue_title"])
    _render_plotly(build_revenue_chart(model.summary), key="v2-sweep-revenue")
    st.caption(model.revenue["capture_caption"])


def _render_peaks(model: SweepDisplay) -> None:
    peaks = model.peaks
    for notice in peaks["notices"]:
        st.info(notice)
    if peaks["monthly_ok"]:
        st.subheader(peaks["monthly_title"])
        if peaks.get("definition"):
            st.caption(peaks["definition"])
        monthly = build_monthly_peak_chart(model.summary)
        if monthly is not None:
            _render_plotly(monthly, key="v2-sweep-monthly-peak")
    st.subheader(peaks["interval_title"])
    interval = build_interval_peak_chart(model.summary)
    if interval is not None:
        _render_plotly(interval, key="v2-sweep-interval-peak")
    if peaks["table_rows"]:
        render_display_table(pd.DataFrame(peaks["table_rows"]), hide_index=True)


def _render_battery_use(model: SweepDisplay) -> None:
    st.subheader(model.battery_use["title"])
    if model.battery_use.get("explanation"):
        st.caption(model.battery_use["explanation"])
    _render_plotly(build_cycles_chart(model.summary), key="v2-sweep-cycles")


def _render_additional_details(model: SweepDisplay) -> None:
    render_section_heading("Additional details")
    extra = model.sizes["extra_rows"]
    if extra:
        render_display_table(pd.DataFrame(extra), hide_index=True)
    with st.expander("Technical solver checks", expanded=False):
        provenance = model.sizes.get("solver_provenance") or {}
        if provenance.get("line"):
            st.write(provenance["line"])
        elif provenance.get("unavailable_note"):
            st.write(provenance["unavailable_note"])
        solver = model.sizes["solver_rows"]
        if solver:
            render_display_table(pd.DataFrame(solver), hide_index=True)


def _render_downloads(model: SweepDisplay, state: dict[str, Any]) -> None:
    folder = Path(model.folder)
    if model.downloads["demo"]:
        st.write("This is the stored demonstration audit folder. It was not produced by a new run.")
    else:
        st.write(f"This completed run is stored as `{model.downloads['folder_name']}`.")
    for heading, rows in grouped_inventory(folder):
        render_section_heading(heading)
        for row in rows:
            payload = read_contained_bytes(folder, row["File"])
            if payload is None:
                continue
            st.download_button(
                f"Download {row['File']}",
                data=payload,
                file_name=row["File"],
                help=row["Purpose"],
                key=f"v2-sweep-dl-{row['File']}",
            )
    results = state.get("results") if isinstance(state.get("results"), Mapping) else {}
    st.download_button(
        "Download audit ZIP",
        data=_cached_zip(str(folder), zip_identity(folder)),
        file_name=audit_zip_filename(
            site=str(results.get("site") or ""),
            period_id=str(results.get("period_id") or ""),
        ),
        mime="application/zip",
        type="primary",
        key="v2-sweep-audit-zip",
    )
    if st.button("Return to Configure options", type="secondary", key="v2-sweep-back-configure"):
        back_to_step4(state)
        st.rerun()


def render_sweep_results(state: dict[str, Any]) -> bool:
    results = state.get("results") if isinstance(state.get("results"), Mapping) else None
    if sweep_display_guard(results) is not None:
        return False
    assert results is not None
    folder = Path(str(results["result_dir"]))
    try:
        model = _cached_display(
            str(folder),
            display_cache_key(folder),
            str(results.get("site") or ""),
            str(results.get("source") or ""),
        )
    except SweepDisplayError:
        return False
    header = model.header
    with st.container(key="v2-sweep-results"):
        render_page_header("Step 6 of 6", header["title"], header["source_line"])
        for note in model.notes:
            st.warning(note)
        render_metric_group(
            [
                ("Site", header["site"] or "—"),
                ("Period", header["period_label"] or "—"),
                ("Tested sizes", header["tested_sizes"]),
                ("Durations", header["durations"]),
                ("Dispatch strategy", header["strategy"]),
            ],
            key="v2-metrics-sweep-header",
        )
        tabs = st.tabs(list(TAB_NAMES))
        with tabs[0]:
            _render_overview(model, state)
        with tabs[1]:
            _render_revenue(model)
        with tabs[2]:
            _render_peaks(model)
        with tabs[3]:
            _render_battery_use(model)
        with tabs[4]:
            _render_additional_details(model)
        with tabs[5]:
            _render_downloads(model, state)
    return True
