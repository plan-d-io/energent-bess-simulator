from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui.flow import ROUTE_LIVE, ROUTE_SAVED, SESSION_KEY, default_state
from ui.services.check_files import DST_PENDING
from ui.services.saved_example import SavedExample
from ui.presentation.components import action_row_alignment
from ui.presentation.shell import stage_button_label, step_label
from ui.views.provide_data import live_view_kind, step1_disabled_reason

APP = Path(__file__).resolve().parents[1] / "app.py"
_LEAD = "Upload the Fluvius offtake, injection and PV production CSV files"
_DEMO_REMINDER = "Saved 2024 validation and results. No simulation runs."
_REMOVED_PRICE_LINE = (
    "Wholesale day-ahead prices for grid injection only. Ordinary users do not upload this file."
)
_STEP2_LEAD = (
    "Reviews the detected meter roles, common coverage and available simulation periods."
)
_PERIOD_LEAD = "Detected time periods that can be used for a simulation"
_LEAD_STEP3 = "Select a calendar period for the simulation. A complete calendar year is recommended."
_REASON_PREFIX = "To continue: "

_SAVED = SavedExample(
    ok=True,
    site_name="Ganda Cars",
    rows=(
        {
            "Role": "Offtake",
            "File": "Historiek_afname.csv",
            "Detected register": "Afname Actief",
            "Unit": "kWh",
        },
        {
            "Role": "Injection",
            "File": "Historiek_injectie.csv",
            "Detected register": "Injectie Actief",
            "Unit": "kWh",
        },
        {
            "Role": "PV production",
            "File": "Historiek_submeting.csv",
            "Detected register": "Productie Actief",
            "Unit": "kWh",
        },
    ),
    error=None,
)


def _button(at: AppTest, label: str):
    matches = [item for item in at.button if item.label == label]
    assert matches, f"missing button {label!r}"
    return matches[0]


def _checkbox(at: AppTest, label: str):
    matches = [item for item in at.checkbox if item.label == label]
    assert matches, f"missing checkbox {label!r}"
    return matches[0]


def _text(at: AppTest) -> str:
    headers = [str(item.value) for item in at.header]
    subheaders = [str(item.value) for item in getattr(at, "subheader", [])]
    tabs = [str(getattr(item, "label", "") or "") for item in getattr(at, "tabs", [])]
    markdown = " ".join(str(item.value) for item in at.markdown)
    captions = " ".join(str(item.value) for item in at.caption)
    info = " ".join(str(item.value) for item in at.info)
    success = " ".join(str(item.value) for item in getattr(at, "success", []))
    warning = " ".join(str(item.value) for item in at.warning)
    error = " ".join(str(item.value) for item in at.error)
    html = " ".join(str(getattr(item, "value", item)) for item in getattr(at, "html", []))
    metrics = []
    for item in getattr(at, "metric", []):
        metrics.append(f"{getattr(item, 'label', '')} {getattr(item, 'value', '')}")
    frames = []
    for collection in (at.dataframe, getattr(at, "table", [])):
        for item in collection:
            try:
                frames.append(item.value.to_string())
            except Exception:
                frames.append(str(item.value))
    return " ".join(
        headers + subheaders + tabs + [markdown, captions, info, success, warning, error, html] + metrics + frames
    )


def _labels(at: AppTest) -> list[str]:
    return [item.label for item in at.button]


def _expander_labels(at: AppTest) -> list[str]:
    labels: list[str] = []
    for item in at.expander:
        labels.append(str(getattr(item, "label", "") or getattr(item, "value", "")))
    return labels


def _stage(at: AppTest, number: int, name: str):
    return _button(at, stage_button_label(number, name))


def test_importing_app_does_not_render() -> None:
    import importlib

    module = importlib.import_module("ui.app")
    importlib.reload(module)
    assert callable(module.main)


def test_app_opens_on_upload_data_not_gallery() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    assert not at.exception
    headers = [item.value for item in at.header]
    combined = _text(at)
    assert "Upload data" in headers
    assert "Provide data" not in combined
    assert "Check the files" not in combined
    assert _LEAD in combined
    assert _REMOVED_PRICE_LINE not in combined
    assert "V2 foundation preview" not in combined
    assert "Development preview" not in combined


def test_step1_lead_and_demo_checkbox_default() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    assert not at.exception
    assert _LEAD in _text(at)
    demo = _checkbox(at, "Demo mode")
    assert demo.value is False
    assert [item.label for item in at.checkbox] == ["Demo mode"]
    labels = _labels(at)
    assert "Live analysis" not in labels
    assert "Saved Ganda Cars example" not in labels
    assert "Back" not in labels
    site = at.text_input[0]
    assert site.label == "Site or project name"
    assert site.proto.disabled is False
    uploader = at.file_uploader[0]
    assert uploader.label == "Fluvius CSV exports"
    assert uploader.proto.disabled is False
    continue_btn = _button(at, "Continue")
    assert continue_btn.proto.disabled
    assert action_row_alignment(has_back=False) == "right"


