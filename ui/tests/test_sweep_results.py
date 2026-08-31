from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui.flow import SESSION_KEY
from ui.services.configure import MODE_ONE
from ui.services.paths import KIND_COMPARISON, KIND_SWEEP
from ui.services.results import SOURCE_DEMO, SOURCE_LIVE, result_record
from ui.services.saved_example import compare_artifact_dir, sweep_artifact_dir
from ui.services.sweep_format import (
    ALL_SIZES_HEADING,
    AVERAGE_MONTHLY_PEAK_DEFINITION,
    CYCLE_LIMIT_EXPLANATION,
    RANGE_BOUNDARY_CONSOLIDATED,
    TAB_NAMES,
    TOP_RESULTS_HEADING,
    TRANSFER_HEADING,
    TRANSFER_LIVE,
)
from ui.tests.test_app import APP, _button, _expander_labels, _labels, _text
from ui.tests.test_review import freeze_one, freeze_size, ready_review_state

GANDA = sweep_artifact_dir()


def _sweep_state(*, demo: bool = False, folder: Path | None = None, site: str = "Ganda Cars"):
    state = freeze_size(ready_review_state(demo=demo))
    state["step"] = 6
    state["max_step"] = 6
    artifact = folder or GANDA
    state["results"] = result_record(
        kind=KIND_SWEEP,
        folder=artifact,
        source=SOURCE_DEMO if demo else SOURCE_LIVE,
        site=site,
        period_id="2024",
        period_label="Complete calendar year 2024",
    )
    return state


def test_demo_size_opens_battery_size_results() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = freeze_size(ready_review_state(demo=True))
    at.run()
    assert not at.exception
    _button(at, "View saved demonstration results").click()
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results ready" not in page
    assert "Battery-size comparison" in page
    assert "Stored demonstration result. Not recalculated." in page
    assert "No tested battery pays back within the configured 10-year screening period." in page
    assert "10.1 years" in page
    assert [item.label for item in at.tabs] == list(TAB_NAMES)
    assert "Additional details" in [item.label for item in at.tabs]
    assert "Additional details" in [item.value for item in at.subheader]
    assert "All tested sizes" not in [item.label for item in at.tabs]
    assert "Results by duration" not in page
    assert "Pays back within screening period" not in page
    assert page.index(TOP_RESULTS_HEADING) < page.index(ALL_SIZES_HEADING)
    assert page.index(ALL_SIZES_HEADING) < page.index(TRANSFER_HEADING)
    assert RANGE_BOUNDARY_CONSOLIDATED not in page
    assert "Range note" not in page
    assert "These are physical peak reductions" not in page
    assert "Complete local calendar months used" not in page
    captions = [str(item.value) for item in at.caption]
    assert AVERAGE_MONTHLY_PEAK_DEFINITION in captions
    assert CYCLE_LIMIT_EXPLANATION in captions
    assert page.index("Average monthly peak reduction versus battery power") < page.index(
        AVERAGE_MONTHLY_PEAK_DEFINITION
    )
    assert page.index("Equivalent full cycles versus battery power") < page.index(
        CYCLE_LIMIT_EXPLANATION
    )
    candidate_frames = [
        item.value
        for item in at.dataframe
        if hasattr(item.value, "columns") and "Flags" in list(item.value.columns)
    ]
    assert len(candidate_frames) == 1
    extra_tables = []
    for collection in (at.dataframe, getattr(at, "table", [])):
        for item in collection:
            try:
                columns = list(item.value.columns)
            except Exception:
                continue
            if "Useful PV (kWh)" in columns:
                extra_tables.append(item)
    assert extra_tables
    expanders = _expander_labels(at)
    assert "What do the flags mean?" in expanders
    assert "Additional details" not in expanders
    solver = [item for item in at.expander if "Technical solver checks" in str(getattr(item, "label", "") or "")]
    assert solver and solver[0].proto.expanded is False
    assert "Solver provenance is not available in this historical result." in page
    assert "Use this size in a live full comparison" in _labels(at)
    assert "Estimated value" not in page
    assert "No battery is the suggested result" not in page
    assert "c001_5kW_10kWh" not in page
    assert "Return to Configure options" in _labels(at)
    assert at.session_state[SESSION_KEY]["results"]["source"] == SOURCE_DEMO
    assert "job" not in at.session_state[SESSION_KEY]
    results = at.session_state[SESSION_KEY]["results"]
    assert "candidates" not in results
    assert results.get("validated") is True
    assert "Download audit ZIP" in [item.label for item in at.download_button]
    assert "Download candidate table as CSV" in [item.label for item in at.download_button]


