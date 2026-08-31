from __future__ import annotations

import json
import shutil
from pathlib import Path

from ui.services.saved_example import EXPECTED_SWEEP_CANDIDATE_COUNT, sweep_artifact_dir
from ui.services.sweep_charts import (
    build_cycles_chart,
    build_interval_peak_chart,
    build_monthly_peak_chart,
    build_payback_chart,
    build_revenue_chart,
    duration_colour_map,
    figure_contains_estimated_value,
    hovertemplates,
    series_names,
    y_values,
)
from ui.services.sweep_display import (
    SweepDisplayError,
    assumptions_line,
    candidate_display_rows,
    duration_comparison_rows,
    highlight_cards,
    load_sweep_display,
    peak_display_rows,
    screening_headline,
    solver_rows,
    sweep_solver_provenance,
    ui_text_blob,
)
from ui.services.sweep_format import (
    CANDIDATE_TABLE_COLUMNS,
    DURATION_TABLE_COLUMNS,
    FORBIDDEN_UI_PHRASES,
    HISTORICAL_NA,
    HISTORICAL_SCREENING_NOTE,
    TAB_NAMES,
    ZERO_COMPLETE_MONTH_NOTE,
)
from ui.services.compare_format import SWEEP_SOLVER_PROVENANCE_UNAVAILABLE
from ui.presentation import tokens as t

GANDA = sweep_artifact_dir()


def _copy_artifact(tmp_path: Path, *, mutate=None) -> Path:
    dest = tmp_path / "sweep"
    dest.mkdir()
    for name in ("sweep_summary.json", "sweep_summary.csv", "sweep_metadata.json"):
        shutil.copy(GANDA / name, dest / name)
    if mutate is not None:
        path = dest / "sweep_summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        mutate(summary)
        path.write_text(json.dumps(summary), encoding="utf-8")
    return dest


def test_ganda_display_order_headline_and_tabs() -> None:
    model = load_sweep_display(GANDA, site="Ganda Cars", source="demo")
    assert len(model.candidates) == EXPECTED_SWEEP_CANDIDATE_COUNT
    ids = [item["candidate_id"] for item in model.candidates]
    assert ids[0] == "c001_5kW_10kWh"
    assert ids[-1] == "c018_300kW_1200kWh"
    assert len(ids) == len(set(ids))
    headline = screening_headline(model.summary)
    assert "10-year screening period" in headline
    assert "10.1 years" in headline
    assert TAB_NAMES == (
        "Overview",
        "Revenue and payback",
        "Grid peaks",
        "Battery use",
        "Additional details",
        "Downloads",
    )
    assert model.header["title"] == "Battery-size comparison"
    assert model.header["strategy"] == "Revenue maximisation"


def test_highlights_and_duration_table_match_artifact() -> None:
    model = load_sweep_display(GANDA)
    cards = highlight_cards(model.summary)
    assert cards[0]["label"] == "Shortest simple payback"
    assert cards[0]["value"] == "10.1 years"
    assert "5 kW / 10 kWh / 2 h" in cards[0]["lines"][0]
    assert cards[1]["label"] == "Highest annual revenue increase among the tested sizes"
    assert "300 kW / 1200 kWh / 4 h" in cards[1]["lines"][0]
    assert cards[2]["label"] == "Largest average monthly peak reduction among the tested sizes"
    assert "Not included in revenue or payback." in cards[2]["lines"]
    line = assumptions_line(model.summary)
    assert "18 sizes tested" in line
    assert "2 h and 4 h" in line
    assert "EUR 300/kWh usable" in line
    assert "10-year screening period" in line
    assert "400 equivalent full cycles/year" in line
    rows = duration_comparison_rows(model.summary)
    assert list(rows[0]) == list(DURATION_TABLE_COLUMNS)
    assert rows[0]["Duration"] == "2 h"
    assert rows[0]["Shortest-payback battery"] == "5 kW / 10 kWh / 2 h"
    assert rows[0]["Shortest payback (years)"] == "10.1"
    assert "Pays back within screening period" not in rows[0]
    assert "Range note" not in rows[0]
    assert list(model.overview["duration_columns"]) == list(DURATION_TABLE_COLUMNS)
    assert "range_notice" not in model.overview


