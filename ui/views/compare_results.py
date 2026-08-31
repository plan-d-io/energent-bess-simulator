"""Full-comparison Results for Evaluate one battery. Read-only artifact display."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import streamlit as st

from ui.flow import back_to_step4
from ui.services.compare_charts import (
    PLOTLY_CONFIG,
    altair_chart,
    plotly_line_figure,
    plotly_linked_figures,
)
from ui.services.compare_display import (
    ComparisonDisplay,
    ComparisonDisplayError,
    comparison_display_guard,
    display_cache_key,
    energy_monthly_models,
    load_comparison_display,
    peaks_monthly_table,
    revenue_monthly_models,
)
from ui.services.compare_downloads import (
    audit_zip_filename,
    build_audit_zip,
    file_size,
    inventory_rows,
    read_contained_bytes,
    zip_identity,
)
from ui.services.compare_explorer import (
    DISPATCH_PARQUET,
    DispatchQueryError,
    explorer_chart_models,
    iso_weeks_wholly_inside,
    parquet_identity,
    query_dispatch_week,
    seasonal_windows,
    week_caption,
    week_csv_bytes,
    week_interval_note,
)
from ui.services.compare_format import TAB_NAMES, case_label
from ui.presentation.components import (
    render_display_table,
    render_metric_group,
    render_page_header,
    render_section_heading,
)

_PREP_PARQUET = "v2-cmp-prep-parquet"
_PREP_CSV = "v2-cmp-prep-csv"
_WINDOW_SEASONAL = "Seasonal week"
_WINDOW_CHOOSE = "Choose a week"


@st.cache_data(show_spinner=False)
def _cached_display(folder: str, identity: tuple, site: str, source: str) -> ComparisonDisplay:
    del identity
    return load_comparison_display(folder, site=site, source=source)


@st.cache_data(show_spinner=False)
def _cached_week(
    parquet_path: str,
    identity: tuple,
    start_utc: str,
    end_utc_exclusive: str,
    scenario: str,
):
    del identity
    result = query_dispatch_week(parquet_path, start_utc, end_utc_exclusive, scenario)
    return result.frame, int(result.n_rows)


@st.cache_data(show_spinner=False)
def _cached_zip(folder: str, identity: tuple) -> bytes:
    del identity
    return build_audit_zip(folder)


def _table(rows: Sequence[Mapping[str, Any]]) -> None:
    render_display_table(pd.DataFrame(list(rows)), hide_index=True)


def _select_strategy(
    cases: Sequence[tuple[str, str]],
    *,
    key: str,
    default: str,
    include_no_battery: bool = False,
) -> str:
    options = [item for item in cases if include_no_battery or item[0] != "no_battery"]
    if not options:
        options = list(cases)
    keys = [item[0] for item in options]
    labels = {item[0]: item[1] for item in options}
    selected = default if default in keys else keys[0]
    return st.selectbox(
        "Dispatch strategy",
        options=keys,
        index=keys.index(selected),
        format_func=lambda item: labels[item],
        key=key,
    )


def _render_plotly(spec, *, key: str, height: int = 320, point: bool = True) -> None:
    st.plotly_chart(
        plotly_line_figure(spec, height=height, point=point),
        width="stretch",
        theme="streamlit",
        config=PLOTLY_CONFIG,
        key=key,
    )


def _render_linked(specs, *, key: str, height: int = 280) -> None:
    st.plotly_chart(
        plotly_linked_figures(specs, height=height),
        width="stretch",
        theme="streamlit",
        config=PLOTLY_CONFIG,
        key=key,
    )


def _render_altair(spec, *, height: int = 320) -> None:
    st.altair_chart(altair_chart(spec, height=height), width="stretch")


def _render_overview(model: ComparisonDisplay) -> None:
    overview = model.overview
    st.caption(overview["financial_caption"])
    warning = overview.get("partial_warning")
    if warning:
        st.warning(warning)
    render_section_heading("Overview")
    _table(overview["highlight_rows"])
    st.caption(overview["method_caption"])
    for heading, rows in overview["groups"]:
        render_section_heading(heading)
        _table(rows)
        if heading == "Energent revenue and payback" and overview.get("payback_definition"):
            st.caption(overview["payback_definition"])
        if heading == "Grid peaks":
            st.caption(model.peaks["financial_note"])


def _render_energy(model: ComparisonDisplay) -> None:
    energy = model.energy
    render_metric_group(
        [
            ("PV production", energy["pv_production"]),
            ("Site load", energy["site_load"]),
            ("Useful PV before the battery", energy["useful_before"]),
        ],
        key="v2-metrics-energy",
    )
    render_section_heading("PV use")
    _table(energy["pv_use"])
    render_section_heading("Grid energy")
    _table(energy["grid_energy"])
    st.caption(energy["import_injection_caption"])
    render_section_heading("Monthly energy")
    strategy = _select_strategy(
        model.cases,
        key="v2-cmp-energy-case",
        default=energy["default_strategy"],
    )
    table, charts = energy_monthly_models(
        model.monthly, scenario=strategy, label=case_label(strategy)
    )
    _render_linked(charts, key="v2-cmp-energy-charts")
    _table(table)


def _render_peaks(model: ComparisonDisplay) -> None:
    peaks = model.peaks
    st.write(peaks["definition"])
    st.caption(peaks["complete_months"])
    _table(peaks["compact"])
    _render_plotly(peaks["all_cases_chart"], key="v2-cmp-peaks-all", point=True)
    strategy = _select_strategy(
        model.cases,
        key="v2-cmp-peaks-case",
        default=peaks["default_strategy"],
    )
    _table(peaks_monthly_table(model.monthly, strategy))
    st.caption(peaks["financial_note"])


def _render_revenue(model: ComparisonDisplay) -> None:
    revenue = model.revenue
    st.write(revenue["limitation"])
    if revenue.get("dynamic_note"):
        st.caption(revenue["dynamic_note"])
    _table(revenue["comparison"])
    with st.expander("Revenue composition detail", expanded=False):
        _table(revenue["detail"])
        st.caption(
            "Export revenue given up is the opportunity cost of charging the battery "
            "instead of exporting that PV."
        )
    render_section_heading("Cost and payback")
    if revenue.get("historical_cost"):
        st.write(revenue["historical_cost"])
    elif revenue.get("cost_text"):
        st.write(revenue["cost_text"])
    _table(revenue["payback_rows"])
    if revenue.get("payback_definition"):
        st.caption(revenue["payback_definition"])
    render_section_heading("Monthly Energent PV revenue")
    strategy = _select_strategy(
        model.cases,
        key="v2-cmp-revenue-case",
        default=revenue["default_strategy"],
    )
    table, charts = revenue_monthly_models(
        model.monthly, scenario=strategy, label=case_label(strategy)
    )
    _render_plotly(charts[0], key="v2-cmp-rev-total", point=True)
    _render_altair(charts[1])
    st.caption("The monthly revenue increase is not profit.")
    _render_altair(charts[2])
    if strategy == "dynamic_injection" and revenue.get("dynamic_note"):
        st.caption(revenue["dynamic_note"])
    _table(table)


def _render_explorer(model: ComparisonDisplay) -> None:
    st.write(
        "Inspect one stored week of quarter-hour dispatch. Changing these controls "
        "does not rerun a simulation."
    )
    folder = Path(model.folder)
    parquet = folder / DISPATCH_PARQUET
    strategy = _select_strategy(
        model.cases,
        key="v2-cmp-explorer-case",
        default=model.explorer["default_strategy"],
        include_no_battery=True,
    )
    seasonal = seasonal_windows(model.summary)
    weeks = iso_weeks_wholly_inside(
        model.explorer["period_start_local"],
        model.explorer["period_end_local"],
    )
    if seasonal:
        window_mode = st.radio(
            "Time window",
            options=[_WINDOW_SEASONAL, _WINDOW_CHOOSE],
            horizontal=True,
            key="v2-cmp-explorer-window",
        )
    else:
        window_mode = _WINDOW_CHOOSE
    if window_mode == _WINDOW_SEASONAL and seasonal:
        labels = {window.season or window.label: window for window in seasonal if window.season}
        season_keys = list(labels)
        default_index = season_keys.index("winter") if "winter" in season_keys else 0
        season = st.selectbox(
            _WINDOW_SEASONAL,
            options=season_keys,
            index=default_index,
            format_func=lambda key: labels[key].label,
            key="v2-cmp-explorer-season",
        )
        window = labels[season]
    else:
        if not weeks:
            st.error("No complete local week falls wholly inside the saved period.")
            return
        default_index = next(
            (
                index
                for index, item in enumerate(weeks)
                if item.iso_year == 2024 and item.iso_week == 3
            ),
            0,
        )
        selected = st.selectbox(
            "Local week",
            options=list(range(len(weeks))),
            index=default_index,
            format_func=lambda index: weeks[index].label,
            key="v2-cmp-explorer-week",
        )
        window = weeks[int(selected)]
    if not parquet.is_file():
        st.error(
            "The dispatch Parquet file is missing, so this tab cannot show quarter-hour traces. "
            "The other result tabs still use the stored annual and monthly summaries."
        )
        return
    try:
        frame, n_rows = _cached_week(
            str(parquet),
            parquet_identity(parquet),
            window.start_utc,
            window.end_utc_exclusive,
            strategy,
        )
    except DispatchQueryError:
        st.error(
            "The selected week could not be read from the dispatch file. "
            "The other result tabs remain available."
        )
        return
    panels = explorer_chart_models(frame, scenario=strategy)
    _render_linked(panels, key="v2-cmp-explorer-charts", height=240)
    st.caption(
        "Grid import is plotted as positive kW. PV injected into the grid is plotted "
        "as negative kW so the two directions stay visually distinct."
    )
    if any(panel.title == "Day-ahead injection price" for panel in panels):
        st.caption(
            "Day-ahead prices use their own EUR/MWh axis. They are not plotted against kW or kWh."
        )
    st.write(week_caption(window, n_rows=n_rows, label=case_label(strategy)))
    st.caption(week_interval_note(n_rows))
    st.download_button(
        "Download this week as CSV",
        data=week_csv_bytes(frame),
        file_name=(
            f"comparison_dispatch_{strategy}_iso{window.iso_week:02d}_"
            f"{window.start_local[:10]}.csv"
        ),
        mime="text/csv",
        key="v2-cmp-week-csv",
        help="Contains only the filtered week, not the full dispatch file.",
    )


def _render_technical(model: ComparisonDisplay) -> None:
    version = model.technical.get("schema_version")
    if version is not None:
        st.caption(f"Artifact schema version: {version}.")
    with st.expander("Battery operation", expanded=False):
        _table(model.technical["battery"])
        st.caption("Equivalent full cycles are a technical indicator, not a degradation model.")
    with st.expander("Solver", expanded=False):
        if model.technical["has_solver_records"]:
            _table(model.technical["solvers"])
        else:
            st.write("This saved run has no solver records.")
    prices = model.technical.get("da_prices")
    if prices:
        with st.expander("Day-ahead injection prices", expanded=False):
            st.write(f"Source file: {prices['source_basename']}.")
            st.write(f"SHA-256: {prices['sha256']}.")
            if prices.get("coverage"):
                st.write(f"Source UTC coverage: {prices['coverage']}.")
            st.write(f"Selected-period quarter-hours: {prices['selected_row_count']}.")
            st.write(
                f"Native hourly source rows: {prices['hourly_rows']}. "
                f"Native 15-minute source rows: {prices['quarter_rows']}."
            )
            if prices.get("hourly_repeated"):
                st.caption("Hourly source values were already repeated over their quarter-hours.")
            if (
                prices.get("min") is not None
                and prices.get("max") is not None
                and prices.get("mean") is not None
            ):
                st.write(
                    f"Selected-period prices: minimum {float(prices['min']):.2f} EUR/MWh, "
                    f"maximum {float(prices['max']):.2f} EUR/MWh, mean {float(prices['mean']):.2f} EUR/MWh."
                )
    groupings = model.technical.get("time_groupings")
    if groupings is not None:
        with st.expander("Injection time groupings", expanded=False):
            st.caption(
                "Peak and off-peak energy windows are an audit grouping. They are not the "
                "fixed tariffs applied to Dynamic injection tariff."
            )
            _table(groupings)


def _size_help(size: int) -> str:
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} kB"
    return f"{size / (1024 * 1024):.1f} MB"


def _render_downloads(model: ComparisonDisplay, state: dict[str, Any]) -> None:
    folder = Path(model.folder)
    if model.downloads["demo"]:
        st.write("This is the stored demonstration audit folder. It was not produced by a new run.")
    else:
        st.write(f"This completed run is stored as `{model.downloads['folder_name']}`.")
    inventory = inventory_rows(folder)
    if inventory:
        _table(inventory)
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
        key="v2-cmp-audit-zip",
        help="Ordinary audit files and stored plot images. Large dispatch files are offered separately.",
    )
    prices_size = file_size(folder, "dynamic_injection_prices.parquet")
    if prices_size is not None:
        payload = read_contained_bytes(folder, "dynamic_injection_prices.parquet")
        if payload is not None:
            st.download_button(
                "Download selected-period day-ahead prices",
                data=payload,
                file_name="dynamic_injection_prices.parquet",
                mime="application/vnd.apache.parquet",
                key="v2-cmp-da-prices",
            )
    for filename, label, flag in (
        ("comparison_dispatch.parquet", "Prepare dispatch Parquet", _PREP_PARQUET),
        ("comparison_dispatch.csv", "Prepare full dispatch CSV", _PREP_CSV),
    ):
        size = file_size(folder, filename)
        if size is None:
            continue
        if not bool(st.session_state.get(flag)):
            if st.button(f"{label} ({_size_help(size)})", key=f"{flag}-btn"):
                st.session_state[flag] = True
                st.rerun()
        else:
            payload = read_contained_bytes(folder, filename)
            if payload is not None:
                st.download_button(
                    f"Download {filename}",
                    data=payload,
                    file_name=filename,
                    key=f"{flag}-dl",
                )
    if st.button("Return to Configure options", type="secondary", key="v2-cmp-back-configure"):
        back_to_step4(state)
        st.rerun()


def render_comparison_results(state: dict[str, Any]) -> bool:
    results = state.get("results") if isinstance(state.get("results"), Mapping) else None
    if comparison_display_guard(results) is not None:
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
    except ComparisonDisplayError:
        return False
    header = model.header
    with st.container(key="v2-compare-results"):
        render_page_header("Step 6 of 6", header["title"], header["source_line"])
        for note in model.notes:
            st.caption(note)
        render_metric_group(
            [
                ("Period", header["period_label"] or "—"),
                ("Battery", header["battery_fact"]),
                ("Dispatch strategies", header["case_count"]),
            ],
            key="v2-metrics-header",
        )
        tabs = st.tabs(list(TAB_NAMES))
        with tabs[0]:
            _render_overview(model)
        with tabs[1]:
            _render_energy(model)
        with tabs[2]:
            _render_peaks(model)
        with tabs[3]:
            _render_revenue(model)
        with tabs[4]:
            _render_explorer(model)
        with tabs[5]:
            _render_technical(model)
        with tabs[6]:
            _render_downloads(model, state)
    return True
