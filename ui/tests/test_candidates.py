from __future__ import annotations

from types import SimpleNamespace

from ui.flow import default_state
from ui.services.candidates import resolve_live_candidates
from ui.services.configure import (
    POWER_EXPLICIT,
    POWER_MANUAL,
    configure_from_defaults,
    resolved_duration_hours,
)


def _defaults() -> dict:
    return {
        "basename": "defaults.toml",
        "signature": "defaults.toml:abc",
        "battery": {
            "usable_energy_kwh": 100.0,
            "charge_power_kw": 50.0,
            "discharge_power_kw": 50.0,
            "charge_efficiency": 0.95,
            "discharge_efficiency": 0.95,
            "initial_charge_kwh": 0.0,
            "max_equivalent_full_cycles_per_year": 400.0,
        },
        "tariffs": {
            "customer_sale_eur_per_mwh": 130.0,
            "peak_export_eur_per_mwh": 60.0,
            "offpeak_export_eur_per_mwh": 30.0,
            "peak_start_local": "08:00",
            "peak_end_local": "20:00",
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
        "economics": {"estimated_battery_cost_eur_per_kwh": 300.0},
        "sweep": {
            "evaluation_period_years": 10.0,
            "default_durations_hours": [2.0, 4.0],
            "revenue_capture_threshold_pct": 95.0,
        },
    }


def _candidate(candidate_id: str, power: float, energy: float, hours: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "power_kw": power,
        "usable_energy_kwh": energy,
        "duration_hours": hours,
        "exceeds_p95_daily_pv_surplus": False,
        "exceeds_p95_daily_import": False,
        "source": "automatic",
    }


def _state() -> dict:
    state = default_state()
    state["period_id"] = "2024"
    state["unvalidated_ack"] = True
    state["site_boundary_ack"] = False
    state["upload_signature"] = (("a.csv", 1, "a"), ("b.csv", 1, "b"), ("c.csv", 1, "c"))
    state["upload_payloads"] = (("a.csv", b"a"), ("b.csv", b"b"), ("c.csv", b"c"))
    state["configure"] = configure_from_defaults(_defaults())
    state["period_inspection"] = {
        "ok": True,
        "period_id": "2024",
        "site_analysis": {
            "n_intervals": 4,
            "durations_hours": [2.0, 4.0],
            "power_grid_kw": [10.0, 20.0, 50.0],
            "p995_import_kw": 12.0,
            "p995_surplus_kw": 18.0,
            "p95_daily_import_kwh": 40.0,
            "p95_daily_surplus_kwh": 80.0,
            "no_revenue_shifting_opportunity": False,
            "power_step_kw": 10.0,
        },
        "automatic_candidates": [
            _candidate("c001_10kW_20kWh", 10.0, 20.0, 2.0),
            _candidate("c002_10kW_40kWh", 10.0, 40.0, 4.0),
        ],
    }
    return state


def test_main_and_advanced_durations_resolve_unique_tuple() -> None:
    sizing = {
        "duration_1h": True,
        "duration_2h": True,
        "duration_4h": True,
        "duration_6h": False,
        "custom_hours_text": "2, 8",
    }
    hours, error = resolved_duration_hours(sizing)
    assert error is None
    assert hours == (1.0, 2.0, 4.0, 8.0)


def test_suggested_uses_inspection_and_does_not_invent_range() -> None:
    state = _state()
    inspect_calls: list[tuple] = []

    def inspect_fn(*args, **kwargs):
        inspect_calls.append((args, kwargs))
        raise AssertionError("should reuse stored 2h/4h inspection")

    built_kwargs: list[dict] = []

    def builder(**kwargs):
        built_kwargs.append(kwargs)
        return SimpleNamespace(
            candidates=[SimpleNamespace(to_dict=lambda: _candidate("c001_10kW_20kWh", 10.0, 20.0, 2.0))],
            mode="automatic",
            removed_duplicates=(),
            durations_hours=(2.0, 4.0),
        )

    result = resolve_live_candidates(state, inspect_fn=inspect_fn, builder=builder)
    assert inspect_calls == []
    assert result["ok"] is True
    assert result["power_range_kw"] == [10.0, 20.0, 50.0]
    assert result["p995_import_kw"] == 12.0
    assert result["items"][0]["candidate_id"] == "c001_10kW_20kWh"
    assert built_kwargs[0]["mode"] == "automatic"


def test_changed_durations_refresh_inspection() -> None:
    state = _state()
    state["configure"]["sizing"]["duration_4h"] = False
    calls: list[tuple] = []

    def inspect_fn(payloads, period_id, **kwargs):
        calls.append(tuple(kwargs.get("durations_hours") or ()))
        return {
            "ok": True,
            "period_id": period_id,
            "site_analysis": {
                "durations_hours": [2.0],
                "power_grid_kw": [10.0, 20.0],
                "p995_import_kw": 12.0,
                "p995_surplus_kw": 18.0,
                "no_revenue_shifting_opportunity": False,
            },
            "automatic_candidates": [_candidate("c001_10kW_20kWh", 10.0, 20.0, 2.0)],
        }

    def builder(**kwargs):
        return SimpleNamespace(
            candidates=[SimpleNamespace(to_dict=lambda item=_candidate("c001_10kW_20kWh", 10.0, 20.0, 2.0): item)],
            mode="automatic",
            removed_duplicates=(),
            durations_hours=(2.0,),
        )

    result = resolve_live_candidates(state, inspect_fn=inspect_fn, builder=builder)
    assert calls == [(2.0,)]
    assert result["ok"] is True


def test_manual_and_explicit_delegate_to_builder() -> None:
    state = _state()
    state["configure"]["sizing"]["power_mode"] = POWER_MANUAL
    state["configure"]["sizing"]["min_power_kw"] = 10.0
    state["configure"]["sizing"]["max_power_kw"] = 20.0
    state["configure"]["sizing"]["power_increment_kw"] = 10.0
    seen: list[str] = []

    def builder(**kwargs):
        seen.append(kwargs["mode"])
        return SimpleNamespace(
            candidates=[SimpleNamespace(to_dict=lambda: _candidate("c001_10kW_20kWh", 10.0, 20.0, 2.0))],
            mode=kwargs["mode"],
            removed_duplicates=({"power_kw": 10.0, "usable_energy_kwh": 20.0},),
            durations_hours=kwargs["durations_hours"],
        )

    manual = resolve_live_candidates(state, builder=builder)
    assert seen == ["manual_range"]
    assert manual["removed_duplicates"][0]["power_kw"] == 10.0
    state["configure"]["sizing"]["power_mode"] = POWER_EXPLICIT
    state["configure"]["sizing"]["explicit_text"] = "50, 100\n50, 100\n75, 150"
    explicit = resolve_live_candidates(state, builder=builder)
    assert seen[-1] == "explicit"
    assert explicit["ok"] is True


def test_core_error_becomes_concise_ui_state() -> None:
    state = _state()
    state["configure"]["sizing"]["power_mode"] = POWER_MANUAL
    state["configure"]["sizing"]["min_power_kw"] = 50.0
    state["configure"]["sizing"]["max_power_kw"] = 10.0
    state["configure"]["sizing"]["power_increment_kw"] = 10.0

    def builder(**_kwargs):
        raise ValueError("Maximum power must be greater than minimum power")

    result = resolve_live_candidates(state, builder=builder)
    assert result["ok"] is False
    assert "Maximum power" in result["error"]
    assert result["items"] == []


def test_no_opportunity_blocks_only_suggested() -> None:
    state = _state()
    state["period_inspection"]["site_analysis"]["no_revenue_shifting_opportunity"] = True
    state["period_inspection"]["site_analysis"]["diagnostic"] = "No import and surplus overlap."
    calls = {"n": 0}

    def builder(**_kwargs):
        calls["n"] += 1
        raise AssertionError("suggested should not build")

    suggested = resolve_live_candidates(state, builder=builder)
    assert suggested["suggested_blocked"] is True
    assert calls["n"] == 0
    state["configure"]["sizing"]["power_mode"] = POWER_MANUAL
    state["configure"]["sizing"]["min_power_kw"] = 10.0
    state["configure"]["sizing"]["max_power_kw"] = 20.0
    state["configure"]["sizing"]["power_increment_kw"] = 10.0

    def manual_builder(**kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            candidates=[SimpleNamespace(to_dict=lambda: _candidate("c001_10kW_20kWh", 10.0, 20.0, 2.0))],
            mode=kwargs["mode"],
            removed_duplicates=(),
            durations_hours=kwargs["durations_hours"],
        )

    manual = resolve_live_candidates(state, builder=manual_builder)
    assert manual["ok"] is True
    assert calls["n"] == 1