def test_ui_blob_excludes_ids_estimated_value_and_suggested() -> None:
    model = load_sweep_display(GANDA)
    blob = ui_text_blob(model)
    for phrase in FORBIDDEN_UI_PHRASES:
        assert phrase.lower() not in blob.lower()
    assert "Range note" not in blob
    assert "Revenue is still increasing at the largest tested size" not in blob
    assert "Highest tested revenue occurs at the upper end of this range." not in blob
    assert "No battery is the suggested result" not in blob
    assert "Suggested" not in {row["Flags"] for row in model.sizes["rows"]}
    for row in model.sizes["rows"]:
        joined = " ".join(str(value) for value in row.values())
        assert "c001" not in joined
        assert "estimated_value" not in joined
    for row in model.overview["duration_rows"]:
        joined = " ".join(str(value) for value in row.values())
        assert "c001" not in joined
        assert "c018" not in joined
    for _cid, label in model.transfer["options"]:
        assert "c001" not in label
        assert "c018" not in label
        assert "(" in label
    flags = {row["Flags"] for row in candidate_display_rows(model.summary)}
    assert "Cycle-limited" in " ".join(flags)
    assert "Revenue-capture" in " ".join(flags)
    assert "Range-boundary" in " ".join(flags)
    assert list(model.sizes["columns"]) == list(CANDIDATE_TABLE_COLUMNS)


def test_historical_fields_fail_locally(tmp_path: Path) -> None:
    folder = _copy_artifact(
        tmp_path,
        mutate=lambda summary: (
            summary.pop("screening_summary", None),
            summary.pop("peak_summary", None),
        ),
    )
    model = load_sweep_display(folder)
    assert model.overview["headline"] == HISTORICAL_SCREENING_NOTE
    cards = highlight_cards(model.summary)
    assert cards[0]["value"] == HISTORICAL_NA
    assert cards[2]["value"] == HISTORICAL_NA
    assert "Peak-reduction summary is not available" in " ".join(model.peaks["notices"])
    assert model.header["tested_sizes"] == "18"


def test_partial_period_uses_estimated_annual_wording(tmp_path: Path) -> None:
    folder = _copy_artifact(
        tmp_path,
        mutate=lambda summary: summary.update(
            {
                "annualized_from_partial_period": True,
                "partial_period_warning": "Partial period scaled to one year.",
            }
        ),
    )
    model = load_sweep_display(folder)
    assert model.notes
    assert "annualised" not in " ".join(model.notes).lower()
    assert model.revenue["phrase"] == "Estimated annual revenue increase"
    assert "Estimated annual revenue increase" in model.revenue["revenue_title"]
    assert "annualised" not in model.revenue["revenue_y"].lower()


