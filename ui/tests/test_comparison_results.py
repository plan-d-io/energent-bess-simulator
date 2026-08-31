from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui.flow import SESSION_KEY
from ui.services.compare_format import OVERVIEW_GROUPS, TAB_NAMES
from ui.services.paths import KIND_COMPARISON, KIND_SWEEP
from ui.services.results import SOURCE_DEMO, SOURCE_LIVE, result_record
from ui.services.saved_example import compare_artifact_dir
from ui.tests.test_app import APP, _button, _expander_labels, _labels, _text
from ui.tests.test_review import freeze_one, freeze_size, ready_review_state


def _comparison_state(*, demo: bool = False, folder: Path | None = None, site: str = "Ganda Cars"):
    state = freeze_one(ready_review_state(demo=demo))
    state["step"] = 6
    state["max_step"] = 6
    artifact = folder or compare_artifact_dir()
    state["results"] = result_record(
        kind=KIND_COMPARISON,
        folder=artifact,
        source=SOURCE_DEMO if demo else SOURCE_LIVE,
        site=site,
        period_id="2024",
        period_label="Complete calendar year 2024",
    )
    return state


def test_demo_one_battery_opens_full_comparison_results() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = freeze_one(ready_review_state(demo=True))
    at.run()
    assert not at.exception
    _button(at, "View saved demonstration results").click()
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results ready" not in page
    assert "Ganda Cars: results" in page
    assert "Stored demonstration result. Not recalculated." in page
    assert [item.label for item in at.tabs] == list(TAB_NAMES)
    for heading in OVERVIEW_GROUPS:
        assert heading in page
    assert "Site totals" in page
    assert "Comparison case" in page
    assert "Dispatch strategies" in page
    assert "Comparison cases" not in page
    assert "quarter-hours. Battery" not in page
    assert "Overview" in [item.value for item in at.subheader]
    assert "Dispatch strategy" in [box.label for box in at.selectbox]
    assert at.session_state[SESSION_KEY]["results"]["source"] == SOURCE_DEMO
    assert "job" not in at.session_state[SESSION_KEY]
    results = at.session_state[SESSION_KEY]["results"]
    assert "monthly" not in results
    assert results.get("validated") is True
    assert "Cancel" not in _labels(at)
    assert "Download audit ZIP" in [item.label for item in at.download_button]
    assert "Return to Configure options" in _labels(at)
    expanders = _expander_labels(at)
    assert "Battery operation" in expanders
    assert "Solver" in expanders
    assert "Revenue composition detail" in expanders


def test_live_comparison_uses_same_composition() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = _comparison_state(demo=False, site="Plant A")
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Plant A: results" in page
    assert "Completed simulation result." in page
    assert "Results ready" not in page
    assert [item.label for item in at.tabs] == list(TAB_NAMES)
    assert "Euro values are Energent PV revenue" in page
    assert "quarter-hours. Battery" not in page
    assert "Overview" in [item.value for item in at.subheader]
    options = [option for box in at.selectbox for option in box.options]
    assert "self_consumption" not in options
    assert "dynamic_injection" not in options
    assert "Self-consumption" in options
    assert "Dynamic injection tariff" in options
    results = at.session_state[SESSION_KEY]["results"]
    assert set(results) <= {
        "version",
        "kind",
        "result_dir",
        "source",
        "demo",
        "job_id",
        "validated",
        "site",
        "period_id",
        "period_label",
    }


def test_invalid_sweep_uses_incomplete_artifact_recovery() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    state = freeze_size(ready_review_state())
    state["step"] = 6
    state["max_step"] = 6
    state["results"] = result_record(
        kind=KIND_SWEEP,
        folder=Path("."),
        source=SOURCE_LIVE,
        site="Plant A",
        period_id="2024",
        period_label="Calendar year 2024",
    )
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results could not be displayed" in page
    assert "The stored result files could not be read." in page
    assert "Battery-size comparison" not in page
    assert "PV and grid energy" not in page
    assert "Return to Review" in _labels(at)


def test_invalid_display_files_show_contained_recovery(tmp_path: Path) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _comparison_state(folder=folder, site="Plant A")
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results could not be displayed" in page
    assert "The stored result files could not be read." in page
    assert "Site totals" not in page
    assert "Return to Review" in _labels(at)
    diag = [item for item in at.expander if "Diagnostics" in str(getattr(item, "label", "") or "")]
    assert diag and diag[0].proto.expanded is False
    assert folder.exists()


def test_return_to_configure_keeps_uploads_and_results() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = _comparison_state(demo=True)
    at.run()
    assert not at.exception
    _button(at, "Return to Configure options").click()
    at.run()
    assert not at.exception
    state = at.session_state[SESSION_KEY]
    assert state["step"] == 4
    assert "upload_payloads" in state
    assert "results" in state
    assert "job" not in state
    assert "Configure options" in [item.value for item in at.header]


def test_data_explorer_offers_seasonal_and_iso_weeks() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = _comparison_state(demo=True)
    at.run()
    assert not at.exception
    radios = [item for item in at.radio if item.label == "Time window"]
    assert radios
    assert list(radios[0].options) == ["Seasonal week", "Choose a week"]
    boxes = {item.label: item for item in at.selectbox}
    assert "Seasonal week" in boxes
    assert "Local week" not in boxes
    radios[0].set_value("Choose a week")
    at.run()
    assert not at.exception
    boxes = {item.label: item for item in at.selectbox}
    assert "Local week" in boxes
    assert "Seasonal week" not in boxes
    from ui.services.compare_display import load_comparison_display
    from ui.services.compare_explorer import iso_weeks_wholly_inside

    model = load_comparison_display(compare_artifact_dir())
    weeks = iso_weeks_wholly_inside(
        model.explorer["period_start_local"],
        model.explorer["period_end_local"],
    )
    expected = next(
        index for index, item in enumerate(weeks) if item.iso_year == 2024 and item.iso_week == 3
    )
    assert boxes["Local week"].value == expected
    assert "ISO week 03" in weeks[expected].label
