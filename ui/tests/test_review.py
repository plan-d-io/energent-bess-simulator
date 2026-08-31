from __future__ import annotations

import json

from ui.flow import (
    ROUTE_SAVED,
    apply_analysis_mode,
    continue_to_step5,
    default_state,
)
from ui.services.configure import (
    MODE_ONE,
    MODE_SIZE,
    REASON_PRICES,
    apply_configure_fields,
    ensure_configure_initialized,
    freeze_configure_snapshot,
    store_frozen_snapshot,
)
from ui.services.review import (
    ACTION_DEMO,
    ACTION_ONE,
    ACTION_SIZE,
    PARTIAL_ACK_LABEL,
    REASON_PARTIAL,
    REASON_STALE,
    SOLVER_HELP,
    apply_review_fields,
    build_review_model,
    ensure_review_initialized,
    requires_partial_period_ack,
    review_action_reason,
    snapshot_block_reason,
)


def _defaults() -> dict:
    return {
        "ok": True,
        "basename": "defaults.toml",
        "signature": "defaults.toml:abc",
        "battery": {
            "usable_energy_kwh": 77.0,
            "charge_power_kw": 33.0,
            "discharge_power_kw": 33.0,
            "charge_efficiency": 0.9,
            "discharge_efficiency": 0.8,
            "initial_charge_kwh": 0.0,
            "max_equivalent_full_cycles_per_year": 250.0,
        },
        "tariffs": {
            "customer_sale_eur_per_mwh": 111.0,
            "peak_export_eur_per_mwh": 44.0,
            "offpeak_export_eur_per_mwh": 22.0,
            "peak_start_local": "07:00",
            "peak_end_local": "19:00",
            "weekends_offpeak": True,
            "timezone": "Europe/Brussels",
        },
        "reporting": {
            "seasonal_plots": True,
            "winter_iso_week": 3,
            "spring_iso_week": 19,
            "summer_iso_week": 26,
            "autumn_iso_week": 41,
        },
        "economics": {"estimated_battery_cost_eur_per_kwh": 250.0},
        "sweep": {
            "evaluation_period_years": 8.0,
            "default_durations_hours": [2.0, 4.0],
            "revenue_capture_threshold_pct": 90.0,
        },
    }


def _period(*, complete: bool = True, n_unvalidated: int = 2) -> dict:
    return {
        "id": "2024",
        "kind": "full_calendar_year" if complete else "common_overlap",
        "label": "Calendar year 2024" if complete else "Partial 2024",
        "n_intervals": 100,
        "n_unvalidated": n_unvalidated,
        "complete_calendar_year": complete,
        "start_local": "2024-01-01T00:00:00+01:00",
        "end_local_exclusive": "2025-01-01T00:00:00+01:00" if complete else "2024-02-01T00:00:00+01:00",
    }


def ready_review_state(*, complete: bool = True, n_unvalidated: int = 2, demo: bool = False) -> dict:
    period = _period(complete=complete, n_unvalidated=n_unvalidated)
    state = default_state()
    state.update(
        {
            "step": 4,
            "max_step": 4,
            "site_name": "Ganda Cars" if demo else "Plant A",
            "period_id": "2024",
            "unvalidated_ack": n_unvalidated > 0,
            "site_boundary_ack": False,
            "upload_payloads": (("offtake.csv", b"a"), ("injection.csv", b"b"), ("pv.csv", b"c")),
            "ingest_snapshot": {
                "ok": True,
                "error": None,
                "roles": {
                    "offtake": {"register": "Afname Actief"},
                    "injection": {"register": "Injectie Actief"},
                    "pv": {"register": "Productie Actief"},
                },
                "periods": [period],
            },
            "period_inspection": {
                "ok": True,
                "period_id": "2024",
                "selected_period": period,
                "site_analysis": {"n_intervals": 4, "power_grid_kw": [10.0, 20.0]},
                "report": {
                    "unvalidated_policy": {
                        "dates": ["2024-10-02"],
                        "n_unvalidated_in_selected_period": n_unvalidated,
                    }
                },
                "fatal": [],
                "warnings": [],
            },
            "price_coverage": {
                "covered": True,
                "unavailable": False,
                "one_battery_unavailable": False,
                "source_basename": "da_prices_qh.parquet",
                "selected_row_count": 100,
            },
        }
    )
    if demo:
        state["data_route"] = ROUTE_SAVED
    return state