def test_zero_complete_months_does_not_invent_peak_candidate(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        peaks = dict(summary["peak_summary"])
        peaks["average_monthly_peak_n_complete_months"] = 0
        peaks["average_monthly_peak_available"] = False
        peaks.pop("largest_average_monthly_peak_reduction_candidate", None)
        summary["peak_summary"] = peaks

    folder = _copy_artifact(tmp_path, mutate=mutate)
    model = load_sweep_display(folder)
    cards = highlight_cards(model.summary)
    assert cards[2]["value"] == "Not applicable"
    assert ZERO_COMPLETE_MONTH_NOTE in cards[2]["lines"]
    assert model.peaks["monthly_ok"] is False
    table = peak_display_rows(model.summary)
    assert "Average monthly peak reduction (kW)" not in table[0]


def test_negative_peak_reductions_remain_visible(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        summary["candidates"][0]["average_monthly_peak_reduction_kw"] = -1.5
        summary["candidates"][0]["annual_peak_reduction_kw"] = -2.0

    folder = _copy_artifact(tmp_path, mutate=mutate)
    model = load_sweep_display(folder)
    table = peak_display_rows(model.summary)
    assert table[0]["Average monthly peak reduction (kW)"] == -1.5
    assert table[0]["Reduction in highest 15-minute grid import (kW)"] == -2.0


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        summary["candidates"][1]["candidate_id"] = summary["candidates"][0]["candidate_id"]

    folder = _copy_artifact(tmp_path, mutate=mutate)
    try:
        load_sweep_display(folder)
    except SweepDisplayError:
        return
    raise AssertionError("duplicate ids must fail")


def test_cached_display_returns_copies() -> None:
    first = load_sweep_display(GANDA, site="Plant A", source="live")
    first.header["title"] = "mutated"
    second = load_sweep_display(GANDA, site="Plant A", source="live")
    assert second.header["title"] == "Battery-size comparison"
    demo = load_sweep_display(GANDA, site="Ganda Cars", source="demo")
    assert demo.header["source_line"] == "Stored demonstration result. Not recalculated."
    assert second.header["source_line"] == "Completed simulation result."
    assert second.header["site"] == "Plant A"


def test_charts_axes_markers_and_no_estimated_value() -> None:
    model = load_sweep_display(GANDA)
    payback = build_payback_chart(model.summary)
    revenue = build_revenue_chart(model.summary)
    monthly = build_monthly_peak_chart(model.summary)
    interval = build_interval_peak_chart(model.summary)
    cycles = build_cycles_chart(model.summary)
    assert payback.layout.xaxis.title.text == "Battery power (kW)"
    assert payback.layout.yaxis.title.text == "Simple payback period (years)"
    assert payback.layout.yaxis.fixedrange is True
    assert payback.layout.dragmode == "zoom"
    assert revenue.layout.yaxis.title.text == "Annual revenue increase (EUR/year)"
    assert "2 h" in series_names(payback)
    assert "4 h" in series_names(payback)
    assert "Shortest simple payback" in series_names(payback)
    assert "Revenue-capture" in series_names(revenue)
    assert monthly is not None
    assert monthly.layout.yaxis.title.text == "Average monthly peak reduction (kW)"
    assert interval is not None
    assert interval.layout.yaxis.title.text == "Highest 15-minute grid import (kW)"
    assert "annual demand" not in str(interval.layout.to_plotly_json()).lower()
    assert cycles.layout.yaxis.title.text == "Equivalent full cycles"
    colours = duration_colour_map(model.summary)
    assert colours["2 h"] == t.CHART_SELF_CONSUMPTION
    assert colours["4 h"] == t.CHART_PEAK_REDUCTION
    for fig in (payback, revenue, monthly, interval, cycles):
        assert not figure_contains_estimated_value(fig)
        blob = " ".join(hovertemplates(fig)).lower()
        assert "estimated value" not in blob
        assert "estimated_value" not in blob


def test_payback_omits_non_applicable_without_zero(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        summary["candidates"][0]["simple_payback_years"] = None

    folder = _copy_artifact(tmp_path, mutate=mutate)
    model = load_sweep_display(folder)
    payback = build_payback_chart(model.summary)
    values = y_values(payback, "2 h")
    assert 0 not in values
    assert all(value is None or float(value) > 0 for value in values)


def test_cycles_come_from_stored_fields() -> None:
    model = load_sweep_display(GANDA)
    cycles = build_cycles_chart(model.summary)
    stored = float(model.candidates[0]["equivalent_full_cycles"])
    two_h = y_values(cycles, "2 h")
    assert two_h
    assert abs(float(two_h[0]) - stored) < 1e-9
    shapes = cycles.layout.shapes or ()
    assert shapes


def test_ganda_sweep_shows_unavailable_solver_provenance() -> None:
    model = load_sweep_display(GANDA)
    provenance = model.sizes["solver_provenance"]
    assert provenance["line"] is None
    assert provenance["unavailable_note"] == SWEEP_SOLVER_PROVENANCE_UNAVAILABLE
    rows = model.sizes["solver_rows"]
    assert rows
    assert "Solver" not in rows[0]
    assert rows[0]["Solver status"] is not None
    assert rows[0]["Solver runtime (s)"] is not None


def test_highs_sweep_summary_provenance_line(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        summary["solver"] = {
            "name": "HiGHS",
            "highs_version": "1.15.1",
            "highspy_version": "1.8.0",
            "production_backend": True,
        }

    folder = _copy_artifact(tmp_path, mutate=mutate)
    model = load_sweep_display(folder)
    assert model.sizes["solver_provenance"]["line"] == "Solver: HiGHS 1.15.1"
    assert model.sizes["solver_provenance"]["unavailable_note"] is None


def test_highs_sweep_candidate_solver_column(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        summary["solver"] = {"name": "HiGHS", "highs_version": "1.15.1"}
        summary["candidates"][0]["solver_name"] = "HiGHS"
        summary["candidates"][1]["solver_name"] = "HiGHS"

    folder = _copy_artifact(tmp_path, mutate=mutate)
    rows = solver_rows(json.loads((folder / "sweep_summary.json").read_text(encoding="utf-8")))
    assert rows[0]["Solver"] == "HiGHS"
    assert rows[1]["Solver"] == "HiGHS"
    assert "Version" not in rows[0]


def test_sweep_solver_provenance_without_version_shows_name_only(tmp_path: Path) -> None:
    def mutate(summary: dict) -> None:
        summary["solver"] = {"name": "HiGHS", "production_backend": True}

    folder = _copy_artifact(tmp_path, mutate=mutate)
    assert sweep_solver_provenance(json.loads((folder / "sweep_summary.json").read_text())) == {
        "line": "Solver: HiGHS",
        "unavailable_note": None,
    }
