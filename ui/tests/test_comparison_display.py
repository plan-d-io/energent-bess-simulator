from __future__ import annotations

import json
import shutil
from pathlib import Path

from ui.services.compare_display import (
    ComparisonDisplayError,
    display_cache_key,
    highlight_rows,
    load_comparison_display,
    load_comparison_files,
    overview_peaks_rows,
    overview_revenue_rows,
    partial_period_warning,
    solver_rows,
    visible_cases,
)
from ui.services.compare_format import (
    EM_DASH,
    HIGHLIGHT_COLUMNS,
    HISTORICAL_DYNAMIC_NOTE,
    HISTORICAL_NA,
    NO_PAYBACK,
    OVERVIEW_GROUPS,
    TAB_NAMES,
    fmt_eur,
    fmt_kw,
    fmt_mwh,
    fmt_years,
)
from ui.services.saved_example import compare_artifact_dir

GANDA = compare_artifact_dir()


def _copy_required(tmp_path: Path, *, summary: dict | None = None) -> Path:
    folder = tmp_path / "compare"
    folder.mkdir(parents=True)
    for name in (
        "comparison_summary.json",
        "monthly_summary.csv",
        "monthly_peaks.csv",
        "run_metadata.json",
    ):
        shutil.copy(GANDA / name, folder / name)
    if summary is not None:
        (folder / "comparison_summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
    return folder


def test_ganda_comparison_loads_read_only() -> None:
    model = load_comparison_display(GANDA, site="Ganda Cars", source="demo")
    assert [key for key, _label in model.cases] == [
        "no_battery",
        "reference",
        "self_consumption",
        "peak_reduction",
        "revenue",
        "dynamic_injection",
    ]
    assert [label for _key, label in model.cases] == [
        "No battery",
        "Rule-based control",
        "Self-consumption",
        "Peak reduction",
        "Revenue maximisation",
        "Dynamic injection tariff",
    ]
    assert model.header["source_line"] == "Stored demonstration result. Not recalculated."
    assert model.header["title"] == "Ganda Cars: results"
    assert model.header["case_count"] == "6"
    assert [name for name, _rows in model.overview["groups"]] == list(OVERVIEW_GROUPS)
    assert model.overview["highlight_columns"] == HIGHLIGHT_COLUMNS
    assert "intro" not in model.overview


def test_cache_identity_changes_when_a_required_file_changes(tmp_path: Path) -> None:
    folder = _copy_required(tmp_path)
    first = display_cache_key(folder)
    payload = (folder / "monthly_summary.csv").read_text(encoding="utf-8")
    (folder / "monthly_summary.csv").write_text(payload + "\n", encoding="utf-8")
    assert display_cache_key(folder) != first


def test_malformed_json_and_missing_columns_fail(tmp_path: Path) -> None:
    folder = _copy_required(tmp_path)
    (folder / "comparison_summary.json").write_text("{", encoding="utf-8")
    try:
        load_comparison_display(folder)
        raise AssertionError("expected ComparisonDisplayError")
    except ComparisonDisplayError:
        pass
    folder = _copy_required(tmp_path / "cols")
    csv = (folder / "monthly_summary.csv").read_text(encoding="utf-8").splitlines()
    (folder / "monthly_summary.csv").write_text("month,scenario\n2024-01,no_battery\n", encoding="utf-8")
    try:
        load_comparison_display(folder)
        raise AssertionError("expected ComparisonDisplayError")
    except ComparisonDisplayError:
        pass
    del csv


def test_wrong_result_kind_fails(tmp_path: Path) -> None:
    folder = tmp_path / "sweep"
    folder.mkdir()
    (folder / "sweep_summary.json").write_text("{}", encoding="utf-8")
    try:
        load_comparison_display(folder)
        raise AssertionError("expected ComparisonDisplayError")
    except ComparisonDisplayError:
        pass


def test_highlight_table_uses_stored_ganda_values() -> None:
    model = load_comparison_display(GANDA)
    rows = {row["Comparison case"]: row for row in model.overview["highlight_rows"]}
    baseline = rows["No battery"]
    assert baseline["Additional useful PV (MWh)"] == EM_DASH
    assert baseline["Average monthly peak reduction (kW)"] == EM_DASH
    assert baseline["Revenue increase (EUR)"] == EM_DASH
    assert baseline["Simple payback period"] == EM_DASH
    case = model.summary["scenarios"]["self_consumption"]
    self_row = rows["Self-consumption"]
    assert self_row["Additional useful PV (MWh)"] == fmt_mwh(
        case["additional_useful_pv_kwh"], unit=False
    )
    assert self_row["Average monthly peak reduction (kW)"] == fmt_kw(
        case["average_monthly_peak_reduction_kw"], unit=False
    )
    assert self_row["Revenue increase (EUR)"] == fmt_eur(case["revenue"]["revenue_change_eur"])
    assert self_row["Simple payback period"] == fmt_years(case["simple_payback_years"])
    assert rows["Dynamic injection tariff"]["Simple payback period"] == NO_PAYBACK


def test_no_payback_is_not_reconstructed_from_capex() -> None:
    model = load_comparison_display(GANDA)
    dyn = model.summary["scenarios"]["dynamic_injection"]
    capex = float(dyn["estimated_battery_capex_eur"])
    change = float(dyn["revenue"]["revenue_change_eur"])
    assert change < 0
    reconstructed = capex / abs(change)
    rows = {row["Comparison case"]: row for row in model.overview["highlight_rows"]}
    assert str(round(reconstructed, 1)) not in rows["Dynamic injection tariff"]["Simple payback period"]
    assert rows["Dynamic injection tariff"]["Simple payback period"] == NO_PAYBACK


def test_site_totals_and_overview_groups_match_stored_values() -> None:
    model = load_comparison_display(GANDA)
    no_batt = model.summary["scenarios"]["no_battery"]
    site = {row["Metric"]: row["Value"] for row in model.overview["groups"][0][1]}
    assert site["PV production (MWh)"] == fmt_mwh(no_batt["total_pv_production_kwh"])
    assert site["Site load (MWh)"] == fmt_mwh(no_batt["site_load_kwh"])
    energy = model.overview["groups"][1][1]
    useful = next(row for row in energy if row["Metric"].startswith("Useful PV supplied"))
    assert useful["Self-consumption"] == fmt_mwh(
        model.summary["scenarios"]["self_consumption"]["useful_pv_delivered_kwh"]
    )
    revenue = overview_revenue_rows(model.summary)
    injection = next(row for row in revenue if row["Metric"] == "Grid-injection revenue (EUR)")
    dyn = model.summary["scenarios"]["dynamic_injection"]
    assert injection["Dynamic injection tariff"] == fmt_eur(
        dyn["revenue"]["dynamic_grid_injection_revenue_eur"]
    )
    peaks = overview_peaks_rows(model.summary)
    assert peaks[0]["Metric"] == "Average monthly peak (kW)"
    assert "Highest 15-minute" in peaks[3]["Metric"]
    assert "annual" not in peaks[3]["Metric"].lower()


def test_display_does_not_mutate_source_mappings() -> None:
    summary, monthly, _peaks, _meta, _val = load_comparison_files(GANDA)
    original_pv = summary["scenarios"]["no_battery"]["additional_useful_pv_kwh"]
    original_month = str(monthly.iloc[0]["month"])
    model = load_comparison_display(GANDA)
    model.summary["scenarios"]["no_battery"]["additional_useful_pv_kwh"] = 999999
    model.monthly.iloc[0, 0] = "changed"
    again = load_comparison_display(GANDA)
    assert again.summary["scenarios"]["no_battery"]["additional_useful_pv_kwh"] == original_pv
    assert str(again.monthly.iloc[0]["month"]) == original_month
    file_summary = json.loads((GANDA / "comparison_summary.json").read_text(encoding="utf-8"))
    assert file_summary["scenarios"]["no_battery"]["additional_useful_pv_kwh"] == original_pv


def test_historical_missing_payback_and_cycles(tmp_path: Path) -> None:
    summary = json.loads((GANDA / "comparison_summary.json").read_text(encoding="utf-8"))
    summary.pop("economics", None)
    order = [key for key in summary["scenario_order"] if key != "dynamic_injection"]
    summary["scenario_order"] = order
    summary["scenarios"].pop("dynamic_injection", None)
    for key, case in summary["scenarios"].items():
        case.pop("simple_payback_years", None)
        case.pop("payback_applicable", None)
        case.pop("equivalent_full_cycles", None)
        case.pop("allowed_equivalent_full_cycles", None)
        case.pop("cycle_limit_binding", None)
        case.pop("estimated_battery_capex_eur", None)
    folder = _copy_required(tmp_path, summary=summary)
    model = load_comparison_display(folder, site="Historical", source="demo")
    assert HISTORICAL_DYNAMIC_NOTE in model.notes
    assert "Dynamic injection tariff" not in [label for _key, label in model.cases]
    rows = {row["Comparison case"]: row for row in model.overview["highlight_rows"]}
    assert rows["Self-consumption"]["Simple payback period"] == HISTORICAL_NA
    battery = model.overview["groups"][4][1]
    cycles = next(row for row in battery if row["Metric"] == "Equivalent full cycles")
    assert cycles["Self-consumption"] == HISTORICAL_NA
    constrained = next(row for row in battery if row["Metric"].startswith("Cycle limit"))
    assert constrained["Self-consumption"] == HISTORICAL_NA
    assert model.revenue["historical_cost"] == HISTORICAL_NA


def test_partial_period_warning_uses_stored_text(tmp_path: Path) -> None:
    summary = json.loads((GANDA / "comparison_summary.json").read_text(encoding="utf-8"))
    summary["economics"]["annualised_from_partial_period"] = True
    summary["economics"]["partial_period_warning"] = "Stored partial-period warning."
    folder = _copy_required(tmp_path, summary=summary)
    model = load_comparison_display(folder)
    assert partial_period_warning(model.summary) == "Stored partial-period warning."
    assert model.overview["partial_warning"] == "Stored partial-period warning."


def test_monthly_models_preserve_month_order_and_axes() -> None:
    from ui.services.compare_display import energy_monthly_models, peaks_chart_model

    model = load_comparison_display(GANDA)
    stored = list(model.monthly.loc[model.monthly["scenario"] == "self_consumption", "month"].astype(str))
    table, charts = energy_monthly_models(
        model.monthly, scenario="self_consumption", label="Self-consumption"
    )
    assert [row["Month"] for row in table] == stored
    for spec in charts:
        assert spec.x_title == "Month"
        assert spec.y_title == "Energy (MWh)"
        assert spec.series_order
    peak_chart = peaks_chart_model(model.summary, model.monthly_peaks)
    assert peak_chart.x_title == "Month"
    assert peak_chart.y_title == "Monthly peak (kW)"
    assert [label for _key, label in visible_cases(model.summary)] == list(peak_chart.series_order)
    colours = dict(peak_chart.colours)
    assert colours["No battery"] != colours["Self-consumption"]
    assert colours["Self-consumption"] == colours["Self-consumption"]


def test_tab_names_are_exact() -> None:
    assert TAB_NAMES == (
        "Overview",
        "PV and grid energy",
        "Grid peaks",
        "Energent revenue",
        "Data explorer",
        "Technical details",
        "Downloads",
    )


def test_highlight_rows_helper_matches_model() -> None:
    model = load_comparison_display(GANDA)
    assert highlight_rows(model.summary) == model.overview["highlight_rows"]


def test_ganda_comparison_solver_rows_show_gurobi_version() -> None:
    model = load_comparison_display(GANDA)
    rows = {row["Comparison case"]: row for row in model.technical["solvers"]}
    sc = rows["Self-consumption"]
    assert sc["Solver"] == "Gurobi"
    assert sc["Version"] == "13.0.1"
    assert sc["Status"] == "OPTIMAL"


def test_highs_comparison_solver_rows_use_stored_version(tmp_path: Path) -> None:
    summary = json.loads((GANDA / "comparison_summary.json").read_text(encoding="utf-8"))
    summary["solvers"]["self_consumption"] = {
        "name": "HiGHS",
        "highs_version": "1.15.1",
        "highspy_version": "1.8.0",
        "status": "OPTIMAL",
        "runtime_s": 0.42,
    }
    folder = _copy_required(tmp_path, summary=summary)
    rows = {row["Comparison case"]: row for row in solver_rows(json.loads((folder / "comparison_summary.json").read_text()))}
    assert rows["Self-consumption"]["Solver"] == "HiGHS"
    assert rows["Self-consumption"]["Version"] == "1.15.1"


def test_highs_comparison_prefers_highspy_when_highs_version_missing(tmp_path: Path) -> None:
    summary = json.loads((GANDA / "comparison_summary.json").read_text(encoding="utf-8"))
    summary["solvers"]["revenue"] = {
        "name": "HiGHS",
        "highspy_version": "1.8.0",
        "status": "OPTIMAL",
        "runtime_s": 0.5,
    }
    folder = _copy_required(tmp_path, summary=summary)
    rows = {row["Comparison case"]: row for row in solver_rows(json.loads((folder / "comparison_summary.json").read_text()))}
    assert rows["Revenue maximisation"]["Version"] == "1.8.0"


def test_missing_comparison_solver_version_degrades_only_version_cell(tmp_path: Path) -> None:
    summary = json.loads((GANDA / "comparison_summary.json").read_text(encoding="utf-8"))
    summary["solvers"]["peak_reduction"] = {
        "name": "HiGHS",
        "status": "OPTIMAL",
        "runtime_s": 0.3,
    }
    rows = {row["Comparison case"]: row for row in solver_rows(summary)}
    row = rows["Peak reduction"]
    assert row["Solver"] == "HiGHS"
    assert row["Version"] == HISTORICAL_NA
    assert row["Status"] == "OPTIMAL"
    assert row["Runtime (s)"] == "0.300"