def _candidates() -> dict:
    return {
        "ok": True,
        "items": [
            {
                "candidate_id": "c001_10kW_20kWh",
                "power_kw": 10.0,
                "usable_energy_kwh": 20.0,
                "duration_hours": 2.0,
            },
            {
                "candidate_id": "c002_20kW_40kWh",
                "power_kw": 20.0,
                "usable_energy_kwh": 40.0,
                "duration_hours": 2.0,
            },
        ],
        "removed_duplicates": [],
        "error": None,
    }


def freeze_one(state: dict) -> dict:
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    if state.get("data_route") == ROUTE_SAVED:
        state["configure"]["source"] = "saved"
        state["configure"]["saved_identity"] = {"artifact": "compare", "period": "2024"}
        state["configure"]["defaults_signature"] = None
    store_frozen_snapshot(state)
    continue_to_step5(state)
    ensure_review_initialized(state)
    return state


def freeze_size(state: dict) -> dict:
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    apply_analysis_mode(state, MODE_SIZE)
    apply_configure_fields(
        state,
        sizing={"duration_2h": True, "duration_1h": False, "duration_4h": False, "duration_6h": False},
        candidates=_candidates(),
    )
    if state.get("data_route") == ROUTE_SAVED:
        state["configure"]["source"] = "saved"
        state["configure"]["saved_identity"] = {"artifact": "sweep", "period": "2024"}
        state["configure"]["defaults_signature"] = None
    store_frozen_snapshot(state)
    continue_to_step5(state)
    ensure_review_initialized(state)
    return state


def test_ready_one_battery_and_sizing_models() -> None:
    one = freeze_one(ready_review_state())
    model = build_review_model(one)
    assert model["ready"] is True
    assert model["action_reason"] is None
    display = model["display"]
    assert display["lead"] == "Confirm configuration before running the simulation."
    assert display["primary_label"] == ACTION_ONE
    assert [row[0] for row in display["cases"]] == [
        "No battery",
        "Rule-based control",
        "Self-consumption",
        "Peak reduction",
        "Revenue maximisation",
        "Dynamic injection tariff",
    ]
    assert display["cases"][1][1] == "Rule-based EMS approximation without foresight."
    assert display["summary"][3] == ["Analysis", "Single battery, multiple dispatch strategies"]
    assert "2 unvalidated quarter-hours acknowledged (2024-10-02)." in display["ack_records"]
    json.dumps(model)
    json.dumps(one["review"])

    size = freeze_size(ready_review_state())
    size_model = build_review_model(size)
    assert size_model["ready"] is True
    display_size = size_model["display"]
    assert display_size["primary_label"] == ACTION_SIZE
    assert display_size["summary"][3] == ["Dispatch strategy", "Revenue maximisation"]
    assert display_size["candidate_rows"][0]["Candidate"] == "c001_10kW_20kWh"
    assert display_size["partial_required"] is False
    json.dumps(size_model)


def test_missing_stale_wrong_mode_and_route_block() -> None:
    state = freeze_one(ready_review_state())
    state["configure"]["snapshot"] = None
    assert snapshot_block_reason(state) == REASON_STALE

    state = freeze_one(ready_review_state())
    state["analysis_mode"] = MODE_SIZE
    assert snapshot_block_reason(state) == REASON_STALE

    state = freeze_one(ready_review_state())
    state["data_route"] = ROUTE_SAVED
    assert snapshot_block_reason(state) == REASON_STALE

    state = freeze_one(ready_review_state())
    state["configure"]["one_battery"]["usable_kwh"] = 120.0
    assert freeze_configure_snapshot(state) != state["configure"]["snapshot"]
    assert snapshot_block_reason(state) == REASON_STALE


def test_solver_help_is_solver_neutral() -> None:
    assert "Gurobi" not in SOLVER_HELP
    assert "detailed solver messages" in SOLVER_HELP.lower()
    assert "does not change the result" in SOLVER_HELP


