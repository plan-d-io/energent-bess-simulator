"""Plotly and Altair figures from comparison chart models."""

from __future__ import annotations

import altair as alt
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ui.services.compare_display import ChartSpec
from ui.presentation import tokens as t

PLOTLY_CONFIG = {
    "displaylogo": False,
    "doubleClick": "reset",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
}

# Room below each plot for tick labels plus a one-row horizontal legend.
_TICK_LABEL_PX = 64
_PANEL_GAP_PX = 140
_SINGLE_BOTTOM_MARGIN = 128
_ALTAIR_HEIGHT = 320


def _frame(spec: ChartSpec) -> pd.DataFrame:
    return pd.DataFrame(list(spec.rows))


def _colour_map(spec: ChartSpec) -> dict[str, str]:
    return dict(spec.colours)


def _legend_below(index: int = 0, *, y: float) -> dict:
    key = "legend" if index == 0 else f"legend{index + 1}"
    return {
        key: dict(
            orientation="h",
            y=y,
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
    }


def _equal_panel_domains(count: int, spacing: float) -> list[tuple[float, float]]:
    """Top-to-bottom paper domains of equal height. Row 1 is the top subplot."""
    usable = 1.0 - spacing * (count - 1)
    span = usable / count
    domains: list[tuple[float, float]] = []
    top = 1.0
    for _ in range(count):
        bottom = top - span
        domains.append((bottom, top))
        top = bottom - spacing
    return domains


def _apply_equal_panel_domains(fig: go.Figure, count: int, spacing: float) -> None:
    for index, (bottom, top) in enumerate(_equal_panel_domains(count, spacing)):
        fig.update_yaxes(domain=[bottom, top], automargin=False, row=index + 1, col=1)


def _x_only_zoom(fig: go.Figure) -> None:
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(dragmode="zoom", hovermode="x unified")


def _linked_geometry(count: int, panel_height: int) -> tuple[int, float, float]:
    """Figure height, subplot gap, and legend offset below each y-axis domain."""
    gap = _PANEL_GAP_PX if count <= 2 else 168
    fig_height = panel_height * count + gap * (count - 1) + 52 + _SINGLE_BOTTOM_MARGIN
    spacing = gap / fig_height
    offset = (_TICK_LABEL_PX + 8) / fig_height
    return fig_height, spacing, offset


def plotly_line_figure(spec: ChartSpec, *, height: int = 320, point: bool = False) -> go.Figure:
    frame = _frame(spec)
    colours = _colour_map(spec)
    fig = make_subplots(rows=1, cols=1)
    mode = "lines+markers" if point else "lines"
    hover = f"%{{x}}<br>%{{fullData.name}}: %{{y:{spec.value_format}}}<extra></extra>"
    order = list(spec.series_order) or list(dict.fromkeys(frame["Series"].tolist()))
    x_field = "Month" if spec.x_type != "time" else "Time"
    for name in order:
        part = frame.loc[frame["Series"] == name]
        if part.empty:
            continue
        x = pd.to_datetime(part["Time"]) if spec.x_type == "time" else part[x_field]
        fig.add_trace(
            go.Scatter(
                x=x,
                y=part["Value"],
                name=name,
                mode=mode,
                line=dict(color=colours.get(name, t.CHART_REFERENCE), width=2),
                hovertemplate=hover,
            )
        )
    if spec.x_type == "time":
        fig.update_xaxes(title_text=spec.x_title)
    else:
        categories = list(dict.fromkeys(frame[x_field].tolist()))
        fig.update_xaxes(
            title_text=spec.x_title,
            type="category",
            categoryorder="array",
            categoryarray=categories,
        )
    fig.update_yaxes(title_text=spec.y_title)
    fig.update_layout(
        height=height + _SINGLE_BOTTOM_MARGIN,
        margin=dict(l=56, r=16, t=48, b=_SINGLE_BOTTOM_MARGIN),
        paper_bgcolor=t.CHART_PAPER,
        plot_bgcolor=t.CHART_PAPER,
        font=dict(color=t.TEXT, size=13),
        title=dict(text=spec.title, font=dict(size=15, color=t.TEXT)),
        uirevision=spec.title,
        **_legend_below(y=-0.34),
    )
    _x_only_zoom(fig)
    fig.update_xaxes(gridcolor=t.CHART_GRID, linecolor=t.BORDER, zeroline=False)
    fig.update_yaxes(gridcolor=t.CHART_GRID, linecolor=t.BORDER, zeroline=False)
    return fig


def plotly_linked_figures(specs: tuple[ChartSpec, ...], *, height: int = 280) -> go.Figure:
    if len(specs) == 1:
        return plotly_line_figure(specs[0], height=height, point=False)
    count = len(specs)
    fig_height, spacing, offset = _linked_geometry(count, height)
    fig = make_subplots(
        rows=count,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=spacing,
        row_heights=[1] * count,
        subplot_titles=[spec.title for spec in specs],
    )
    for row, spec in enumerate(specs, start=1):
        frame = _frame(spec)
        colours = _colour_map(spec)
        hover = f"%{{x}}<br>%{{fullData.name}}: %{{y:{spec.value_format}}}<extra></extra>"
        order = list(spec.series_order) or list(dict.fromkeys(frame["Series"].tolist()))
        legend = "legend" if row == 1 else f"legend{row}"
        for name in order:
            part = frame.loc[frame["Series"] == name]
            if part.empty:
                continue
            x = pd.to_datetime(part["Time"]) if spec.x_type == "time" else part["Month"]
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=part["Value"],
                    name=name,
                    mode="lines",
                    line=dict(color=colours.get(name, t.CHART_REFERENCE), width=2),
                    hovertemplate=hover,
                    legend=legend,
                    legendgroup=f"{spec.title}:{name}",
                ),
                row=row,
                col=1,
            )
        fig.update_yaxes(title_text=spec.y_title, row=row, col=1, gridcolor=t.CHART_GRID)
        fig.update_xaxes(
            title_text=spec.x_title if row == count else "",
            row=row,
            col=1,
            gridcolor=t.CHART_GRID,
        )
    fig.update_xaxes(showticklabels=True, matches="x")
    _apply_equal_panel_domains(fig, count, spacing)
    legend_layout: dict = {}
    for index, (bottom, _top) in enumerate(_equal_panel_domains(count, spacing)):
        legend_layout.update(_legend_below(index, y=bottom - offset))
    fig.update_layout(
        height=fig_height,
        margin=dict(l=56, r=16, t=48, b=_SINGLE_BOTTOM_MARGIN),
        paper_bgcolor=t.CHART_PAPER,
        plot_bgcolor=t.CHART_PAPER,
        font=dict(color=t.TEXT, size=13),
        uirevision="linked",
        **legend_layout,
    )
    _apply_equal_panel_domains(fig, count, spacing)
    _x_only_zoom(fig)
    _apply_equal_panel_domains(fig, count, spacing)
    return fig


