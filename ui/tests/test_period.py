from __future__ import annotations

from ui.services.period import (
    REASON_BOUNDARY,
    REASON_NO_PERIOD,
    REASON_OTHER,
    REASON_STALE,
    REASON_UNVALIDATED,
    classification_line,
    continue_disabled_reason,
    default_period_id,
    meter_boundary_facts,
    message_cannot_create_ack,
    needs_meter_boundary_ack,
    non_acknowledgeable_fatals,
    ordered_period_ids,
    period_option_label,
    selected_dst_rows,
    show_meter_boundary_panel,
    simultaneous_diagnostic,
    unvalidated_checkbox_label,
    unvalidated_dates_in_period,
    unvalidated_detail,
    unvalidated_warning_title,
)


def _periods() -> list[dict[str, object]]:
    return [
        {
            "id": "common",
            "kind": "common_overlap",
            "label": "Continuous common measured overlap",
            "n_intervals": 200,
            "n_unvalidated": 0,
            "complete_calendar_year": False,
        },
        {
            "id": "2023",
            "kind": "full_calendar_year",
            "label": "Calendar year 2023",
            "n_intervals": 35040,
            "n_unvalidated": 0,
            "complete_calendar_year": True,
        },
        {
            "id": "2024",
            "kind": "full_calendar_year",
            "label": "Calendar year 2024",
            "n_intervals": 35136,
            "n_unvalidated": 96,
            "complete_calendar_year": True,
        },
        {
            "id": "2023p",
            "kind": "partial_calendar_year",
            "label": "Partial calendar year 2023",
            "n_intervals": 50,
            "n_unvalidated": 0,
            "complete_calendar_year": False,
        },
    ]


def test_complete_years_sort_latest_first_then_stored_remainder() -> None:
    assert ordered_period_ids(_periods()) == ["2024", "2023", "common", "2023p"]
    assert default_period_id(_periods()) == "2024"


def test_default_is_first_stored_period_without_complete_year() -> None:
    periods = [
        {"id": "common", "complete_calendar_year": False},
        {"id": "2023p", "complete_calendar_year": False},
    ]
    assert default_period_id(periods) == "common"


def test_option_labels_use_stored_facts_without_recommended_language() -> None:
    period = next(item for item in _periods() if item["id"] == "2024")
    label = period_option_label(period)
    assert label == "Calendar year 2024 · complete year · 35,136 quarter-hours · 96 unvalidated"
    assert "Recommended" not in label
    assert "Saved demonstration" not in label
    assert classification_line(period) == "Complete calendar year · 35,136 quarter-hours"


def test_selected_dst_rows_use_structured_transitions_only() -> None:
    counters_only = {
        "report": {
            "dst": {
                "n_spring_skipped_wall_clock": 4,
                "n_autumn_repeated_wall_clock": 12,
            }
        }
    }
    assert selected_dst_rows(counters_only) == ()
    assert selected_dst_rows({"report": {}}) == ()
    assert selected_dst_rows(None) == ()
    rows = selected_dst_rows(
        {
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
            }
        }
    )
    assert rows[0]["Local date"] == "2024-03-31"
    assert rows[0]["Direction"] == "Forward"
    assert rows[0]["Quarter-hours in local day"] == "92"


def test_unvalidated_dates_filter_to_structured_local_bounds() -> None:
    period = {
        "start_local": "2024-01-01T00:00:00+01:00",
        "end_local_exclusive": "2025-01-01T00:00:00+01:00",
    }
    dates = unvalidated_dates_in_period(period, ["2023-12-31", "2024-10-02", "2025-01-01"])
    assert dates == ("2024-10-02",)
    assert unvalidated_warning_title(96) == "Data contains 96 unvalidated quarter-hours"
    detail = unvalidated_detail(dates)
    assert "Affected local date: 2024-10-02" in detail
    assert "Only non-empty readings are used." in detail
    assert "Non-empty Ongevalideerd" not in detail
    assert unvalidated_checkbox_label(96, dates) == (
        "Use 96 unvalidated readings on 2024-10-02. Only non-empty readings are used."
    )


def test_ave_maria_meter_boundary_facts_are_not_labelled_ganda() -> None:
    inspection = {
        "requires_site_boundary_acknowledgement": True,
        "fatal": [
            {
                "code": "NEGATIVE_LOAD",
                "message": "negative site load",
                "details": {
                    "count": 24,
                    "total_negative_load_kwh": 27.388,
                    "min_kwh": -2.662,
                    "affected_local_dates": ["2025-08-13", "2025-08-19"],
                    "first_local_timestamp": "2025-08-13T00:00:00+02:00",
                    "last_local_timestamp": "2025-08-19T00:00:00+02:00",
                },
            },
            {
                "code": "EXPORT_EXCEEDS_PV",
                "message": "export exceeds pv",
                "details": {
                    "count": 22,
                    "max_excess_kwh": 1.25,
                    "threshold_kwh": 0.05,
                    "total_excess_kwh": 4.0,
                    "affected_local_dates": ["2025-08-13", "2025-08-19"],
                },
            },
        ],
        "warnings": [],
    }
    facts = meter_boundary_facts(inspection)
    joined = " ".join(facts)
    assert "Affected quarter-hours: 24" in facts
    assert "Total energy difference: 27.388 kWh" in facts
    assert "Largest interval difference: 2.662 kWh" in facts
    assert "Quarter-hours where export exceeds measured PV: 22" in facts
    assert "Maximum interval excess: 1.25 kWh" in facts
    assert joined.count("Affected quarter-hours") == 1
    assert "Ganda" not in joined
    from ui.services.period import meter_boundary_detail

    detail = meter_boundary_detail(inspection)
    assert "Affected quarter-hours: 24" in detail
    assert "Export-exceeds-PV threshold" not in detail
    assert "negative site consumption" not in detail
    assert show_meter_boundary_panel(inspection)
    assert needs_meter_boundary_ack(inspection)


