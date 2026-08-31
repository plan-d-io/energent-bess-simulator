"""Plotly figures for battery-size Results. Uses V2 tokens and interaction."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd
import plotly.graph_objects as go

from ui.services.sweep_display import (
    allowed_cycles,
    capture_candidate_ids,
    duration_hours_list,
    revenue_increase_phrase,
    shortest_payback_id,
)
from ui.services.sweep_format import (
    duration_label,
    fmt_payback_years,
    is_missing,
    payback_is_applicable,
)
from ui.presentation import tokens as t

PLOTLY_CONFIG = {
    "displaylogo": False,
    "doubleClick": "reset",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
}

_SINGLE_BOTTOM_MARGIN = 128
_DURATION_COLOURS = (
    t.CHART_SELF_CONSUMPTION,
    t.CHART_PEAK_REDUCTION,
    t.CHART_REVENUE,
    t.CHART_DYNAMIC,
)


def duration_colour_map(summary: Mapping[str, Any]) -> dict[str, str]:
    colours: dict[str, str] = {}
    for index, hours in enumerate(duration_hours_list(summary)):
        colours[duration_label(hours)] = _DURATION_COLOURS[index % len(_DURATION_COLOURS)]
    return colours


def chart_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    capture = capture_candidate_ids(summary)
    rows: list[dict[str, Any]] = []
    for item in summary.get("candidates") or []:
        flags: list[str] = []
        if item.get("cycle_limit_binding"):
            flags.append("Cycle-limited")
        if str(item.get("candidate_id")) in capture:
            flags.append("Revenue-capture")
        rows.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "duration_label": duration_label(item["duration_hours"]),
                "power_kw": item.get("power_kw"),
                "usable_energy_kwh": item.get("usable_energy_kwh"),
                "duration_hours": item.get("duration_hours"),
                "estimated_capex_eur": item.get("estimated_capex_eur"),
                "annual_revenue_uplift_eur": item.get("annual_revenue_uplift_eur"),
                "simple_payback_years": item.get("simple_payback_years"),
                "equivalent_full_cycles": item.get("equivalent_full_cycles"),
                "cycle_limit_binding": bool(item.get("cycle_limit_binding")),
                "charge_pv_kwh": item.get("charge_pv_kwh"),
                "discharge_load_kwh": item.get("discharge_load_kwh"),
                "total_loss_kwh": item.get("total_loss_kwh"),
                "stored_throughput_kwh": item.get("stored_throughput_kwh"),
                "remaining_equivalent_full_cycles_allowance": item.get(
                    "remaining_equivalent_full_cycles_allowance"
                ),
                "average_monthly_peak_kw": item.get("average_monthly_peak_kw"),
                "average_monthly_peak_reduction_kw": item.get("average_monthly_peak_reduction_kw"),
                "average_monthly_peak_reduction_pct": item.get("average_monthly_peak_reduction_pct"),
                "baseline_average_monthly_peak_kw": item.get("baseline_average_monthly_peak_kw"),
                "annual_peak_kw": item.get("annual_peak_kw"),
                "annual_peak_reduction_kw": item.get("annual_peak_reduction_kw"),
                "annual_peak_reduction_pct": item.get("annual_peak_reduction_pct"),
                "flags": ", ".join(flags) if flags else "None",
            }
        )
    return rows


def _hover_custom(part: pd.DataFrame) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for _, item in part.iterrows():
        rows.append(
            [
                item["power_kw"],
                item["usable_energy_kwh"],
                item["duration_label"],
                item["estimated_capex_eur"],
                item["annual_revenue_uplift_eur"],
                fmt_payback_years(item["simple_payback_years"]),
                item["flags"],
            ]
        )
    return rows


def _revenue_hover(summary: Mapping[str, Any]) -> str:
    phrase = revenue_increase_phrase(summary)
    return (
        "Power: %{customdata[0]:.6g} kW<br>"
        "Usable energy: %{customdata[1]:.6g} kWh<br>"
        "Duration: %{customdata[2]}<br>"
        "CAPEX: EUR %{customdata[3]:,.0f}<br>"
        f"{phrase}: EUR %{{customdata[4]:,.0f}}<br>"
        "Simple payback: %{customdata[5]}<br>"
        "Flags: %{customdata[6]}"
        "<extra></extra>"
    )


def _legend() -> dict[str, Any]:
    return dict(
        orientation="h",
        y=-0.34,
        yanchor="top",
        x=0,
        xanchor="left",
        bgcolor="rgba(255,255,255,0)",
        borderwidth=0,
        itemclick="toggle",
        itemdoubleclick="toggleothers",
        tracegroupgap=0,
        font=dict(size=12),
    )


def _finish(fig: go.Figure, *, x_title: str, y_title: str, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height + _SINGLE_BOTTOM_MARGIN,
        margin=dict(l=56, r=16, t=24, b=_SINGLE_BOTTOM_MARGIN),
        paper_bgcolor=t.CHART_PAPER,
        plot_bgcolor=t.CHART_PAPER,
        font=dict(color=t.TEXT, size=13),
        legend=_legend(),
        hovermode="closest",
        dragmode="zoom",
    )
    fig.update_xaxes(
        title_text=x_title,
        fixedrange=False,
        gridcolor=t.CHART_GRID,
        linecolor=t.BORDER,
        zeroline=False,
    )
    fig.update_yaxes(
        title_text=y_title,
        fixedrange=True,
        gridcolor=t.CHART_GRID,
        linecolor=t.BORDER,
        zeroline=False,
    )
    return fig


def _duration_traces(
    fig: go.Figure,
    frame: pd.DataFrame,
    y_field: str,
    hover: str,
    colours: Mapping[str, str],
) -> None:
    for name in list(dict.fromkeys(frame["duration_label"].tolist())):
        part = frame.loc[frame["duration_label"] == name]
        if part.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=part["power_kw"],
                y=part[y_field],
                name=name,
                mode="lines+markers",
                line=dict(color=colours.get(name, t.CHART_REFERENCE), width=2),
                marker=dict(size=7),
                customdata=_hover_custom(part),
                hovertemplate=hover,
            )
        )


def _mark(
    fig: go.Figure,
    frame: pd.DataFrame,
    candidate_id: str | None,
    y_field: str,
    name: str,
    hover: str,
    symbol: str,
    colour: str,
) -> None:
    if not candidate_id:
        return
    mark = frame.loc[frame["candidate_id"] == candidate_id]
    if mark.empty:
        return
    fig.add_trace(
        go.Scatter(
            x=mark["power_kw"],
            y=mark[y_field],
            name=name,
            mode="markers",
            marker=dict(size=14, symbol=symbol, color=colour),
            customdata=_hover_custom(mark),
            hovertemplate=hover,
        )
    )


def build_payback_chart(summary: Mapping[str, Any]) -> go.Figure:
    frame = pd.DataFrame(chart_rows(summary))
    work = frame.loc[frame["simple_payback_years"].map(payback_is_applicable)].copy()
    fig = go.Figure()
    hover = _revenue_hover(summary)
    colours = duration_colour_map(summary)
    if not work.empty:
        _duration_traces(fig, work, "simple_payback_years", hover, colours)
        _mark(
            fig,
            work,
            shortest_payback_id(summary),
            "simple_payback_years",
            "Shortest simple payback",
            hover,
            "star",
            t.CHART_REVENUE,
        )
    years = None
    screening = summary.get("screening_summary") if isinstance(summary.get("screening_summary"), Mapping) else {}
    if isinstance(screening, Mapping) and screening.get("screening_period_years") is not None:
        years = float(screening["screening_period_years"])
    elif (summary.get("sweep") or {}).get("evaluation_period_years") is not None:
        years = float((summary.get("sweep") or {})["evaluation_period_years"])
    if years is not None:
        fig.add_hline(
            y=years,
            line_dash="dash",
            line_color=t.TEXT_SECONDARY,
            annotation_text=f"{years:g}-year screening period",
            annotation_position="top left",
        )
    fig = _finish(fig, x_title="Battery power (kW)", y_title="Simple payback period (years)")
    fig.update_yaxes(tickformat=".1f")
    return fig


def build_revenue_chart(summary: Mapping[str, Any]) -> go.Figure:
    frame = pd.DataFrame(chart_rows(summary))
    fig = go.Figure()
    hover = _revenue_hover(summary)
    colours = duration_colour_map(summary)
    _duration_traces(fig, frame, "annual_revenue_uplift_eur", hover, colours)
    capture_ids = capture_candidate_ids(summary)
    if capture_ids:
        marks = frame.loc[frame["candidate_id"].isin(capture_ids)]
        if not marks.empty:
            fig.add_trace(
                go.Scatter(
                    x=marks["power_kw"],
                    y=marks["annual_revenue_uplift_eur"],
                    name="Revenue-capture",
                    mode="markers",
                    marker=dict(size=11, symbol="diamond", color=t.CHART_DYNAMIC),
                    customdata=_hover_custom(marks),
                    hovertemplate=hover,
                )
            )
    phrase = revenue_increase_phrase(summary)
    fig = _finish(fig, x_title="Battery power (kW)", y_title=f"{phrase} (EUR/year)")
    fig.update_yaxes(tickformat=",.0f")
    return fig


def _peak_hover(summary: Mapping[str, Any], *, monthly: bool) -> str:
    phrase = revenue_increase_phrase(summary)
    if monthly:
        return (
            "Power: %{customdata[0]:.6g} kW<br>"
            "Usable energy: %{customdata[1]:.6g} kWh<br>"
            "Duration: %{customdata[2]}<br>"
            "Average monthly peak reduction: %{customdata[7]:.1f} kW<br>"
            f"{phrase}: EUR %{{customdata[4]:,.0f}}<br>"
            "Simple payback: %{customdata[5]}"
            "<extra></extra>"
        )
    return (
        "Power: %{customdata[0]:.6g} kW<br>"
        "Usable energy: %{customdata[1]:.6g} kWh<br>"
        "Duration: %{customdata[2]}<br>"
        "Highest 15-minute grid import: %{customdata[8]:.1f} kW<br>"
        f"{phrase}: EUR %{{customdata[4]:,.0f}}<br>"
        "Simple payback: %{customdata[5]}"
        "<extra></extra>"
    )


def _peak_custom(part: pd.DataFrame) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for _, item in part.iterrows():
        rows.append(
            [
                item["power_kw"],
                item["usable_energy_kwh"],
                item["duration_label"],
                item["estimated_capex_eur"],
                item["annual_revenue_uplift_eur"],
                fmt_payback_years(item["simple_payback_years"]),
                item["flags"],
                item.get("average_monthly_peak_reduction_kw"),
                item.get("annual_peak_kw"),
            ]
        )
    return rows


def build_monthly_peak_chart(summary: Mapping[str, Any]) -> go.Figure | None:
    frame = pd.DataFrame(chart_rows(summary))
    work = frame.dropna(subset=["average_monthly_peak_reduction_kw"]).copy()
    if work.empty:
        return None
    fig = go.Figure()
    hover = _peak_hover(summary, monthly=True)
    colours = duration_colour_map(summary)
    for name in list(dict.fromkeys(work["duration_label"].tolist())):
        part = work.loc[work["duration_label"] == name]
        fig.add_trace(
            go.Scatter(
                x=part["power_kw"],
                y=part["average_monthly_peak_reduction_kw"],
                name=name,
                mode="lines+markers",
                line=dict(color=colours.get(name, t.CHART_REFERENCE), width=2),
                marker=dict(size=7),
                customdata=_peak_custom(part),
                hovertemplate=hover,
            )
        )
    peaks = summary.get("peak_summary") if isinstance(summary.get("peak_summary"), Mapping) else {}
    snap = (peaks or {}).get("largest_average_monthly_peak_reduction_candidate") or {}
    mark_id = str(snap.get("candidate_id") or "") or None
    if mark_id:
        mark = work.loc[work["candidate_id"] == mark_id]
        if not mark.empty:
            fig.add_trace(
                go.Scatter(
                    x=mark["power_kw"],
                    y=mark["average_monthly_peak_reduction_kw"],
                    name="Largest average monthly peak reduction",
                    mode="markers",
                    marker=dict(size=14, symbol="star", color=t.CHART_REVENUE),
                    customdata=_peak_custom(mark),
                    hovertemplate=hover,
                )
            )
    return _finish(
        fig,
        x_title="Battery power (kW)",
        y_title="Average monthly peak reduction (kW)",
    )


def build_interval_peak_chart(summary: Mapping[str, Any]) -> go.Figure | None:
    frame = pd.DataFrame(chart_rows(summary))
    work = frame.dropna(subset=["annual_peak_kw"]).copy()
    if work.empty:
        return None
    fig = go.Figure()
    hover = _peak_hover(summary, monthly=False)
    colours = duration_colour_map(summary)
    for name in list(dict.fromkeys(work["duration_label"].tolist())):
        part = work.loc[work["duration_label"] == name]
        fig.add_trace(
            go.Scatter(
                x=part["power_kw"],
                y=part["annual_peak_kw"],
                name=name,
                mode="lines+markers",
                line=dict(color=colours.get(name, t.CHART_REFERENCE), width=2),
                marker=dict(size=7),
                customdata=_peak_custom(part),
                hovertemplate=hover,
            )
        )
    peaks = summary.get("peak_summary") if isinstance(summary.get("peak_summary"), Mapping) else {}
    baseline = (peaks or {}).get("baseline_annual_peak_kw")
    if not is_missing(baseline):
        fig.add_hline(
            y=float(baseline),
            line_dash="dash",
            line_color=t.TEXT_SECONDARY,
            annotation_text="No-battery baseline",
            annotation_position="top left",
        )
    snap = (peaks or {}).get("largest_highest_interval_peak_reduction_candidate") or {}
    mark_id = str(snap.get("candidate_id") or "") or None
    if mark_id:
        mark = work.loc[work["candidate_id"] == mark_id]
        if not mark.empty:
            fig.add_trace(
                go.Scatter(
                    x=mark["power_kw"],
                    y=mark["annual_peak_kw"],
                    name="Largest reduction in highest 15-minute import",
                    mode="markers",
                    marker=dict(size=11, symbol="diamond", color=t.CHART_REVENUE),
                    customdata=_peak_custom(mark),
                    hovertemplate=hover,
                )
            )
    return _finish(
        fig,
        x_title="Battery power (kW)",
        y_title="Highest 15-minute grid import (kW)",
    )


def _cycle_hover() -> str:
    return (
        "Power: %{customdata[0]:.6g} kW<br>"
        "Usable energy: %{customdata[1]:.6g} kWh<br>"
        "Duration: %{customdata[2]}<br>"
        "Equivalent full cycles: %{y:.1f}<br>"
        "Charge from PV: %{customdata[7]:.1f} kWh<br>"
        "Discharge to customer: %{customdata[8]:.1f} kWh<br>"
        "Losses: %{customdata[9]:.1f} kWh<br>"
        "Throughput: %{customdata[10]:.1f} kWh<br>"
        "Remaining cycle allowance: %{customdata[11]:.1f}<br>"
        "Cycle limit reached: %{customdata[12]}"
        "<extra></extra>"
    )


def _cycle_custom(part: pd.DataFrame) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for _, item in part.iterrows():
        rows.append(
            [
                item["power_kw"],
                item["usable_energy_kwh"],
                item["duration_label"],
                item["estimated_capex_eur"],
                item["annual_revenue_uplift_eur"],
                fmt_payback_years(item["simple_payback_years"]),
                item["flags"],
                item.get("charge_pv_kwh"),
                item.get("discharge_load_kwh"),
                item.get("total_loss_kwh"),
                item.get("stored_throughput_kwh"),
                item.get("remaining_equivalent_full_cycles_allowance"),
                "Yes" if item.get("cycle_limit_binding") else "No",
            ]
        )
    return rows


def build_cycles_chart(summary: Mapping[str, Any]) -> go.Figure:
    frame = pd.DataFrame(chart_rows(summary))
    work = frame.dropna(subset=["equivalent_full_cycles"]).copy()
    fig = go.Figure()
    hover = _cycle_hover()
    colours = duration_colour_map(summary)
    for name in list(dict.fromkeys(work["duration_label"].tolist())):
        part = work.loc[work["duration_label"] == name]
        fig.add_trace(
            go.Scatter(
                x=part["power_kw"],
                y=part["equivalent_full_cycles"],
                name=name,
                mode="lines+markers",
                line=dict(color=colours.get(name, t.CHART_REFERENCE), width=2),
                marker=dict(size=7),
                customdata=_cycle_custom(part),
                hovertemplate=hover,
            )
        )
    allowance = allowed_cycles(summary)
    if allowance is not None:
        fig.add_hline(
            y=allowance,
            line_dash="dash",
            line_color=t.TEXT_SECONDARY,
            annotation_text="Configured cycle allowance",
            annotation_position="top left",
        )
    return _finish(fig, x_title="Battery power (kW)", y_title="Equivalent full cycles")


def figure_contains_estimated_value(fig: go.Figure) -> bool:
    blob = str(fig.to_plotly_json())
    return "estimated value" in blob.lower() or "estimated_value" in blob.lower()


def hovertemplates(fig: go.Figure) -> list[str]:
    texts: list[str] = []
    for trace in fig.data:
        template = getattr(trace, "hovertemplate", None)
        if template:
            texts.append(str(template))
    return texts


def series_names(fig: go.Figure) -> list[str]:
    return [str(trace.name) for trace in fig.data]


def y_values(fig: go.Figure, name: str) -> list[Any]:
    for trace in fig.data:
        if str(trace.name) == name:
            return list(trace.y)
    return []
