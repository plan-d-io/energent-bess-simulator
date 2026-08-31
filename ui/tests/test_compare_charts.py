from __future__ import annotations

from ui.services.compare_charts import altair_chart, plotly_line_figure, plotly_linked_figures
from ui.services.compare_display import (
    energy_monthly_models,
    load_comparison_display,
    peaks_chart_model,
)
from ui.services.compare_format import case_label, fmt_eur
from ui.services.saved_example import compare_artifact_dir

GANDA = compare_artifact_dir()


def test_fmt_eur_keeps_currency_and_amount_together() -> None:
    text = fmt_eur(16119)
    assert "\u00a0" in text
    assert text.split("\u00a0")[0] == "EUR"
    assert " " not in text


def test_line_charts_use_horizontal_zoom_and_bottom_legend() -> None:
    model = load_comparison_display(GANDA)
    _table, charts = energy_monthly_models(
        model.monthly,
        scenario="self_consumption",
        label=case_label("self_consumption"),
    )
    linked = plotly_linked_figures(charts)
    assert linked.layout.dragmode == "zoom"
    assert linked.layout.yaxis.fixedrange is True
    assert linked.layout.yaxis2.fixedrange is True
    assert linked.layout.legend.orientation == "h"
    assert linked.layout.legend.xanchor == "left"
    assert float(linked.layout.legend.x or 0) <= 0.05
    assert linked.layout.legend2.orientation == "h"
    top_domain = float(linked.layout.yaxis.domain[0])
    bottom_domain = float(linked.layout.yaxis2.domain[0])
    assert float(linked.layout.legend.y) < top_domain - 0.03
    assert float(linked.layout.legend2.y) < bottom_domain - 0.03
    assert linked.layout.margin.b >= 120

    peaks = plotly_line_figure(peaks_chart_model(model.summary, model.monthly_peaks))
    assert peaks.layout.dragmode == "zoom"
    assert peaks.layout.yaxis.fixedrange is True
    assert peaks.layout.legend.orientation == "h"
    assert float(peaks.layout.legend.y) <= -0.3
    assert peaks.layout.margin.b >= 120


def test_altair_legend_sits_below_the_chart() -> None:
    model = load_comparison_display(GANDA)
    from ui.services.compare_display import revenue_monthly_models

    _table, charts = revenue_monthly_models(
        model.monthly,
        scenario="self_consumption",
        label=case_label("self_consumption"),
    )
    chart = altair_chart(charts[1])
    spec = chart.to_dict()
    legend = spec["encoding"]["color"]["legend"]
    assert legend["orient"] == "bottom"
    assert spec["height"] == 320
    assert spec.get("width") != "container"
    stacked = altair_chart(charts[2]).to_dict()
    assert stacked["height"] == 320
    assert stacked["encoding"]["y"].get("stack") == "zero"


def _panel_spans(fig, count: int) -> list[float]:
    spans: list[float] = []
    for index in range(count):
        axis = "yaxis" if index == 0 else f"yaxis{index + 1}"
        domain = list(fig.layout[axis].domain)
        spans.append(float(domain[1]) - float(domain[0]))
    return spans


def test_linked_panels_use_equal_plot_heights() -> None:
    from ui.services.compare_explorer import (
        explorer_chart_models,
        query_dispatch_week,
        seasonal_windows,
    )

    model = load_comparison_display(GANDA)
    winter = next(window for window in seasonal_windows(model.summary) if window.season == "winter")
    result = query_dispatch_week(
        GANDA / "comparison_dispatch.parquet",
        winter.start_utc,
        winter.end_utc_exclusive,
        "self_consumption",
    )
    panels = explorer_chart_models(result.frame, scenario="self_consumption")
    fig = plotly_linked_figures(panels, height=240)
    spans = _panel_spans(fig, len(panels))
    assert max(spans) - min(spans) < 1e-9
    _table, monthly = energy_monthly_models(
        model.monthly,
        scenario="self_consumption",
        label=case_label("self_consumption"),
    )
    monthly_fig = plotly_linked_figures(monthly)
    monthly_spans = _panel_spans(monthly_fig, len(monthly))
    assert max(monthly_spans) - min(monthly_spans) < 1e-9