def test_live_sweep_uses_same_composition() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = _sweep_state(demo=False, site="Plant A")
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Battery-size comparison" in page
    assert "Completed simulation result." in page
    assert "Results ready" not in page
    assert "Plant A" in page
    assert [item.label for item in at.tabs] == list(TAB_NAMES)
    assert TRANSFER_LIVE in _labels(at)
    assert TRANSFER_HEADING in page
    assert TOP_RESULTS_HEADING in page
    assert "PV and grid energy" not in page


def test_comparison_still_opens_full_comparison_results() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    state = freeze_one(ready_review_state(demo=True))
    state["step"] = 6
    state["max_step"] = 6
    state["results"] = result_record(
        kind=KIND_COMPARISON,
        folder=compare_artifact_dir(),
        source=SOURCE_DEMO,
        site="Ganda Cars",
        period_id="2024",
        period_label="Complete calendar year 2024",
    )
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Ganda Cars: results" in page
    assert "Battery-size comparison" not in page
    assert "Site totals" in page


def test_unknown_kind_does_not_guess_sweep_view(tmp_path: Path) -> None:
    folder = tmp_path / "mystery"
    folder.mkdir()
    at = AppTest.from_file(str(APP), default_timeout=12)
    state = freeze_size(ready_review_state())
    state["step"] = 6
    state["max_step"] = 6
    record = result_record(
        kind="other",
        folder=folder,
        source=SOURCE_LIVE,
        site="Plant A",
        period_id="2024",
        period_label="Calendar year 2024",
    )
    state["results"] = record
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results ready" in page
    assert "Find a battery size" in page
    assert [item.label for item in at.tabs] == []
    assert "Revenue and payback" not in page


def test_live_transfer_lands_on_one_battery_configure(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("launch must not run on transfer")

    monkeypatch.setattr("ui.services.job.launch_live_job", _boom)
    at = AppTest.from_file(str(APP), default_timeout=30)
    state = _sweep_state(demo=False, site="Plant A")
    payloads = state["upload_payloads"]
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    _button(at, TRANSFER_LIVE).click()
    at.run()
    assert not at.exception
    current = at.session_state[SESSION_KEY]
    assert current["step"] == 4
    assert current["analysis_mode"] == MODE_ONE
    one = current["configure"]["one_battery"]
    assert one["usable_kwh"] == 10.0
    assert one["power_kw"] == one["charge_kw"] == one["discharge_kw"] == 5.0
    assert one["split_power"] is False
    assert current["site_name"] == "Plant A"
    assert current["upload_payloads"] == payloads
    assert current["period_id"] == "2024"
    assert current["unvalidated_ack"] is True
    assert "results" not in current
    assert "job" not in current
    assert "review" not in current
    page = _text(at)
    assert "Configure options" in page or "Step 4 of 6" in page


def test_sweep_header_uses_shared_metric_group_key() -> None:
    source = (Path(__file__).resolve().parents[1] / "views" / "sweep_results.py").read_text(
        encoding="utf-8"
    )
    assert 'key="v2-metrics-sweep-header"' in source
    assert "v2-sweep-metrics-header" not in source
    assert 'key="v2-sweep-highlights"' in source
