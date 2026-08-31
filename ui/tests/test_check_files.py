from __future__ import annotations

from ui.services.check_files import (
    EAN_JOIN_NOTE,
    REASON_FATALS,
    REASON_NO_PERIODS,
    REASON_STALE,
    SIMULTANEOUS_NOTE,
    build_check_files_model,
    dst_transition_rows,
    ordered_periods,
    snapshot_is_renderable,
)
from ui.services.saved_example import project_validation_report


def _roles() -> dict[str, dict[str, object]]:
    return {
        "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 10, "ean": "111"},
        "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 11, "ean": "222"},
        "pv": {"register": "Productie Actief", "unit": "kWh", "n_rows": 12, "ean": "333"},
    }


def _periods() -> list[dict[str, object]]:
    return [
        {
            "id": "common",
            "kind": "common_overlap",
            "label": "Continuous common measured overlap",
            "n_intervals": 200,
            "n_unvalidated": 4,
            "start_local": "2023-11-08T00:00:00+01:00",
            "end_local_exclusive": "2025-10-27T00:00:00+01:00",
        },
        {
            "id": "2023",
            "kind": "partial_calendar_year",
            "label": "Partial calendar year 2023",
            "n_intervals": 50,
            "n_unvalidated": 0,
        },
        {
            "id": "2024",
            "kind": "full_calendar_year",
            "label": "Calendar year 2024",
            "n_intervals": 100,
            "n_unvalidated": 2,
        },
        {
            "id": "roll",
            "kind": "rolling_twelve_months",
            "label": "Rolling twelve months",
            "n_intervals": 80,
            "n_unvalidated": 1,
        },
    ]


def _snapshot(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "roles": _roles(),
        "sources": [{"path": "/tmp/btm_v2_upload_abc/offtake.csv"}],
        "issues": [],
        "periods": _periods(),
        "dst": {"n_spring_skipped_wall_clock": 6, "n_autumn_repeated_wall_clock": 28},
        "error": None,
    }
    payload.update(overrides)
    return payload


def test_live_and_saved_shapes_share_view_model() -> None:
    live = _snapshot()
    saved = project_validation_report(
        {
            "ok": True,
            "roles": _roles(),
            "sources": [{"path": r"C:\exports\offtake.csv", "registers": ["Afname Actief"]}],
            "warnings": [],
            "fatal": [],
            "periods": _periods(),
            "dst": {},
        }
    )
    live_model = build_check_files_model(live, price_filename="da_prices_qh.parquet")
    saved_model = build_check_files_model(saved, price_filename="da_prices_qh.parquet")
    assert [row["Role"] for row in live_model.role_rows] == [
        "Offtake",
        "Injection",
        "PV production",
    ]
    assert live_model.role_rows == saved_model.role_rows
    assert [row["Period"] for row in live_model.period_rows] == [
        row["Period"] for row in saved_model.period_rows
    ]


def test_roles_omit_ean_and_format_rows() -> None:
    model = build_check_files_model(_snapshot(), price_filename=None)
    combined = " ".join(str(row) for row in model.role_rows)
    assert "ean" not in combined.lower()
    assert "111" not in combined
    assert model.role_rows[0]["Rows"] == "10"
    assert model.role_rows[1]["Rows"] == "11"
    sources = str((_snapshot().get("sources") or [{}])[0])
    assert "/tmp/" in sources
    assert "/tmp/" not in combined


def test_common_overlap_uses_stored_counts() -> None:
    model = build_check_files_model(_snapshot(), price_filename=None)
    metrics = dict(model.coverage_metrics)
    assert metrics["Quarter-hours in common coverage"] == "200"
    assert metrics["Unvalidated in that coverage"] == "4"
    assert metrics["Fatal errors"] == "0"


def test_missing_common_overlap_is_not_available() -> None:
    periods = [item for item in _periods() if item["kind"] != "common_overlap"]
    model = build_check_files_model(_snapshot(periods=periods), price_filename=None)
    metrics = dict(model.coverage_metrics)
    assert metrics["Quarter-hours in common coverage"] == "Not available"
    assert metrics["Unvalidated in that coverage"] == "Not available"


def test_candidate_period_order_and_columns() -> None:
    model = build_check_files_model(_snapshot(), price_filename="da_prices_qh.parquet")
    labels = [row["Period"] for row in model.period_rows]
    assert labels == [
        "Calendar year 2024",
        "Partial calendar year 2023",
        "Rolling twelve months",
        "Continuous common measured overlap",
    ]
    assert list(model.period_rows[0].keys()) == [
        "Period",
        "Type",
        "Quarter-hours",
        "Unvalidated",
    ]
    assert "Day-ahead prices" not in model.period_rows[0]
    assert model.price_available is True