def test_empty_live_mode_shows_site_name_reason() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    assert not at.exception
    assert _button(at, "Continue").proto.disabled
    combined = _text(at)
    assert f"{_REASON_PREFIX}Enter a site or project name." in combined
    assert _REMOVED_PRICE_LINE not in combined
    assert _expander_labels(at).count("Day-ahead injection prices") == 1
    assert "Select the three Fluvius CSV exports to continue." not in combined
    assert combined.count("To continue:") == 1


def test_demo_mode_shows_readonly_site_disabled_uploader_and_table(monkeypatch) -> None:
    monkeypatch.setattr(
        "ui.views.provide_data.load_saved_example",
        lambda **_kwargs: _SAVED,
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    _checkbox(at, "Demo mode").check()
    at.run()
    assert not at.exception
    assert at.session_state[SESSION_KEY]["data_route"] == ROUTE_SAVED
    combined = _text(at)
    assert "Saved example" not in combined
    assert _DEMO_REMINDER in combined
    assert len(at.info) == 0
    assert "Afname Actief" in combined
    site = at.text_input[0]
    assert site.value == "Demo site"
    assert site.proto.disabled is True
    assert at.file_uploader[0].proto.disabled is True
    assert _button(at, "Continue").proto.disabled is False
    assert "Back" not in _labels(at)
    assert "To continue:" not in _text(at)
    assert _expander_labels(at).count("Day-ahead injection prices") == 1
    assert _REMOVED_PRICE_LINE not in combined
    assert "Standard project dataset · da_prices_qh.parquet" in combined


def test_demo_mode_does_not_call_live_ingestion(monkeypatch) -> None:
    called = {"n": 0}

    def _forbidden(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("live ingestion must not run in Demo mode")

    monkeypatch.setattr(
        "ui.views.provide_data.load_saved_example",
        lambda **_kwargs: _SAVED,
    )
    monkeypatch.setattr(
        "ui.views.provide_data.inspect_fluvius_payloads",
        _forbidden,
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    _checkbox(at, "Demo mode").check()
    at.run()
    assert not at.exception
    assert called["n"] == 0


def test_clearing_demo_mode_returns_to_live(monkeypatch) -> None:
    monkeypatch.setattr(
        "ui.views.provide_data.load_saved_example",
        lambda **_kwargs: _SAVED,
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    _checkbox(at, "Demo mode").check()
    at.run()
    _checkbox(at, "Demo mode").uncheck()
    at.run()
    assert not at.exception
    assert at.session_state[SESSION_KEY]["data_route"] == ROUTE_LIVE
    assert at.text_input[0].proto.disabled is False
    assert at.file_uploader[0].proto.disabled is False
    assert _button(at, "Continue").proto.disabled
    assert _DEMO_REMINDER not in _text(at)


def test_demo_continue_reaches_check_files_then_step3(monkeypatch) -> None:
    monkeypatch.setattr(
        "ui.views.provide_data.load_saved_example",
        lambda **_kwargs: _SAVED,
    )

    def _live_forbidden(*_args, **_kwargs):
        raise AssertionError("Demo mode must not run live inspection or price coverage")

    monkeypatch.setattr(
        "ui.views.choose_period.inspect_period_payloads",
        _live_forbidden,
    )
    monkeypatch.setattr(
        "ui.views.choose_period.price_coverage_for_payloads",
        _live_forbidden,
    )
    monkeypatch.setattr(
        "ui.views.configure.load_defaults_snapshot",
        _live_forbidden,
    )
    monkeypatch.setattr(
        "ui.views.configure.resolve_live_candidates",
        _live_forbidden,
    )
    monkeypatch.setattr(
        "ui.services.candidates.inspect_period_payloads",
        _live_forbidden,
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    _checkbox(at, "Demo mode").check()
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    headers = [item.value for item in at.header]
    combined = _text(at)
    assert "Data verification" in headers
    assert "Check the files" not in combined
    assert "Files usable" in combined
    assert "Afname Actief" in combined
    assert "Calendar year 2024" in combined
    assert "Checked after period selection" not in combined
    assert "Day-ahead prices" not in combined
    assert _STEP2_LEAD in combined
    assert _PERIOD_LEAD in combined
    assert "Local timestamps were converted to UTC." in combined
    assert "Belgian timestamps" not in combined
    assert "Timestamps converted" in combined
    assert DST_PENDING in combined
    assert "DST details" not in _expander_labels(at)
    assert "Quarter-hours in local day" not in combined
    assert "n_spring_skipped_wall_clock" not in combined
    assert "Provide data" not in combined
    assert "Day-ahead price dataset available" in combined
    assert [item.label for item in at.checkbox] == []
    back = _button(at, "Back")
    continue_btn = _button(at, "Continue")
    assert back.proto.type == "secondary"
    assert continue_btn.proto.disabled is False
    assert continue_btn.proto.type == "primary"
    assert action_row_alignment(has_back=True) == "distribute"
    continue_btn.click()
    at.run()
    assert not at.exception
    step3 = _text(at)
    assert "Simulation period" in [item.value for item in at.header]
    assert "Simulation period is not implemented yet." not in step3
    assert _LEAD_STEP3 in step3
    assert "Simulation period" in [item.label for item in at.selectbox]
    assert at.selectbox[0].value == "2024"
    assert "Complete calendar year" in step3
    assert "35,136" in step3
    assert "96" in step3
    assert "2024-10-02" in step3
    assert "Data contains 96 unvalidated quarter-hours" in step3
    assert "Affected local date: 2024-10-02" in step3
    assert "Only non-empty readings are used." in step3
    assert "Non-empty Ongevalideerd" not in step3
    assert "Meter-boundary mismatch" not in step3
    assert "Period checks passed" in step3
    assert "Period details" in _expander_labels(at)
    assert "DST details" not in _expander_labels(at)
    assert "2024-03-31" in step3
    assert "2024-10-27" in step3
    assert "Day-ahead prices cover this period" in step3
    assert "35,136 quarter-hours matched exactly." in step3
    unvalidated = [item for item in at.checkbox if "unvalidated" in item.label.lower()]
    assert unvalidated
    assert unvalidated[0].value is True
    assert unvalidated[0].proto.disabled
    assert _button(at, "Continue").proto.disabled is False
    assert "To continue:" not in step3
    flow = at.session_state[SESSION_KEY]
    assert flow["period_id"] == "2024"
    assert flow["unvalidated_ack"] is True
    assert flow["site_boundary_ack"] is False
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    configure_page = _text(at)
    assert "Configure options" in [item.value for item in at.header]
    assert "Configuration is not implemented yet." not in configure_page
    assert "Evaluate one battery" in _labels(at)
    assert "Find a battery size" in _labels(at)
    assert "Choose and configure the simulation run" in configure_page
    assert "Simulate one battery size using all dispatch strategies." in configure_page
    assert "Compare a range of sizes using the revenue maximisation dispatch strategy." in configure_page
    assert "Evaluate one battery" not in configure_page
    assert "Find a battery size" not in configure_page
    assert "Configure —" not in " ".join(_labels(at))
    assert "Results —" not in " ".join(_labels(at))
    assert "Demo settings are read-only." in configure_page
    assert "Usable battery capacity (kWh)" in [item.label for item in at.number_input]
    assert "Saved example unavailable" not in configure_page
    assert _button(at, "Continue").proto.disabled is False
    usable = [item for item in at.number_input if item.label == "Usable battery capacity (kWh)"]
    assert usable
    assert usable[0].proto.disabled
    mode_buttons = [item for item in at.button if item.label in {"Evaluate one battery", "Find a battery size"}]
    assert mode_buttons
    assert all(not item.proto.disabled for item in mode_buttons)
    _button(at, "Find a battery size").click()
    at.run()
    assert not at.exception
    size_page = _text(at)
    assert "Choose and configure the simulation run" in size_page
    assert "Battery sizes are compared using Revenue maximisation." not in size_page
    assert "Other dispatch strategies can be evaluated afterwards for a selected size." not in size_page
    assert "Compare a range of sizes using the revenue maximisation dispatch strategy." in size_page
    assert "Find a battery size" not in size_page
    assert "Evaluate one battery" not in size_page
    assert "18 battery sizes will be tested." in size_page
    assert "c001_5kW_10kWh" in size_page + " " + " ".join(_expander_labels(at))
    assert "Restore recommended defaults" not in _labels(at)
    _button(at, "Evaluate one battery").click()
    at.run()
    assert not at.exception
    one_again = _text(at)
    assert "Find a battery size" not in one_again
    assert "Evaluate one battery" not in one_again
    _stage(at, 3, "Simulation period").click()
    at.run()
    assert not at.exception
    earlier = _text(at)
    assert "Find a battery size" not in earlier
    assert "Evaluate one battery" not in earlier
    _stage(at, 4, step_label(4, "one-battery")).click()
    at.run()
    assert not at.exception
    assert at.session_state[SESSION_KEY]["max_step"] == 4
    assert at.session_state[SESSION_KEY]["period_id"] == "2024"
    review = _stage(at, 5, "Review and run")
    results = _stage(at, 6, step_label(6, "one-battery"))
    assert review.proto.disabled
    assert results.proto.disabled
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    review_page = _text(at)
    assert "Review and run" in [item.value for item in at.header]
    assert "Confirm configuration before running the simulation." in review_page
    assert "The stored result will be opened and no simulation will run." in review_page
    assert "Review is not implemented yet." not in review_page
    assert "Incomplete V2 development state" not in review_page
    assert "No battery" in review_page
    assert "Rule-based control" in review_page
    assert "Self-consumption" in review_page
    assert "Peak reduction" in review_page
    assert "Revenue maximisation" in review_page
    assert "Dynamic injection tariff" in review_page
    assert "Baseline for comparison, constructed from measured consumption, PV production and grid exchange." in review_page
    assert "Rule-based EMS approximation without foresight." in review_page
    assert "never charges from the grid." in review_page
    assert "Single battery, multiple dispatch strategies" in review_page
    assert "Dispatch strategies" in review_page
    assert "Saved example" not in review_page
    assert "Diagnostics" in _expander_labels(at)
    diag = [item for item in at.expander if "Diagnostics" in str(getattr(item, "label", "") or "")]
    assert diag
    assert diag[0].proto.expanded is False
    assert _button(at, "View saved demonstration results").proto.disabled is False
    assert "Show detailed solver output in the run log" not in [item.label for item in at.checkbox]
    assert "I understand that results for this partial period will be annualised for the sizing estimate." not in [
        item.label for item in at.checkbox
    ]
    assert "Execution will be connected in the next phase." not in review_page
    assert "To continue:" not in review_page
    assert "Continue" not in _labels(at)
    assert at.session_state[SESSION_KEY]["max_step"] == 5
    snapshot = at.session_state[SESSION_KEY]["configure"]["snapshot"]
    assert snapshot["analysis_mode"] == "one-battery"
    assert snapshot["one_battery"]["usable_kwh"] == 100.0
    assert snapshot["one_battery"]["charge_kw"] == 50.0
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert "Configure options" in [item.value for item in at.header]
    assert at.session_state[SESSION_KEY]["configure"]["one_battery"]["usable_kwh"] == 100.0
    assert at.session_state[SESSION_KEY]["configure"]["snapshot"]["one_battery"]["usable_kwh"] == 100.0
    _stage(at, 1, "Upload data").click()
    at.run()
    assert "Upload data" in [item.value for item in at.header]
    assert at.session_state[SESSION_KEY]["period_id"] == "2024"
    _stage(at, 4, step_label(4, "one-battery")).click()
    at.run()
    assert "Configure options" in [item.value for item in at.header]
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert "Simulation period" in [item.value for item in at.header]
    assert at.session_state[SESSION_KEY]["period_id"] == "2024"
    assert at.session_state[SESSION_KEY]["unvalidated_ack"] is True
    _stage(at, 2, "Data verification").click()
    at.run()
    assert not at.exception
    assert "Data verification" in [item.value for item in at.header]
    assert at.session_state[SESSION_KEY]["max_step"] == 5
    assert at.session_state[SESSION_KEY]["ingest_snapshot"] is not None
    _stage(at, 3, "Simulation period").click()
    at.run()
    assert "Simulation period" in [item.value for item in at.header]
    enabled = [item.label for item in at.button if not item.proto.disabled]
    assert stage_button_label(3, "Simulation period") not in enabled
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert "Data verification" in [item.value for item in at.header]
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert "Upload data" in [item.value for item in at.header]
    assert "Back" not in _labels(at)


def test_incompatible_state_version_resets_to_step1() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    stale = default_state()
    stale["version"] = 0
    stale["step"] = 2
    at.session_state[SESSION_KEY] = stale
    at.run()
    assert not at.exception
    assert "Upload data" in [item.value for item in at.header]


def test_step1_reason_priority() -> None:
    assert (
        step1_disabled_reason(
            demo=False,
            site_name="",
            file_count=3,
            kind="ready",
            demo_ok=True,
        )
        == "Enter a site or project name."
    )
    assert (
        step1_disabled_reason(
            demo=False,
            site_name="Plant",
            file_count=0,
            kind="empty",
            demo_ok=False,
        )
        == "Upload the three Fluvius CSV files."
    )
    assert (
        step1_disabled_reason(
            demo=False,
            site_name="Plant",
            file_count=2,
            kind="wrong_count",
            demo_ok=False,
        )
        == "Upload exactly three Fluvius CSV files."
    )
    assert (
        step1_disabled_reason(
            demo=False,
            site_name="Plant",
            file_count=3,
            kind="checking",
            demo_ok=False,
        )
        == "Wait until the files have been checked."
    )
    assert (
        step1_disabled_reason(
            demo=False,
            site_name="Plant",
            file_count=3,
            kind="invalid",
            demo_ok=False,
        )
        == "Resolve the file errors above."
    )
    assert (
        step1_disabled_reason(
            demo=False,
            site_name="Plant",
            file_count=3,
            kind="ready",
            demo_ok=False,
        )
        is None
    )
    assert (
        step1_disabled_reason(
            demo=True,
            site_name="Ganda Cars",
            file_count=0,
            kind="empty",
            demo_ok=False,
        )
        == "Restore the demo files."
    )
    assert (
        step1_disabled_reason(
            demo=True,
            site_name="Ganda Cars",
            file_count=0,
            kind="empty",
            demo_ok=True,
        )
        is None
    )


def test_named_live_empty_upload_shows_upload_reason() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    at.text_input[0].set_value("Plant A")
    at.run()
    assert not at.exception
    combined = _text(at)
    assert f"{_REASON_PREFIX}Upload the three Fluvius CSV files." in combined
    assert "Enter a site or project name." not in combined
    assert _button(at, "Continue").proto.disabled


def test_injected_live_step2_uses_snapshot() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    state = default_state()
    state["step"] = 2
    state["max_step"] = 2
    state["site_name"] = "Plant A"
    state["data_ready"] = True
    state["ingest_snapshot"] = {
        "ok": True,
        "roles": {
            "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 10},
            "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 11},
            "pv": {"register": "Productie Actief", "unit": "kWh", "n_rows": 12},
        },
        "sources": [{"path": "offtake.csv"}],
        "issues": [],
        "periods": [
            {
                "id": "2024",
                "kind": "full_calendar_year",
                "label": "Calendar year 2024",
                "n_intervals": 100,
                "n_unvalidated": 2,
            }
        ],
        "dst": {"n_spring_skipped_wall_clock": 6, "n_autumn_repeated_wall_clock": 28},
        "error": None,
    }
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    combined = _text(at)
    assert "Data verification" in [item.value for item in at.header]
    assert "Files usable" in combined
    assert "Calendar year 2024" in combined
    assert "Checked after period selection" not in combined
    assert "Day-ahead prices" not in combined
    assert _STEP2_LEAD in combined
    assert _PERIOD_LEAD in combined
    assert "Local timestamps were converted to UTC." in combined
    assert "Timestamps converted" in combined
    assert DST_PENDING in combined
    assert "DST details" not in _expander_labels(at)
    assert "Quarter-hours in local day" not in combined
    assert "n_spring_skipped_wall_clock" not in combined
    assert _button(at, "Continue").proto.disabled is False


def test_injected_demo_step2_hides_structured_dst() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    state = default_state()
    state["step"] = 2
    state["max_step"] = 2
    state["data_route"] = ROUTE_SAVED
    state["site_name"] = "Ganda Cars"
    state["data_ready"] = True
    state["ingest_snapshot"] = {
        "ok": True,
        "roles": {
            "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 10},
            "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 11},
            "pv": {"register": "Productie Actief", "unit": "kWh", "n_rows": 12},
        },
        "sources": [{"path": "Historiek_afname.csv"}],
        "issues": [],
        "periods": [
            {
                "id": "2024",
                "kind": "full_calendar_year",
                "label": "Calendar year 2024",
                "n_intervals": 100,
                "n_unvalidated": 2,
            }
        ],
        "dst": {
            "n_spring_skipped_wall_clock": 6,
            "transitions": [
                {
                    "date_local": "2024-03-31",
                    "kind": "spring_forward",
                    "physical_quarter_hours_in_local_day": 92,
                }
            ],
        },
        "error": None,
    }
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    combined = _text(at)
    assert "Data verification" in [item.value for item in at.header]
    assert "Timestamps converted" in combined
    assert "Local timestamps were converted to UTC." in combined
    assert DST_PENDING in combined
    assert "DST details" not in _expander_labels(at)
    assert "Quarter-hours in local day" not in combined
    assert "2024-03-31" not in combined
    assert _button(at, "Continue").proto.disabled is False
    at = AppTest.from_file(str(APP), default_timeout=12)
    stale = default_state()
    stale["step"] = 2
    stale["max_step"] = 2
    at.session_state[SESSION_KEY] = stale
    at.run()
    assert not at.exception
    combined = _text(at)
    assert "The files must be checked again" in combined
    assert f"{_REASON_PREFIX}Return to Upload data and check the files again." in combined
    assert _button(at, "Continue").proto.disabled

    blocked = default_state()
    blocked["step"] = 2
    blocked["max_step"] = 2
    blocked["ingest_snapshot"] = {
        "ok": True,
        "roles": {
            "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 1},
            "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 1},
            "pv": {"register": "Productie Actief", "unit": "kWh", "n_rows": 1},
        },
        "issues": [],
        "periods": [],
        "dst": {},
        "error": None,
    }
    at.session_state[SESSION_KEY] = blocked
    at.run()
    assert not at.exception
    no_period = _text(at)
    assert "No usable simulation period was found" in no_period
    assert f"{_REASON_PREFIX}No usable simulation period was found." in no_period
    assert _button(at, "Continue").proto.disabled


def test_live_view_kinds() -> None:
    assert live_view_kind(file_count=0, inspecting=False, snapshot=None) == "empty"
    assert live_view_kind(file_count=2, inspecting=False, snapshot=None) == "wrong_count"
    assert live_view_kind(file_count=3, inspecting=True, snapshot=None) == "checking"
    assert (
        live_view_kind(
            file_count=3,
            inspecting=False,
            snapshot={"ok": False, "roles": {}, "error": None},
        )
        == "invalid"
    )
    assert (
        live_view_kind(
            file_count=3,
            inspecting=False,
            snapshot={
                "ok": True,
                "roles": {"offtake": {}, "injection": {}, "pv": {}},
                "error": None,
            },
        )
        == "ready"
    )


def _live_period_state(*, n_unvalidated: int = 2) -> dict:
    state = default_state()
    state["step"] = 3
    state["max_step"] = 3
    state["site_name"] = "Plant A"
    state["data_ready"] = True
    state["upload_payloads"] = (("offtake.csv", b"a"), ("injection.csv", b"b"), ("pv.csv", b"c"))
    state["upload_signature"] = (("offtake.csv", 1, "a"), ("injection.csv", 1, "b"), ("pv.csv", 1, "c"))
    state["ingest_snapshot"] = {
        "ok": True,
        "roles": {
            "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 10},
            "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 11},
            "pv": {"register": "Productie Actief", "unit": "kWh", "n_rows": 12},
        },
        "sources": [{"path": "offtake.csv"}],
        "issues": [],
        "periods": [
            {
                "id": "2024",
                "kind": "full_calendar_year",
                "label": "Calendar year 2024",
                "n_intervals": 100,
                "n_unvalidated": n_unvalidated,
                "complete_calendar_year": True,
                "start_local": "2024-01-01T00:00:00+01:00",
                "end_local_exclusive": "2025-01-01T00:00:00+01:00",
            }
        ],
        "dst": {},
        "error": None,
    }
    return state


def test_live_step3_unvalidated_ack_does_not_self_set(monkeypatch) -> None:
    inspect_kwargs: list[dict] = []

    def fake_inspect(_payloads, period_id, **kwargs):
        inspect_kwargs.append(dict(kwargs))
        return {
            "ok": True,
            "requires_site_boundary_acknowledgement": False,
            "period_id": period_id,
            "selected_period": {"id": period_id, "n_unvalidated": 2},
            "fatal": [],
            "warnings": [],
            "report": {
                "unvalidated_policy": {
                    "dates": ["2024-10-02"],
                    "n_unvalidated_in_selected_period": 2,
                }
            },
            "site_analysis": {"n_intervals": 100, "durations_hours": [2.0, 4.0]},
            "automatic_candidates": [],
        }

    monkeypatch.setattr("ui.views.choose_period.inspect_period_payloads", fake_inspect)
    monkeypatch.setattr(
        "ui.views.choose_period.price_coverage_for_payloads",
        lambda *_args, **_kwargs: {
            "covered": True,
            "unavailable": False,
            "one_battery_unavailable": False,
            "selected_row_count": 100,
            "source_basename": "da_prices_qh.parquet",
            "coverage_utc": ["2015-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
            "native_resolution_counts": {"PT15M": 100},
            "hourly_values_repeated": False,
            "error": None,
        },
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _live_period_state()
    at.run()
    assert not at.exception
    combined = _text(at)
    assert "Simulation period" in [item.value for item in at.header]
    assert "Data contains 2 unvalidated quarter-hours" in combined
    assert "2024-10-02" in combined
    assert "Period checks passed" not in combined
    assert inspect_kwargs
    assert inspect_kwargs[0]["acknowledge_site_boundary"] is False
    box = _checkbox(at, [item.label for item in at.checkbox if "unvalidated" in item.label.lower()][0])
    assert box.value is False
    assert f"{_REASON_PREFIX}Acknowledge the unvalidated readings." in combined
    assert _button(at, "Continue").proto.disabled
    box.check()
    at.run()
    assert not at.exception
    after = _text(at)
    assert "Period checks passed" in after
    assert _button(at, "Continue").proto.disabled is False
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    assert "Configure options" in [item.value for item in at.header]
    assert "Configuration is not implemented yet." not in _text(at)
    assert "Evaluate one battery" in _labels(at)
    assert _button(at, "Continue").proto.disabled is False


def test_live_step3_omits_empty_unvalidated_caption(monkeypatch) -> None:
    def fake_inspect(_payloads, period_id, **kwargs):
        del kwargs
        return {
            "ok": True,
            "requires_site_boundary_acknowledgement": False,
            "period_id": period_id,
            "selected_period": {"id": period_id, "n_unvalidated": 0},
            "fatal": [],
            "warnings": [],
            "report": {
                "unvalidated_policy": {
                    "dates": [],
                    "n_unvalidated_in_selected_period": 0,
                }
            },
            "site_analysis": {"n_intervals": 100, "durations_hours": [2.0, 4.0]},
            "automatic_candidates": [],
        }

    monkeypatch.setattr("ui.views.choose_period.inspect_period_payloads", fake_inspect)
    monkeypatch.setattr(
        "ui.views.choose_period.price_coverage_for_payloads",
        lambda *_args, **_kwargs: {
            "covered": True,
            "unavailable": False,
            "one_battery_unavailable": False,
            "selected_row_count": 100,
            "source_basename": "da_prices_qh.parquet",
            "coverage_utc": ["2015-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
            "native_resolution_counts": {"PT15M": 100},
            "hourly_values_repeated": False,
            "error": None,
        },
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _live_period_state(n_unvalidated=0)
    at.run()
    assert not at.exception
    combined = _text(at)
    assert "Simulation period" in [item.value for item in at.header]
    assert "no acknowledgement is required" not in combined
    assert "Data contains" not in combined
    assert "unvalidated" not in " ".join(item.label.lower() for item in at.checkbox)


def test_live_step3_shows_structured_dst_in_period_details(monkeypatch) -> None:
    def fake_inspect(_payloads, period_id, **kwargs):
        del kwargs
        return {
            "ok": True,
            "requires_site_boundary_acknowledgement": False,
            "period_id": period_id,
            "selected_period": {"id": period_id, "n_unvalidated": 0},
            "fatal": [],
            "warnings": [],
            "report": {
                "dst": {
                    "n_spring_skipped_wall_clock": 4,
                    "transitions": [
                        {
                            "date_local": "2024-03-31",
                            "kind": "spring_forward",
                            "physical_quarter_hours_in_local_day": 92,
                        }
                    ],
                }
            },
            "site_analysis": {"n_intervals": 100, "durations_hours": [2.0, 4.0]},
            "automatic_candidates": [],
        }

    monkeypatch.setattr("ui.views.choose_period.inspect_period_payloads", fake_inspect)
    monkeypatch.setattr(
        "ui.views.choose_period.price_coverage_for_payloads",
        lambda *_args, **_kwargs: {
            "covered": True,
            "unavailable": False,
            "one_battery_unavailable": False,
            "selected_row_count": 100,
            "source_basename": "da_prices_qh.parquet",
            "coverage_utc": ["2015-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
            "native_resolution_counts": {"PT15M": 100},
            "hourly_values_repeated": False,
            "error": None,
        },
    )
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _live_period_state(n_unvalidated=0)
    at.run()
    assert not at.exception
    combined = _text(at)
    assert "Simulation period" in [item.value for item in at.header]
    assert "Period details" in _expander_labels(at)
    assert "DST details" not in _expander_labels(at)
    assert "2024-03-31" in combined
    assert "Forward" in combined
    assert "92" in combined
    assert "n_spring_skipped_wall_clock" not in combined
    assert DST_PENDING not in combined
    from ui.tests.test_review import freeze_one, ready_review_state

    def _boom(*_args, **_kwargs):
        raise AssertionError("public request builders must not run on Review")

    monkeypatch.setattr("btm_sim.run.build_run_request", _boom)
    monkeypatch.setattr("btm_sim.sweep.build_sweep_request", _boom)
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = freeze_one(ready_review_state())
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Review and run" in [item.value for item in at.header]
    assert "Confirm configuration before running the simulation." in page
    assert "No battery" in page
    assert "Rule-based control" in page
    assert "Dynamic injection tariff" in page
    assert "Plant A" in page
    assert "Single battery, multiple dispatch strategies" in page
    assert _button(at, "Run simulation").proto.disabled is False
    assert "Execution will be connected in the next phase." not in page
    assert "To continue:" not in page
    assert "Diagnostics" in _expander_labels(at)
    diag = [item for item in at.expander if "Diagnostics" in str(getattr(item, "label", "") or "")]
    assert getattr(diag[0], "proto").expanded is False
    assert "Show detailed solver output in the run log" in [item.label for item in at.checkbox]
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert "Configure options" in [item.value for item in at.header]
    assert at.session_state[SESSION_KEY]["configure"]["snapshot"]["analysis_mode"] == "one-battery"


def test_live_sizing_review_keeps_candidates_collapsed(monkeypatch) -> None:
    from ui.tests.test_review import freeze_size, ready_review_state

    def _boom(*_args, **_kwargs):
        raise AssertionError("public request builders must not run on Review")

    monkeypatch.setattr("btm_sim.run.build_run_request", _boom)
    monkeypatch.setattr("btm_sim.sweep.build_sweep_request", _boom)
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = freeze_size(ready_review_state())
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Confirm the inputs before running the battery-size comparison." in page
    assert "Dispatch strategy" in page
    assert "Revenue maximisation" in page
    assert _button(at, "Run battery-size comparison").proto.disabled is False
    labels = _expander_labels(at)
    assert any(str(label).startswith("Battery sizes") for label in labels)
    cand = [
        item
        for item in at.expander
        if str(getattr(item, "label", "") or "").startswith("Battery sizes")
    ]
    assert cand
    assert cand[0].proto.expanded is False
    diag = [item for item in at.expander if "Diagnostics" in str(getattr(item, "label", "") or "")]
    assert diag
    assert diag[0].proto.expanded is False
    assert "Execution will be connected in the next phase." not in page


def _execution_state(tmp_path: Path, *, klass: str = "running"):
    import json

    from datetime import datetime, timezone

    from ui.tests.test_review import freeze_one, ready_review_state

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output = tmp_path / "run"
    output.mkdir()
    status = {
        "job_id": "btm-ui",
        "state": "running" if klass == "running" else klass,
        "output_dir": str(output),
        "message": "Solving revenue case",
        "stage_number": 4,
        "stage_total": 12,
        "updated_at_utc": now,
        "started_at_utc": now,
        "artifact_schema_version": 2,
        "error_message": "Solver failed" if klass == "failed" else None,
        "error_category": "solver" if klass == "failed" else None,
    }
    (output / "run_status.json").write_text(json.dumps(status), encoding="utf-8")
    state = freeze_one(ready_review_state())
    state["step"] = 6
    state["max_step"] = 6
    state["job"] = {
        "version": 1,
        "job_id": "btm-ui",
        "kind": "comparison",
        "output_dir": str(output),
        "request_path": str(output / "run_request.json"),
        "staging_dir": str(tmp_path / "_ui_staging" / "btm-ui"),
        "worker_console_path": str(tmp_path / "_ui_staging" / "btm-ui" / "worker_stdout.log"),
        "pid": 9,
        "launch_state": "launched" if klass in {"queued", "running"} else "terminal",
        "launch_utc": now,
        "site": "Plant A",
        "period_id": "2024",
        "period_label": "Calendar year 2024",
        "fingerprint": state["review"]["fingerprint"],
        "data_route": "live",
        "lock_navigation": klass != "ready",
    }
    if klass == "ready":
        state["results"] = {
            "version": 1,
            "kind": "comparison",
            "result_dir": str(output),
            "source": "live",
            "demo": False,
            "job_id": "btm-ui",
            "validated": True,
            "site": "Plant A",
            "period_id": "2024",
            "period_label": "Calendar year 2024",
        }
        state["job"]["launch_state"] = "completed"
        state["job"]["lock_navigation"] = False
    if klass == "incomplete":
        status["state"] = "completed"
        (output / "run_status.json").write_text(json.dumps(status), encoding="utf-8")
        state["job"]["launch_state"] = "launched"
        state["job"]["lock_navigation"] = True
    return state


def test_running_execution_shows_stage_and_locks_navigation(tmp_path: Path) -> None:
    state = _execution_state(tmp_path, klass="running")
    job = state["job"]
    output = Path(job["output_dir"])
    (output / "run_request.json").write_text("{}", encoding="utf-8")
    (output / "run_events.jsonl").write_text("", encoding="utf-8")
    (output / "run.log").write_text("Run started\n", encoding="utf-8")
    console = Path(job["worker_console_path"])
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_text("worker\n", encoding="utf-8")
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = state
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Simulation running" in page
    assert "Plant A" in page
    assert "Calendar year 2024" in page
    assert "Solving revenue case" in page
    assert "Stage 4 of 12" in page
    assert "The bar follows completed stages. It is not a solver percentage." in page
    assert "Cancel" not in _labels(at)
    assert "Back" not in _labels(at)
    assert "Run log" in _expander_labels(at)
    log = [item for item in at.expander if "Run log" in str(getattr(item, "label", "") or "")]
    assert log and log[0].proto.expanded is False
    downloads = [item.label for item in at.download_button]
    assert "Request" not in downloads
    assert "Status" not in downloads
    assert "Events" not in downloads
    assert "Run log" not in downloads
    assert "Worker console" not in downloads
    enabled = [item.label for item in at.button if not item.proto.disabled]
    assert stage_button_label(1, "Upload data") not in enabled
    assert stage_button_label(5, "Review and run") not in enabled


def test_failed_execution_offers_return_not_open_results(tmp_path: Path) -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _execution_state(tmp_path, klass="failed")
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Simulation failed" in page
    assert "Partial results are not opened." in page
    assert "Open results" not in _labels(at)
    assert "Cancel" not in _labels(at)
    assert "Return to Review" in _labels(at)
    assert "Diagnostics" in _expander_labels(at)


def test_incomplete_results_offer_return_not_open_results(tmp_path: Path) -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _execution_state(tmp_path, klass="incomplete")
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results could not be opened" in page
    assert "Partial results are not opened." in page
    assert "Open results" not in _labels(at)
    assert "Cancel" not in _labels(at)
    assert "Return to Review" in _labels(at)
    diag = [item for item in at.expander if "Diagnostics" in str(getattr(item, "label", "") or "")]
    assert diag and diag[0].proto.expanded is False


def test_invalid_ready_comparison_does_not_invent_values(tmp_path: Path) -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.session_state[SESSION_KEY] = _execution_state(tmp_path, klass="ready")
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results could not be displayed" in page
    assert "The stored result files could not be read." in page
    assert "Return to Review" in _labels(at)
    assert "Cancel" not in _labels(at)
    assert "Return to Review" in _labels(at)


def test_demo_review_opens_full_comparison_results() -> None:
    from ui.tests.test_review import freeze_one, ready_review_state

    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state[SESSION_KEY] = freeze_one(ready_review_state(demo=True))
    at.run()
    assert not at.exception
    _button(at, "View saved demonstration results").click()
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Results ready" not in page
    assert "Demo site: results" in page
    assert [item.label for item in at.tabs][0] == "Overview"
    assert at.session_state[SESSION_KEY]["results"]["source"] == "demo"
    assert "job" not in at.session_state[SESSION_KEY]
    assert "Cancel" not in _labels(at)
