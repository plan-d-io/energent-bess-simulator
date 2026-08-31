from __future__ import annotations

from ui.services.compare_display import load_comparison_display
from ui.services.compare_explorer import (
    AUTUMN_DST_WEEK_INTERVALS,
    ORDINARY_WEEK_INTERVALS,
    SPRING_DST_WEEK_INTERVALS,
    explorer_chart_models,
    iso_weeks_wholly_inside,
    query_dispatch_week,
    seasonal_windows,
    week_csv_bytes,
)
from ui.services.saved_example import compare_artifact_dir

GANDA = compare_artifact_dir()
PARQUET = GANDA / "comparison_dispatch.parquet"


def test_seasonal_week_query_uses_utc_bounds_and_physical_count() -> None:
    model = load_comparison_display(GANDA)
    winter = next(window for window in seasonal_windows(model.summary) if window.season == "winter")
    result = query_dispatch_week(
        PARQUET, winter.start_utc, winter.end_utc_exclusive, "self_consumption"
    )
    assert result.n_rows == ORDINARY_WEEK_INTERVALS
    assert result.start_utc == winter.start_utc
    assert result.end_utc_exclusive == winter.end_utc_exclusive
    assert f"self_consumption_grid_import_kwh" in result.columns
    import pandas as pd

    stamps = pd.to_datetime(result.frame["timestamp_utc"], utc=True)
    assert stamps.min() >= pd.Timestamp(winter.start_utc)
    assert stamps.max() < pd.Timestamp(winter.end_utc_exclusive)
    csv = week_csv_bytes(result.frame)
    assert csv.count(b"\n") == result.n_rows + 1


def test_dst_week_counts_are_physical_quarter_hours() -> None:
    model = load_comparison_display(GANDA)
    period = model.summary["selected_period"]
    weeks = iso_weeks_wholly_inside(period["start_local"], period["end_local_exclusive"])
    spring = next(window for window in weeks if window.iso_year == 2024 and window.iso_week == 13)
    autumn = next(window for window in weeks if window.iso_year == 2024 and window.iso_week == 43)
    spring_result = query_dispatch_week(
        PARQUET, spring.start_utc, spring.end_utc_exclusive, "peak_reduction"
    )
    autumn_result = query_dispatch_week(
        PARQUET, autumn.start_utc, autumn.end_utc_exclusive, "peak_reduction"
    )
    assert spring_result.n_rows == SPRING_DST_WEEK_INTERVALS
    assert autumn_result.n_rows == AUTUMN_DST_WEEK_INTERVALS


def test_price_panel_only_for_dynamic_with_stored_prices() -> None:
    model = load_comparison_display(GANDA)
    winter = next(window for window in seasonal_windows(model.summary) if window.season == "winter")
    self_result = query_dispatch_week(
        PARQUET, winter.start_utc, winter.end_utc_exclusive, "self_consumption"
    )
    dyn_result = query_dispatch_week(
        PARQUET, winter.start_utc, winter.end_utc_exclusive, "dynamic_injection"
    )
    self_titles = [panel.title for panel in explorer_chart_models(self_result.frame, scenario="self_consumption")]
    dyn_titles = [panel.title for panel in explorer_chart_models(dyn_result.frame, scenario="dynamic_injection")]
    assert "Day-ahead injection price" not in self_titles
    assert "Day-ahead injection price" in dyn_titles
    for panel in explorer_chart_models(dyn_result.frame, scenario="dynamic_injection"):
        assert panel.x_title == "Local time"
        assert panel.y_title in {"Power (kW)", "Stored energy (kWh)", "Price (EUR/MWh)"}


def test_explorer_line_colours_are_distinct_within_each_panel() -> None:
    from ui.services.compare_display import energy_monthly_models
    from ui.presentation.tokens import CHART_EXPLORER, CHART_SERIES

    model = load_comparison_display(GANDA)
    winter = next(window for window in seasonal_windows(model.summary) if window.season == "winter")
    result = query_dispatch_week(
        PARQUET, winter.start_utc, winter.end_utc_exclusive, "self_consumption"
    )
    panels = explorer_chart_models(result.frame, scenario="self_consumption")
    for panel in panels:
        colours = [colour.lower() for _name, colour in panel.colours]
        assert len(set(colours)) == len(colours)
    grid = next(panel for panel in panels if panel.title == "Grid import and PV injection")
    grid_colours = dict(grid.colours)
    assert grid_colours["Grid import - no battery"] == CHART_EXPLORER["Grid import - no battery"]
    assert grid_colours["Grid import - Self-consumption"] == CHART_EXPLORER["Grid import - battery"]
    assert grid_colours["PV injection - Self-consumption"] == CHART_EXPLORER["PV injection - battery"]
    battery = next(panel for panel in panels if panel.title == "Battery charging and discharging")
    assert len({colour for _name, colour in battery.colours}) >= 2
    _table, monthly = energy_monthly_models(
        model.monthly, scenario="self_consumption", label="Self-consumption"
    )
    monthly_grid = dict(monthly[1].colours)
    monthly_pv = dict(monthly[0].colours)
    assert monthly_grid["Grid import - no battery"] == CHART_EXPLORER["Grid import - no battery"]
    assert monthly_grid["PV injection - no battery"] == CHART_EXPLORER["PV injection - no battery"]
    assert monthly_grid["Grid import - battery"] == CHART_EXPLORER["Grid import - battery"]
    assert monthly_grid["PV injection - battery"] == CHART_EXPLORER["PV injection - battery"]
    assert monthly_grid["Grid import - battery"] != monthly_grid["PV injection - battery"]
    assert len(set(monthly_pv.values())) == len(monthly_pv)
    assert len(set(monthly_grid.values())) == len(monthly_grid)
    assert grid_colours["PV injection - Self-consumption"] != grid_colours["Grid import - Self-consumption"]
    assert grid_colours["PV injection - Self-consumption"] != CHART_SERIES["PV injection - battery"]