def test_ignored_registers_come_only_from_structured_warning() -> None:
    without = build_check_files_model(_snapshot(), price_filename=None)
    assert without.ignored_rows == ()
    snapshot = _snapshot(
        issues=[
            {
                "severity": "warning",
                "code": "UNUSED_REGISTERS",
                "details": {
                    "unused": [
                        {
                            "register": "Afname Capacitief",
                            "eans": ["541448860020928494"],
                            "n_rows": 10,
                            "units": ["kVArh"],
                        },
                        {
                            "register": "Afname Capacitief",
                            "eans": ["541448860020928494"],
                            "n_rows": 10,
                            "units": ["kVArh"],
                        },
                    ]
                },
            }
        ]
    )
    model = build_check_files_model(snapshot, price_filename=None)
    assert len(model.ignored_rows) == 1
    assert model.ignored_rows[0]["Register"] == "Afname Capacitief"
    assert "ean" not in str(model.ignored_rows).lower()
    assert "541448860020928494" not in str(model.ignored_rows)


def test_duplicate_warnings_and_step3_decisions_are_not_shown() -> None:
    snapshot = _snapshot(
        issues=[
            {"severity": "warning", "code": "EAN_MISMATCH", "details": {"eans": {"offtake": "1"}}},
            {"severity": "warning", "code": "EAN_MISMATCH", "details": {"eans": {"offtake": "1"}}},
            {"severity": "warning", "code": "PARTIAL_CALENDAR_YEARS", "details": {"years": ["2023"]}},
            {"severity": "warning", "code": "UNVALIDATED_USED", "details": {"count": 96}},
            {"severity": "warning", "code": "NO_COMPLETE_CALENDAR_YEAR"},
        ],
        simultaneous_import_export={"n_intervals": 3, "note": "keep separate"},
    )
    model = build_check_files_model(snapshot, price_filename=None)
    assert model.check_detail_notes == (EAN_JOIN_NOTE, SIMULTANEOUS_NOTE)
    assert model.no_complete_year is True
    assert "96" not in " ".join(model.check_detail_notes)
    assert "acknowledg" not in " ".join(model.check_detail_notes).lower()


def test_stale_fatal_and_no_period_reasons() -> None:
    assert build_check_files_model(None, price_filename=None).disabled_reason == REASON_STALE
    assert snapshot_is_renderable({"ok": True, "saved_example": True, "roles": {"offtake": {}}}) is False
    fatal = _snapshot(
        ok=False,
        issues=[{"severity": "fatal", "code": "MISSING_REGISTER", "details": {"role": "pv"}}],
        periods=[],
        roles={"offtake": {"register": "Afname Actief"}, "injection": {"register": "Injectie Actief"}},
    )
    fatal_model = build_check_files_model(fatal, price_filename=None)
    assert fatal_model.can_continue is False
    assert fatal_model.disabled_reason == REASON_FATALS
    empty_periods = build_check_files_model(_snapshot(periods=[]), price_filename=None)
    assert empty_periods.disabled_reason == REASON_NO_PERIODS
    ready = build_check_files_model(_snapshot(), price_filename=None)
    assert ready.can_continue is True
    assert ready.disabled_reason is None


def test_dst_does_not_treat_parser_counters_as_clock_changes() -> None:
    snapshot = _snapshot()
    pending = build_check_files_model(snapshot, price_filename=None)
    assert pending.dst_converted is True
    assert pending.dst_transition_rows == ()
    assert pending.dst_pending_detail is True
    assert dst_transition_rows(snapshot) == ()


def test_step2_does_not_surface_structured_transitions() -> None:
    snapshot = _snapshot(
        dst={
            "n_spring_skipped_wall_clock": 6,
            "transitions": [
                {
                    "date_local": "2024-03-31",
                    "kind": "spring_forward",
                    "physical_quarter_hours_in_local_day": 92,
                }
            ],
        }
    )
    model = build_check_files_model(snapshot, price_filename=None)
    assert model.dst_converted is True
    assert model.dst_pending_detail is True
    assert model.dst_transition_rows == ()
    rows = dst_transition_rows(snapshot)
    assert rows[0]["Local date"] == "2024-03-31"
    assert rows[0]["Direction"] == "Forward"
    assert rows[0]["Quarter-hours in local day"] == "92"


def test_price_file_does_not_block_continue() -> None:
    available = build_check_files_model(_snapshot(), price_filename="da_prices_qh.parquet")
    missing = build_check_files_model(_snapshot(), price_filename=None)
    assert available.can_continue is True
    assert missing.can_continue is True
    assert missing.price_available is False


def test_ordered_periods_preserve_source_order_within_kind() -> None:
    ordered = ordered_periods(
        [
            {"kind": "partial_calendar_year", "id": "2025"},
            {"kind": "partial_calendar_year", "id": "2023"},
            {"kind": "full_calendar_year", "id": "2024"},
        ]
    )
    assert [item["id"] for item in ordered] == ["2024", "2025", "2023"]


def test_snapshot_does_not_carry_a_dataframe() -> None:
    model = build_check_files_model(_snapshot(), price_filename=None)
    assert "usable" not in (_snapshot())
    assert model.role_rows[0]["Role"] == "Offtake"
