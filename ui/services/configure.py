"""Serialisable Configure state, validation and frozen snapshots."""

from __future__ import annotations

import json
from datetime import time as dt_time
from typing import Any, Mapping, MutableMapping, Sequence

from btm_sim import BatteryConfig, TariffConfig
from btm_sim.config import EconomicsConfig, ReportingConfig, SweepConfig

from ui.flow import clear_step5_plus, displayed_site_name, is_saved_example
from ui.services.defaults import REASON_DEFAULTS
from ui.services.period import (
    discovery_allow_unvalidated,
    inspection_belongs_to_period,
    inspection_ok,
    is_complete_year,
    needs_meter_boundary_ack,
    period_by_id,
    snapshot_is_stale,
)
from ui.services.period_inspection import as_serialisable

MODE_ONE = "one-battery"
MODE_SIZE = "size"
POWER_SUGGESTED = "suggested"
POWER_MANUAL = "manual"
POWER_EXPLICIT = "explicit"
POWER_MODE_LABELS = (
    "Suggested from the site data",
    "Set the range manually",
    "Enter specific battery sizes",
)
POWER_LABEL_TO_MODE = {
    "Suggested from the site data": POWER_SUGGESTED,
    "Set the range manually": POWER_MANUAL,
    "Enter specific battery sizes": POWER_EXPLICIT,
}
POWER_MODE_TO_CORE = {
    POWER_SUGGESTED: "automatic",
    POWER_MANUAL: "manual_range",
    POWER_EXPLICIT: "explicit",
}

REASON_STALE = "Return to Simulation period and confirm the selected period."
REASON_PRICES = "Choose Find a battery size, or select a period covered by the price dataset."
REASON_SHARED = "Correct the tariff, cost or battery assumptions."
REASON_DURATION = "Select at least one battery duration."
REASON_CANDIDATES = "Resolve the battery-size list."
REASON_BRANCH = "Correct the invalid configuration values."
REASON_DEMO = "The saved demo is not available."
PRICE_UNAVAILABLE_TITLE = "Day-ahead prices do not cover this period"
PRICE_UNAVAILABLE_BODY = (
    "Choose Find a battery size, or select a period covered by the price dataset."
)
PARTIAL_PERIOD_NOTE = (
    "This is a partial calendar window. Battery sizing will be annualised later."
)
CAPEX_CAPTION = (
    "Simple payback uses this cost estimate. It is not profit, NPV or a complete business case."
)
SWEEP_COST_CAPTION = (
    "This is a screening period. It does not add financing, discounting, "
    "degradation, operating cost, tax, inflation or future tariff changes."
)
RESTORE_HELP = (
    "Reload starting values from the central defaults file. This does not change the file."
)
EXPLICIT_HELP = "One pair per line: power kW, usable energy kWh. Example: 50, 100"
CONFIGURE_LEAD = "Choose and configure the simulation run"
DEMO_READONLY = "Demo settings are read-only."

MODE_CARD_OPTIONS = (
    (
        MODE_ONE,
        "Evaluate one battery",
        "Simulate one battery size using all dispatch strategies.\n",
    ),
    (
        MODE_SIZE,
        "Find a battery size",
        "Compare a range of sizes using the revenue maximisation dispatch strategy.",
    ),
)


def default_analysis_mode(*, price_covered: bool) -> str:
    return MODE_ONE if price_covered else MODE_SIZE


def prices_cover_period(coverage: Mapping[str, Any] | None) -> bool:
    if not isinstance(coverage, Mapping):
        return False
    if coverage.get("unavailable") or coverage.get("one_battery_unavailable"):
        return False
    return bool(coverage.get("covered"))


def selected_period_record(state: Mapping[str, Any]) -> Mapping[str, Any] | None:
    period_id = state.get("period_id")
    inspection = state.get("period_inspection")
    if isinstance(inspection, Mapping):
        selected = inspection.get("selected_period")
        if isinstance(selected, Mapping) and str(selected.get("id") or "") == str(period_id or ""):
            return selected
    snapshot = state.get("ingest_snapshot")
    if isinstance(snapshot, Mapping) and period_id:
        found = period_by_id(snapshot, str(period_id))
        if isinstance(found, Mapping):
            return found
    return None