def altair_chart(spec: ChartSpec, *, height: int = _ALTAIR_HEIGHT) -> alt.Chart:
    frame = _frame(spec)
    colours = _colour_map(spec)
    domain = list(spec.series_order) or list(dict.fromkeys(frame["Series"].tolist()))
    rng = [colours.get(name, t.CHART_REFERENCE) for name in domain]
    x = alt.X("Month:N", title=spec.x_title, sort=list(dict.fromkeys(frame["Month"].tolist())))
    y = alt.Y("Value:Q", title=spec.y_title, stack="zero" if spec.kind == "stacked_bar" else None)
    color = alt.Color(
        "Series:N",
        title="Series",
        scale=alt.Scale(domain=domain, range=rng),
        sort=domain,
        legend=alt.Legend(orient="bottom", direction="horizontal", columns=2, title="Series"),
    )
    tooltip = [
        alt.Tooltip("Month:N", title="Month"),
        alt.Tooltip("Series:N", title="Series"),
        alt.Tooltip("Value:Q", title=spec.y_title, format=spec.value_format.replace(",", "")),
    ]
    if spec.kind in {"stacked_bar", "bar"}:
        encoded = alt.Chart(frame).mark_bar().encode(x=x, y=y, color=color, tooltip=tooltip)
    else:
        encoded = alt.Chart(frame).mark_line(point=True).encode(x=x, y=y, color=color, tooltip=tooltip)
    return (
        encoded.properties(title=spec.title, height=height)
        .configure_axis(labelColor=t.TEXT_SECONDARY, titleColor=t.TEXT, gridColor=t.CHART_GRID)
        .configure_legend(labelColor=t.TEXT, titleColor=t.TEXT, orient="bottom", direction="horizontal")
        .configure_view(strokeWidth=0)
        .configure_title(color=t.TEXT, fontSize=15, anchor="start")
    )


def chart_has_axes(spec: ChartSpec) -> bool:
    return bool(spec.x_title) and bool(spec.y_title)