def test_init_is_idempotent_and_solver_defaults_off() -> None:
    state = freeze_one(ready_review_state())
    first = dict(state["review"])
    ensure_review_initialized(state)
    assert state["review"]["fingerprint"] == first["fingerprint"]
    assert state["review"]["detailed_solver_output"] is False
    apply_review_fields(state, detailed_solver_output=True)
    ensure_review_initialized(state)
    assert state["review"]["detailed_solver_output"] is True
    json.dumps(state["review"]["intent"])


def test_configure_change_clears_review() -> None:
    state = freeze_one(ready_review_state())
    apply_review_fields(state, detailed_solver_output=True)
    apply_configure_fields(state, shared={"cost_eur_per_kwh": 310.0})
    assert "review" not in state
    assert state["configure"]["snapshot"] is None
    assert state["max_step"] == 4


def test_partial_ack_only_for_live_sizing_on_partial_period() -> None:
    complete_size = freeze_size(ready_review_state(complete=True))
    assert requires_partial_period_ack(complete_size) is False
    assert snapshot_block_reason(complete_size) is None

    one_partial = freeze_one(ready_review_state(complete=False))
    assert requires_partial_period_ack(one_partial) is False
    assert snapshot_block_reason(one_partial) is None

    demo_partial = freeze_size(ready_review_state(complete=False, demo=True))
    assert requires_partial_period_ack(demo_partial) is False
    assert snapshot_block_reason(demo_partial) is None

    live_partial = freeze_size(ready_review_state(complete=False))
    assert requires_partial_period_ack(live_partial) is True
    assert snapshot_block_reason(live_partial) == REASON_PARTIAL
    assert PARTIAL_ACK_LABEL in build_review_model(live_partial)["display"]["partial_label"]
    apply_review_fields(live_partial, partial_period_ack=True)
    assert snapshot_block_reason(live_partial) is None
    assert review_action_reason(live_partial) is None


def test_missing_prices_block_one_battery_only() -> None:
    state = freeze_one(ready_review_state())
    state["price_coverage"] = {"covered": False, "unavailable": True, "one_battery_unavailable": True}
    # Coverage is part of freeze, so this also makes the snapshot stale.
    # Rebuild a matching snapshot that still lacks coverage.
    state["configure"]["snapshot"] = freeze_configure_snapshot(state)
    ensure_review_initialized(state)
    assert snapshot_block_reason(state) == REASON_PRICES

    size = freeze_size(ready_review_state())
    size["price_coverage"] = {"covered": False, "unavailable": True, "one_battery_unavailable": True}
    size["configure"]["snapshot"] = freeze_configure_snapshot(size)
    ensure_review_initialized(size)
    assert snapshot_block_reason(size) is None


def test_ack_records_are_not_editable_decisions() -> None:
    state = freeze_one(ready_review_state())
    display = build_review_model(state)["display"]
    assert all("checkbox" not in line.lower() for line in display["ack_records"])
    boundary = freeze_one(ready_review_state())
    boundary["site_boundary_ack"] = True
    boundary["period_inspection"]["warnings"] = [
        {
            "code": "NEGATIVE_LOAD",
            "details": {"count": 3, "total_negative_load_kwh": 1.5},
        }
    ]
    boundary["configure"]["snapshot"] = freeze_configure_snapshot(boundary)
    ensure_review_initialized(boundary)
    records = build_review_model(boundary)["display"]["ack_records"]
    assert "Meter-boundary condition acknowledged." in records
    assert "Affected quarter-hours: 3" not in records
    detail = build_review_model(boundary)["display"]["ack_boundary_detail"]
    assert "Affected quarter-hours: 3" in detail
    assert "Total energy difference: 1.5 kWh" in detail


def test_demo_review_is_readonly_and_uses_demo_action() -> None:
    state = freeze_one(ready_review_state(demo=True, n_unvalidated=96))
    model = build_review_model(state)
    assert model["ready"] is True
    assert model["display"]["demo"] is True
    assert model["display"]["primary_label"] == ACTION_DEMO
    assert model["display"]["partial_required"] is False
    assert "96 unvalidated quarter-hours acknowledged" in " ".join(model["display"]["ack_records"])
    assert model["intent"]["allow_unvalidated"] is True
    json.dumps(model["intent"])