def configure_entry_reason(state: Mapping[str, Any], *, demo: bool) -> str | None:
    period_id = state.get("period_id")
    if not period_id:
        return REASON_STALE
    inspection = state.get("period_inspection")
    if not inspection_belongs_to_period(inspection, str(period_id)):
        return REASON_STALE
    if not inspection_ok(inspection):
        return REASON_STALE
    if not demo and snapshot_is_stale(state.get("ingest_snapshot")):
        return REASON_STALE
    selected = selected_period_record(state)
    if selected is None:
        return REASON_STALE
    if discovery_allow_unvalidated(selected) and not bool(state.get("unvalidated_ack")):
        return REASON_STALE
    if needs_meter_boundary_ack(inspection) and not bool(state.get("site_boundary_ack")):
        return REASON_STALE
    site = inspection.get("site_analysis") if isinstance(inspection, Mapping) else None
    if not isinstance(site, Mapping):
        return REASON_STALE
    return None


def _hours_set(values: Sequence[Any]) -> set[float]:
    return {round(float(item), 12) for item in values}


def duration_flags_from_hours(hours: Sequence[Any]) -> dict[str, Any]:
    selected = _hours_set(hours)
    custom = [
        float(item)
        for item in hours
        if round(float(item), 12) not in {1.0, 2.0, 4.0, 6.0}
    ]
    custom_text = ", ".join(_format_hours(item) for item in custom)
    return {
        "duration_1h": 1.0 in selected,
        "duration_2h": 2.0 in selected,
        "duration_4h": 4.0 in selected,
        "duration_6h": 6.0 in selected,
        "custom_hours_text": custom_text,
    }


def parse_custom_hours(text: str) -> tuple[tuple[float, ...], str | None]:
    raw = str(text or "").strip()
    if not raw:
        return (), None
    parts = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    values: list[float] = []
    for part in parts:
        try:
            number = float(part)
        except (TypeError, ValueError):
            return (), "Additional durations must be comma-separated positive hours."
        if not number > 0:
            return (), "Additional durations must be comma-separated positive hours."
        values.append(number)
    return tuple(values), None


def resolved_duration_hours(sizing: Mapping[str, Any]) -> tuple[tuple[float, ...], str | None]:
    selected: list[float] = []
    if sizing.get("duration_1h"):
        selected.append(1.0)
    if sizing.get("duration_2h"):
        selected.append(2.0)
    if sizing.get("duration_4h"):
        selected.append(4.0)
    if sizing.get("duration_6h"):
        selected.append(6.0)
    extra, error = parse_custom_hours(str(sizing.get("custom_hours_text") or ""))
    if error:
        return (), error
    selected.extend(extra)
    if not selected:
        return (), REASON_DURATION
    rounded = [round(item, 12) for item in selected]
    unique: list[float] = []
    seen: set[float] = set()
    for value, key in zip(selected, rounded):
        if key in seen:
            continue
        seen.add(key)
        unique.append(float(value))
    unique.sort()
    return tuple(unique), None


def _format_hours(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:g}"


def configure_from_defaults(defaults: Mapping[str, Any]) -> dict[str, Any]:
    battery = dict(defaults.get("battery") or {})
    tariffs = dict(defaults.get("tariffs") or {})
    reporting = dict(defaults.get("reporting") or {})
    economics = dict(defaults.get("economics") or {})
    sweep = dict(defaults.get("sweep") or {})
    charge_kw = float(battery["charge_power_kw"])
    discharge_kw = float(battery["discharge_power_kw"])
    combined = charge_kw if abs(charge_kw - discharge_kw) < 1e-12 else None
    split = combined is None
    durations = list(sweep.get("default_durations_hours") or ())
    shared = {
        "eta_charge": float(battery["charge_efficiency"]),
        "eta_discharge": float(battery["discharge_efficiency"]),
        "max_efc_per_year": float(battery["max_equivalent_full_cycles_per_year"]),
        "customer_sale_eur_per_mwh": float(tariffs["customer_sale_eur_per_mwh"]),
        "peak_export_eur_per_mwh": float(tariffs["peak_export_eur_per_mwh"]),
        "offpeak_export_eur_per_mwh": float(tariffs["offpeak_export_eur_per_mwh"]),
        "peak_start_local": str(tariffs["peak_start_local"]),
        "peak_end_local": str(tariffs["peak_end_local"]),
        "timezone": str(tariffs.get("timezone") or "Europe/Brussels"),
        "weekends_offpeak": bool(tariffs["weekends_offpeak"]),
        "seasonal_plots": bool(reporting["seasonal_plots"]),
        "winter_iso_week": int(reporting["winter_iso_week"]),
        "spring_iso_week": int(reporting["spring_iso_week"]),
        "summer_iso_week": int(reporting["summer_iso_week"]),
        "autumn_iso_week": int(reporting["autumn_iso_week"]),
        "cost_eur_per_kwh": float(economics["estimated_battery_cost_eur_per_kwh"]),
    }
    one_battery = {
        "usable_kwh": float(battery["usable_energy_kwh"]),
        "power_kw": float(combined if combined is not None else charge_kw),
        "split_power": split,
        "charge_kw": charge_kw,
        "discharge_kw": discharge_kw,
    }
    sizing = {
        **duration_flags_from_hours(durations),
        "power_mode": POWER_SUGGESTED,
        "min_power_kw": None,
        "max_power_kw": None,
        "power_increment_kw": None,
        "explicit_text": "",
        "evaluation_years": float(sweep["evaluation_period_years"]),
        "capture_pct": float(sweep["revenue_capture_threshold_pct"]),
    }
    return as_serialisable(
        {
            "source": "live",
            "defaults_basename": defaults.get("basename"),
            "defaults_signature": defaults.get("signature"),
            "saved_identity": None,
            "shared": shared,
            "one_battery": one_battery,
            "sizing": sizing,
            "candidates": empty_candidates(),
            "snapshot": None,
        }
    )