def test_message_text_cannot_create_or_suppress_meter_boundary_ack() -> None:
    noisy = {
        "requires_site_boundary_acknowledgement": False,
        "fatal": [
            {
                "code": "UNKNOWN_PERIOD",
                "message": "NEGATIVE_LOAD looks present and export exceeds PV",
                "details": {},
            }
        ],
        "warnings": [],
    }
    assert show_meter_boundary_panel(noisy) is False
    assert needs_meter_boundary_ack(noisy) is False
    assert message_cannot_create_ack(noisy) is True
    suppressed = {
        "requires_site_boundary_acknowledgement": True,
        "fatal": [
            {
                "code": "NEGATIVE_LOAD",
                "message": "this is fine, no meter issue",
                "details": {"count": 1, "total_negative_load_kwh": 0.5},
            }
        ],
        "warnings": [],
    }
    assert show_meter_boundary_panel(suppressed) is True


def test_mixed_fatal_is_not_acknowledgeable() -> None:
    inspection = {
        "requires_site_boundary_acknowledgement": False,
        "fatal": [
            {"code": "NEGATIVE_LOAD", "message": "boundary", "details": {"count": 2}},
            {"code": "UNVALIDATED_NOT_ALLOWED", "message": "unvalidated", "details": {}},
        ],
        "warnings": [],
    }
    assert show_meter_boundary_panel(inspection) is False
    codes = {item["code"] for item in non_acknowledgeable_fatals(inspection)}
    assert codes == {"NEGATIVE_LOAD", "UNVALIDATED_NOT_ALLOWED"}


def test_simultaneous_import_export_never_gates() -> None:
    inspection = {
        "ok": True,
        "requires_site_boundary_acknowledgement": False,
        "site_analysis": {"n_intervals": 4},
        "fatal": [],
        "warnings": [],
        "report": {
            "simultaneous_import_export": {
                "n_intervals": 3,
                "note": "directional totals",
            }
        },
    }
    assert simultaneous_diagnostic(inspection)["n_intervals"] == 3
    assert needs_meter_boundary_ack(inspection) is False
    reason = continue_disabled_reason(
        stale=False,
        demo_blocked=False,
        selected={"id": "2024"},
        inspection=inspection,
        inspection_running=False,
        needs_unvalidated=False,
        unvalidated_ack=False,
        needs_boundary=False,
        boundary_ack=False,
        inspection_usable=True,
    )
    assert reason is None


def test_continue_reason_priority() -> None:
    assert (
        continue_disabled_reason(
            stale=True,
            demo_blocked=False,
            selected=None,
            inspection=None,
            inspection_running=False,
            needs_unvalidated=True,
            unvalidated_ack=False,
            needs_boundary=True,
            boundary_ack=False,
            inspection_usable=False,
        )
        == REASON_STALE
    )
    assert (
        continue_disabled_reason(
            stale=False,
            demo_blocked=False,
            selected=None,
            inspection=None,
            inspection_running=False,
            needs_unvalidated=True,
            unvalidated_ack=False,
            needs_boundary=True,
            boundary_ack=False,
            inspection_usable=False,
        )
        == REASON_NO_PERIOD
    )
    assert (
        continue_disabled_reason(
            stale=False,
            demo_blocked=False,
            selected={"id": "2024"},
            inspection={"ok": False},
            inspection_running=False,
            needs_unvalidated=True,
            unvalidated_ack=False,
            needs_boundary=True,
            boundary_ack=False,
            inspection_usable=False,
        )
        == REASON_UNVALIDATED
    )
    assert (
        continue_disabled_reason(
            stale=False,
            demo_blocked=False,
            selected={"id": "2024"},
            inspection={"ok": False, "requires_site_boundary_acknowledgement": True},
            inspection_running=False,
            needs_unvalidated=False,
            unvalidated_ack=True,
            needs_boundary=True,
            boundary_ack=False,
            inspection_usable=False,
        )
        == REASON_BOUNDARY
    )
    assert (
        continue_disabled_reason(
            stale=False,
            demo_blocked=False,
            selected={"id": "2024"},
            inspection={"ok": False, "error": {"code": "INSPECTION_FAILED"}},
            inspection_running=False,
            needs_unvalidated=False,
            unvalidated_ack=False,
            needs_boundary=False,
            boundary_ack=False,
            inspection_usable=False,
        )
        == REASON_OTHER
    )
