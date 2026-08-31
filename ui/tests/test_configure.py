from __future__ import annotations

import json
from datetime import time as dt_time
from types import SimpleNamespace

import pytest

from ui.flow import (
    apply_analysis_mode,
    continue_to_step5,
    default_state,
)
from ui.services.configure import (
    MODE_ONE,
    MODE_SIZE,
    REASON_DURATION,
    REASON_PRICES,
    active_powers,
    apply_configure_fields,
    apply_split_power,
    configure_from_defaults,
    continue_reason,
    ensure_configure_initialized,
    estimated_capex_eur,
    freeze_configure_snapshot,
    restore_recommended_defaults,
    round_trip_percent,
    validate_one_battery,
    validate_shared,
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


def _ready_state() -> dict:
    state = default_state()
    state.update(
        {
            "step": 4,
            "max_step": 4,
            "period_id": "2024",
            "unvalidated_ack": True,
            "site_boundary_ack": False,
            "ingest_snapshot": {
                "ok": True,
                "error": None,
                "roles": {
                    "offtake": {"register": "Afname Actief"},
                    "injection": {"register": "Injectie Actief"},
                    "pv": {"register": "Productie Actief"},
                },
                "periods": [
                    {
                        "id": "2024",
                        "n_unvalidated": 0,
                        "complete_calendar_year": True,
                    }
                ],
            },
            "period_inspection": {
                "ok": True,
                "period_id": "2024",
                "selected_period": {"id": "2024", "n_unvalidated": 0, "complete_calendar_year": True},
                "site_analysis": {"n_intervals": 4, "power_grid_kw": [10.0, 20.0]},
            },
            "price_coverage": {
                "covered": True,
                "unavailable": False,
                "one_battery_unavailable": False,
            },
        }
    )
    return state


def test_init_once_preserves_edits_and_restore_reloads() -> None:
    state = _ready_state()
    defaults = _defaults()
    ensure_configure_initialized(state, defaults, price_covered=True)
    assert state["analysis_mode"] == MODE_ONE
    assert state["configure"]["one_battery"]["usable_kwh"] == 77.0
    apply_configure_fields(state, one_battery={"usable_kwh": 120.0}, shared={"cost_eur_per_kwh": 400.0})
    ensure_configure_initialized(state, defaults, price_covered=True)
    assert state["configure"]["one_battery"]["usable_kwh"] == 120.0
    restore_recommended_defaults(state, defaults)
    assert state["configure"]["one_battery"]["usable_kwh"] == 77.0
    assert state["configure"]["shared"]["cost_eur_per_kwh"] == 250.0
    json.dumps(state["configure"])


def test_mode_switch_preserves_both_branches_and_shared() -> None:
    state = _ready_state()
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    apply_configure_fields(
        state,
        one_battery={"usable_kwh": 120.0},
        sizing={"duration_1h": True},
        shared={"cost_eur_per_kwh": 400.0},
    )
    state["configure"]["snapshot"] = {"stale": True}
    state["review"] = {"x": 1}
    state["max_step"] = 5
    apply_analysis_mode(state, MODE_SIZE)
    assert state["configure"]["one_battery"]["usable_kwh"] == 120.0
    assert state["configure"]["sizing"]["duration_1h"] is True
    assert state["configure"]["shared"]["cost_eur_per_kwh"] == 400.0
    assert state["configure"]["snapshot"] is None
    assert "review" not in state
    assert state["max_step"] == 4


def test_split_power_active_values_do_not_leak_into_snapshot() -> None:
    one = {"usable_kwh": 100.0, "power_kw": 50.0, "split_power": False, "charge_kw": None, "discharge_kw": None}
    enabled = apply_split_power(one, True)
    assert enabled["split_power"] is True
    assert enabled["charge_kw"] == 50.0
    assert enabled["discharge_kw"] == 50.0
    enabled["charge_kw"] = 10.0
    enabled["discharge_kw"] = 20.0
    assert active_powers(enabled) == (10.0, 20.0)
    disabled = apply_split_power(enabled, False)
    assert disabled["split_power"] is False
    assert active_powers(disabled) == (50.0, 50.0)
    state = _ready_state()
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    apply_configure_fields(state, one_battery=disabled)
    state["configure"]["one_battery"]["charge_kw"] = 10.0
    state["configure"]["one_battery"]["discharge_kw"] = 20.0
    state["configure"]["one_battery"]["split_power"] = False
    snapshot = freeze_configure_snapshot(state)
    assert snapshot["one_battery"]["charge_kw"] == 50.0
    assert snapshot["one_battery"]["discharge_kw"] == 50.0
    assert snapshot["one_battery"]["soc_initial_kwh"] == 0.0
    json.dumps(snapshot)


def test_round_trip_and_capex_are_derived() -> None:
    shared = {"eta_charge": 0.9, "eta_discharge": 0.8, "cost_eur_per_kwh": 250.0}
    one = {"usable_kwh": 77.0}
    assert round_trip_percent(shared) == pytest.approx(72.0)
    assert estimated_capex_eur(one, shared) == 19250.0


def test_typed_classes_accept_and_reject() -> None:
    state = _ready_state()
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    shared = state["configure"]["shared"]
    one = state["configure"]["one_battery"]
    assert validate_shared(shared) is None
    assert validate_one_battery(one, shared) is None
    bad_shared = dict(shared)
    bad_shared["peak_start_local"] = "20:00"
    bad_shared["peak_end_local"] = "08:00"
    assert validate_shared(bad_shared) is not None
    bad_one = dict(one)
    bad_one["usable_kwh"] = -1.0
    assert validate_one_battery(bad_one, shared) is not None


def test_missing_prices_block_only_one_battery() -> None:
    state = _ready_state()
    state["price_coverage"] = {
        "covered": False,
        "unavailable": True,
        "one_battery_unavailable": True,
    }
    ensure_configure_initialized(state, _defaults(), price_covered=False)
    assert state["analysis_mode"] == MODE_SIZE
    state["analysis_mode"] = MODE_ONE
    assert continue_reason(state, demo=False, defaults_ok=True) == REASON_PRICES
    state["analysis_mode"] = MODE_SIZE
    state["configure"]["candidates"] = {
        "ok": True,
        "items": [{"candidate_id": "c001_10kW_20kWh", "power_kw": 10.0, "usable_energy_kwh": 20.0, "duration_hours": 2.0}],
        "error": None,
    }
    assert continue_reason(state, demo=False, defaults_ok=True) is None


def test_sizing_requires_a_duration() -> None:
    state = _ready_state()
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    state["analysis_mode"] = MODE_SIZE
    apply_configure_fields(
        state,
        sizing={"duration_1h": False, "duration_2h": False, "duration_4h": False, "duration_6h": False},
    )
    assert continue_reason(state, demo=False, defaults_ok=True) == REASON_DURATION


def test_material_change_clears_snapshot_and_step5() -> None:
    state = _ready_state()
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    state["configure"]["snapshot"] = freeze_configure_snapshot(state)
    continue_to_step5(state)
    state["review"] = {"x": 1}
    apply_configure_fields(state, shared={"cost_eur_per_kwh": 310.0})
    assert state["configure"]["snapshot"] is None
    assert "review" not in state
    assert state["max_step"] == 4


def test_session_configure_is_plain() -> None:
    state = _ready_state()
    ensure_configure_initialized(state, _defaults(), price_covered=True)
    json.dumps(state["configure"])
    snapshot = freeze_configure_snapshot(state)
    json.dumps(snapshot)
    dumped = json.dumps(state["configure"])
    assert "datetime" not in dumped
    assert "BatteryConfig" not in dumped
    assert isinstance(snapshot["shared"]["peak_start_local"], str)
    assert not isinstance(snapshot["shared"]["peak_start_local"], dt_time)


def test_configure_from_defaults_uses_injected_values_not_copied_fallbacks() -> None:
    configure = configure_from_defaults(_defaults())
    assert configure["one_battery"]["usable_kwh"] == 77.0
    assert configure["one_battery"]["power_kw"] == 33.0
    assert configure["shared"]["cost_eur_per_kwh"] == 250.0
    assert SimpleNamespace not in {type(configure), type(configure["shared"])}