def configure_from_saved(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(context.get("configure") or {})
    payload["source"] = "saved"
    payload["snapshot"] = None
    if "candidates" not in payload:
        payload["candidates"] = empty_candidates()
    return as_serialisable(payload)


def empty_candidates() -> dict[str, Any]:
    return {
        "ok": False,
        "mode": None,
        "durations_hours": [],
        "items": [],
        "removed_duplicates": [],
        "count": 0,
        "error": None,
        "suggested_blocked": False,
        "suggested_message": None,
        "power_range_kw": None,
        "p995_import_kw": None,
        "p995_surplus_kw": None,
    }


def ensure_configure_initialized(
    state: MutableMapping[str, Any],
    defaults: Mapping[str, Any],
    *,
    price_covered: bool,
) -> dict[str, Any]:
    if not state.get("analysis_mode"):
        state["analysis_mode"] = default_analysis_mode(price_covered=price_covered)
    existing = state.get("configure")
    if isinstance(existing, dict) and existing.get("source") == "live":
        return dict(state)
    state["configure"] = configure_from_defaults(defaults)
    return dict(state)


def ensure_demo_configure(
    state: MutableMapping[str, Any],
    context: Mapping[str, Any],
    *,
    price_covered: bool,
) -> dict[str, Any]:
    if not state.get("analysis_mode"):
        state["analysis_mode"] = default_analysis_mode(price_covered=price_covered)
    existing = state.get("configure")
    if isinstance(existing, dict) and existing.get("source") == "saved":
        return dict(state)
    state["configure"] = configure_from_saved(context)
    return dict(state)


def restore_recommended_defaults(
    state: MutableMapping[str, Any],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    mode = state.get("analysis_mode")
    state["configure"] = configure_from_defaults(defaults)
    if mode in {MODE_ONE, MODE_SIZE}:
        state["analysis_mode"] = mode
    return clear_step5_plus(state)


def apply_configure_fields(
    state: MutableMapping[str, Any],
    *,
    shared: Mapping[str, Any] | None = None,
    one_battery: Mapping[str, Any] | None = None,
    sizing: Mapping[str, Any] | None = None,
    candidates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    configure = state.setdefault("configure", {})
    if not isinstance(configure, dict):
        configure = {}
        state["configure"] = configure
    changed = False
    if shared:
        target = configure.setdefault("shared", {})
        changed = _update_mapping(target, shared) or changed
    if one_battery:
        target = configure.setdefault("one_battery", {})
        changed = _update_mapping(target, one_battery) or changed
    if sizing:
        target = configure.setdefault("sizing", {})
        changed = _update_mapping(target, sizing) or changed
    if candidates is not None:
        previous = configure.get("candidates")
        configure["candidates"] = dict(candidates)
        changed = previous != configure["candidates"] or changed
    if changed:
        clear_step5_plus(state)
    return dict(state)


def _update_mapping(target: dict[str, Any], patch: Mapping[str, Any]) -> bool:
    changed = False
    for key, value in patch.items():
        if target.get(key) != value:
            target[key] = value
            changed = True
    return changed


def apply_split_power(one_battery: MutableMapping[str, Any], enabled: bool) -> dict[str, Any]:
    current = dict(one_battery)
    was_split = bool(current.get("split_power"))
    if enabled and not was_split:
        combined = float(current["power_kw"])
        if current.get("charge_kw") is None:
            current["charge_kw"] = combined
        if current.get("discharge_kw") is None:
            current["discharge_kw"] = combined
        current["split_power"] = True
    elif not enabled and was_split:
        current["split_power"] = False
    else:
        current["split_power"] = bool(enabled)
    return current


def active_powers(one_battery: Mapping[str, Any]) -> tuple[float, float]:
    if one_battery.get("split_power"):
        return float(one_battery["charge_kw"]), float(one_battery["discharge_kw"])
    power = float(one_battery["power_kw"])
    return power, power


def round_trip_percent(shared: Mapping[str, Any]) -> float:
    return float(shared["eta_charge"]) * float(shared["eta_discharge"]) * 100.0


def estimated_capex_eur(one_battery: Mapping[str, Any], shared: Mapping[str, Any]) -> float:
    return float(one_battery["usable_kwh"]) * float(shared["cost_eur_per_kwh"])


def format_eur(value: float) -> str:
    return f"EUR {value:,.0f}"


def format_percent(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def format_power_range(values: Sequence[Any] | None) -> str | None:
    if not values:
        return None
    numbers = [float(item) for item in values]
    return f"{_format_kw(min(numbers))}–{_format_kw(max(numbers))} kW"


def _format_kw(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:g}"


def parse_hhmm(text: str) -> dt_time:
    parts = str(text).strip().split(":")
    if len(parts) != 2:
        raise ValueError("expected HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("expected HH:MM")
    return dt_time(hour, minute)


def format_hhmm(value: dt_time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_explicit_pairs(text: str) -> tuple[list[tuple[float, float]], str | None]:
    pairs: list[tuple[float, float]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.replace(";", ",").split(",") if part.strip()]
        if len(parts) != 2:
            return [], "Each line must be power kW, usable energy kWh."
        try:
            pairs.append((float(parts[0]), float(parts[1])))
        except (TypeError, ValueError):
            return [], "Each line must be power kW, usable energy kWh."
    return pairs, None


def validate_shared(shared: Mapping[str, Any]) -> str | None:
    try:
        EconomicsConfig(estimated_battery_cost_eur_per_kwh=float(shared["cost_eur_per_kwh"]))
        ReportingConfig(
            seasonal_plots=bool(shared["seasonal_plots"]),
            winter_iso_week=int(shared["winter_iso_week"]),
            spring_iso_week=int(shared["spring_iso_week"]),
            summer_iso_week=int(shared["summer_iso_week"]),
            autumn_iso_week=int(shared["autumn_iso_week"]),
        )
        TariffConfig(
            customer_sale_eur_per_mwh=float(shared["customer_sale_eur_per_mwh"]),
            peak_export_eur_per_mwh=float(shared["peak_export_eur_per_mwh"]),
            offpeak_export_eur_per_mwh=float(shared["offpeak_export_eur_per_mwh"]),
            peak_start_local=parse_hhmm(str(shared["peak_start_local"])),
            peak_end_local=parse_hhmm(str(shared["peak_end_local"])),
            weekends_offpeak=bool(shared["weekends_offpeak"]),
            timezone=str(shared.get("timezone") or "Europe/Brussels"),
        )
    except Exception:
        return REASON_SHARED
    return None


def validate_one_battery(one_battery: Mapping[str, Any], shared: Mapping[str, Any]) -> str | None:
    charge_kw, discharge_kw = active_powers(one_battery)
    try:
        BatteryConfig(
            e_usable_kwh=float(one_battery["usable_kwh"]),
            p_charge_kw=charge_kw,
            p_discharge_kw=discharge_kw,
            eta_charge=float(shared["eta_charge"]),
            eta_discharge=float(shared["eta_discharge"]),
            soc_initial_kwh=0.0,
            max_equivalent_full_cycles_per_year=float(shared["max_efc_per_year"]),
        )
    except Exception:
        return REASON_BRANCH
    return None


def validate_sweep(shared: Mapping[str, Any], sizing: Mapping[str, Any], durations: Sequence[float]) -> str | None:
    try:
        SweepConfig(
            estimated_battery_cost_eur_per_kwh=float(shared["cost_eur_per_kwh"]),
            evaluation_period_years=float(sizing["evaluation_years"]),
            default_durations_hours=tuple(durations),
            revenue_capture_threshold_pct=float(sizing["capture_pct"]),
        )
    except Exception:
        return REASON_BRANCH
    return None


def continue_reason(
    state: Mapping[str, Any],
    *,
    demo: bool,
    defaults_ok: bool,
    saved_ok: bool = True,
) -> str | None:
    stale = configure_entry_reason(state, demo=demo)
    if stale:
        return stale
    if demo and not saved_ok:
        return REASON_DEMO
    if not demo and not defaults_ok:
        return REASON_DEFAULTS
    mode = state.get("analysis_mode")
    covered = prices_cover_period(state.get("price_coverage") if isinstance(state.get("price_coverage"), Mapping) else None)
    if mode == MODE_ONE and not covered:
        return REASON_PRICES
    configure = state.get("configure")
    if not isinstance(configure, Mapping):
        return REASON_BRANCH
    shared = configure.get("shared") or {}
    shared_error = validate_shared(shared)
    if shared_error:
        return shared_error
    if mode == MODE_ONE:
        return validate_one_battery(configure.get("one_battery") or {}, shared)
    sizing = configure.get("sizing") or {}
    durations, duration_error = resolved_duration_hours(sizing)
    if duration_error:
        return duration_error
    sweep_error = validate_sweep(shared, sizing, durations)
    if sweep_error:
        return sweep_error
    candidates = configure.get("candidates") or {}
    if not candidates.get("ok") or not candidates.get("items"):
        return str(candidates.get("error") or REASON_CANDIDATES)
    return None


def freeze_configure_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    configure = dict(state.get("configure") or {})
    shared = dict(configure.get("shared") or {})
    mode = str(state.get("analysis_mode") or MODE_ONE)
    one_battery = dict(configure.get("one_battery") or {})
    charge_kw, discharge_kw = active_powers(one_battery)
    active_one = {
        "usable_kwh": float(one_battery["usable_kwh"]),
        "split_power": bool(one_battery.get("split_power")),
        "charge_kw": charge_kw,
        "discharge_kw": discharge_kw,
        "soc_initial_kwh": 0.0,
    }
    if not active_one["split_power"]:
        active_one["power_kw"] = float(one_battery["power_kw"])
    sizing = dict(configure.get("sizing") or {})
    durations, _ = resolved_duration_hours(sizing)
    candidates = dict(configure.get("candidates") or {})
    coverage = state.get("price_coverage") if isinstance(state.get("price_coverage"), Mapping) else {}
    snapshot = {
        "analysis_mode": mode,
        "source": configure.get("source"),
        "defaults_basename": configure.get("defaults_basename"),
        "defaults_signature": configure.get("defaults_signature"),
        "saved_identity": configure.get("saved_identity"),
        "site_name": displayed_site_name(state),
        "period_id": state.get("period_id"),
        "unvalidated_ack": bool(state.get("unvalidated_ack")),
        "site_boundary_ack": bool(state.get("site_boundary_ack")),
        "price_coverage_available": prices_cover_period(coverage),
        "shared": shared,
        "one_battery": active_one if mode == MODE_ONE else None,
        "sizing": None,
        "demo": is_saved_example(state),
    }
    if mode == MODE_SIZE:
        snapshot["sizing"] = {
            "power_mode": sizing.get("power_mode"),
            "core_mode": POWER_MODE_TO_CORE.get(str(sizing.get("power_mode"))),
            "durations_hours": list(durations),
            "min_power_kw": sizing.get("min_power_kw"),
            "max_power_kw": sizing.get("max_power_kw"),
            "power_increment_kw": sizing.get("power_increment_kw"),
            "explicit_text": sizing.get("explicit_text") if sizing.get("power_mode") == POWER_EXPLICIT else None,
            "evaluation_years": sizing.get("evaluation_years"),
            "capture_pct": sizing.get("capture_pct"),
            "candidates": list(candidates.get("items") or []),
            "removed_duplicates": list(candidates.get("removed_duplicates") or []),
        }
    return as_serialisable(snapshot)


def store_frozen_snapshot(state: MutableMapping[str, Any]) -> dict[str, Any]:
    configure = state.setdefault("configure", {})
    configure["snapshot"] = freeze_configure_snapshot(state)
    json.dumps(configure["snapshot"])
    return dict(state)


def seed_manual_range_from_site(sizing: MutableMapping[str, Any], site: Mapping[str, Any] | None) -> None:
    if not isinstance(site, Mapping):
        return
    grid = site.get("power_grid_kw") or []
    if sizing.get("min_power_kw") is None and grid:
        sizing["min_power_kw"] = float(min(float(item) for item in grid))
    if sizing.get("max_power_kw") is None and grid:
        sizing["max_power_kw"] = float(max(float(item) for item in grid))
    if sizing.get("power_increment_kw") is None and site.get("power_step_kw") is not None:
        sizing["power_increment_kw"] = float(site["power_step_kw"])


def period_is_partial(state: Mapping[str, Any]) -> bool:
    return not is_complete_year(selected_period_record(state))


def assert_plain(value: Any) -> None:
    json.dumps(value)
