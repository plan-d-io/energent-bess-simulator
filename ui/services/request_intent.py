"""Map frozen Review intent to public request fields and compare serialized payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ui.services.configure import MODE_ONE, MODE_SIZE, parse_explicit_pairs
from ui.services.period_inspection import as_serialisable

_FLOAT_TOL = 1e-9


def _close(left: Any, right: Any) -> bool:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return left == right
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) <= _FLOAT_TOL * scale


def _hhmm(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def cli_overrides_from_intent(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Stable CLI mapping for the public request builders. Values only, no paths."""
    shared = dict(intent.get("shared") or {})
    cli: dict[str, Any] = {
        "eta_charge": float(shared["eta_charge"]),
        "eta_discharge": float(shared["eta_discharge"]),
        "soc_initial": 0.0,
        "max_equivalent_full_cycles_per_year": float(shared["max_efc_per_year"]),
        "customer_rate": float(shared["customer_sale_eur_per_mwh"]),
        "export_peak_rate": float(shared["peak_export_eur_per_mwh"]),
        "export_offpeak_rate": float(shared["offpeak_export_eur_per_mwh"]),
        "peak_start": _hhmm(shared["peak_start_local"]),
        "peak_end": _hhmm(shared["peak_end_local"]),
        "weekends_offpeak": bool(shared["weekends_offpeak"]),
        "timezone": str(shared.get("timezone") or "Europe/Brussels"),
        "seasonal_plots": bool(shared["seasonal_plots"]),
        "winter_iso_week": int(shared["winter_iso_week"]),
        "spring_iso_week": int(shared["spring_iso_week"]),
        "summer_iso_week": int(shared["summer_iso_week"]),
        "autumn_iso_week": int(shared["autumn_iso_week"]),
        "estimated_battery_cost_eur_per_kwh": float(shared["cost_eur_per_kwh"]),
    }
    mode = str(intent.get("analysis_mode") or MODE_ONE)
    if mode == MODE_ONE:
        one = dict(intent.get("one_battery") or {})
        cli["e_usable"] = float(one["usable_kwh"])
        cli["p_charge"] = float(one["charge_kw"])
        cli["p_discharge"] = float(one["discharge_kw"])
        return as_serialisable(cli)
    sizing = dict(intent.get("sizing") or {})
    cli["evaluation_period_years"] = float(sizing["evaluation_years"])
    cli["revenue_capture_threshold_pct"] = float(sizing["capture_pct"])
    cli["default_durations_hours"] = [float(item) for item in sizing.get("durations_hours") or []]
    return as_serialisable(cli)


def builder_kwargs_from_intent(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Keyword arguments the future execution phase will pass to the public builders."""
    kwargs: dict[str, Any] = {
        "period_id": str(intent.get("period_id") or ""),
        "site_label": str(intent.get("site_label") or ""),
        "allow_unvalidated": bool(intent.get("allow_unvalidated")),
        "acknowledge_site_boundary": bool(intent.get("acknowledge_site_boundary")),
        "detailed_solver_output": bool(intent.get("detailed_solver_output")),
        "cli": cli_overrides_from_intent(intent),
    }
    if str(intent.get("analysis_mode") or MODE_ONE) != MODE_SIZE:
        return kwargs
    sizing = dict(intent.get("sizing") or {})
    kwargs["mode"] = str(sizing.get("core_mode") or "automatic")
    kwargs["durations_hours"] = [float(item) for item in sizing.get("durations_hours") or []]
    core_mode = kwargs["mode"]
    if core_mode == "manual_range":
        kwargs["min_power_kw"] = float(sizing["min_power_kw"])
        kwargs["max_power_kw"] = float(sizing["max_power_kw"])
        kwargs["power_increment_kw"] = float(sizing["power_increment_kw"])
    if core_mode == "explicit":
        pairs, error = parse_explicit_pairs(str(sizing.get("explicit_text") or ""))
        if error:
            kwargs["explicit_pairs"] = []
        else:
            kwargs["explicit_pairs"] = pairs
    return kwargs


def _candidate_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(item.get("candidate_id") or ""),
        round(float(item.get("power_kw") or 0.0), 9),
        round(float(item.get("usable_energy_kwh") or item.get("e_usable_kwh") or 0.0), 9),
        round(float(item.get("duration_hours") or 0.0), 9),
    )


def _add(mismatches: list[str], label: str, ok: bool) -> None:
    if not ok:
        mismatches.append(label)


def mismatches_for_serialized_request(
    payload: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> list[str]:
    """Return specific mismatch labels. Empty means the serialized request matches the intent."""
    found: list[str] = []
    _add(found, "site label", str(payload.get("site_label") or "") == str(intent.get("site_label") or ""))
    _add(found, "period id", str(payload.get("period_id") or "") == str(intent.get("period_id") or ""))
    _add(
        found,
        "unvalidated flag",
        bool(payload.get("allow_unvalidated")) == bool(intent.get("allow_unvalidated")),
    )
    _add(
        found,
        "meter-boundary flag",
        bool(payload.get("acknowledge_site_boundary"))
        == bool(intent.get("acknowledge_site_boundary")),
    )
    _add(
        found,
        "detailed solver output",
        bool(payload.get("detailed_solver_output")) == bool(intent.get("detailed_solver_output")),
    )
    shared = dict(intent.get("shared") or {})
    battery = dict(payload.get("battery") or {})
    tariffs = dict(payload.get("tariffs") or {})
    reporting = dict(payload.get("reporting") or {})
    economics = dict(payload.get("economics") or {})
    mode = str(intent.get("analysis_mode") or MODE_ONE)
    if mode == MODE_ONE:
        one = dict(intent.get("one_battery") or {})
        _add(found, "usable capacity", _close(battery.get("e_usable_kwh"), one.get("usable_kwh")))
        _add(found, "charge power", _close(battery.get("p_charge_kw"), one.get("charge_kw")))
        _add(found, "discharge power", _close(battery.get("p_discharge_kw"), one.get("discharge_kw")))
        prices = dict(payload.get("prices") or {})
        expected = str(intent.get("price_dataset_basename") or "")
        actual = Path(str(prices.get("path") or "")).name
        _add(found, "standard price dataset", actual == expected and bool(expected))
    _add(found, "initial stored energy", _close(battery.get("soc_initial_kwh"), 0.0))
    _add(found, "charge efficiency", _close(battery.get("eta_charge"), shared.get("eta_charge")))
    _add(
        found,
        "discharge efficiency",
        _close(battery.get("eta_discharge"), shared.get("eta_discharge")),
    )
    _add(
        found,
        "cycle limit",
        _close(
            battery.get("max_equivalent_full_cycles_per_year"),
            shared.get("max_efc_per_year"),
        ),
    )
    _add(
        found,
        "customer PV-sale tariff",
        _close(tariffs.get("customer_sale_eur_per_mwh"), shared.get("customer_sale_eur_per_mwh")),
    )
    _add(
        found,
        "peak injection tariff",
        _close(tariffs.get("peak_export_eur_per_mwh"), shared.get("peak_export_eur_per_mwh")),
    )
    _add(
        found,
        "off-peak injection tariff",
        _close(tariffs.get("offpeak_export_eur_per_mwh"), shared.get("offpeak_export_eur_per_mwh")),
    )
    _add(found, "peak-period start", _hhmm(tariffs.get("peak_start_local")) == _hhmm(shared.get("peak_start_local")))
    _add(found, "peak-period end", _hhmm(tariffs.get("peak_end_local")) == _hhmm(shared.get("peak_end_local")))
    _add(
        found,
        "weekends off-peak",
        bool(tariffs.get("weekends_offpeak")) == bool(shared.get("weekends_offpeak")),
    )
    _add(
        found,
        "timezone",
        str(tariffs.get("timezone") or "") == str(shared.get("timezone") or "Europe/Brussels"),
    )
    _add(
        found,
        "seasonal plots",
        bool(reporting.get("seasonal_plots")) == bool(shared.get("seasonal_plots")),
    )
    for week in ("winter_iso_week", "spring_iso_week", "summer_iso_week", "autumn_iso_week"):
        _add(found, week.replace("_", " "), int(reporting.get(week) or 0) == int(shared.get(week) or 0))
    _add(
        found,
        "estimated battery cost",
        _close(
            economics.get("estimated_battery_cost_eur_per_kwh"),
            shared.get("cost_eur_per_kwh"),
        ),
    )
    if mode != MODE_SIZE:
        return found
    sizing = dict(intent.get("sizing") or {})
    sweep = dict(payload.get("sweep") or {})
    _add(found, "sizing mode", str(payload.get("mode") or "") == str(sizing.get("core_mode") or ""))
    intent_durations = [round(float(item), 9) for item in sizing.get("durations_hours") or []]
    payload_durations = [round(float(item), 9) for item in payload.get("durations_hours") or []]
    _add(found, "durations", intent_durations == payload_durations)
    _add(
        found,
        "evaluation period",
        _close(sweep.get("evaluation_period_years"), sizing.get("evaluation_years")),
    )
    _add(
        found,
        "revenue-capture threshold",
        _close(sweep.get("revenue_capture_threshold_pct"), sizing.get("capture_pct")),
    )
    intent_candidates = [_candidate_key(item) for item in sizing.get("candidates") or [] if isinstance(item, Mapping)]
    payload_candidates = [
        _candidate_key(item) for item in payload.get("candidates") or [] if isinstance(item, Mapping)
    ]
    _add(found, "candidate count", len(intent_candidates) == len(payload_candidates))
    _add(found, "candidate order", intent_candidates == payload_candidates)
    core_mode = str(sizing.get("core_mode") or "")
    manual = dict(payload.get("manual_range") or {})
    if core_mode == "manual_range":
        _add(found, "manual minimum power", _close(manual.get("min_power_kw"), sizing.get("min_power_kw")))
        _add(found, "manual maximum power", _close(manual.get("max_power_kw"), sizing.get("max_power_kw")))
        _add(
            found,
            "manual power increment",
            _close(manual.get("power_increment_kw"), sizing.get("power_increment_kw")),
        )
    if core_mode == "explicit":
        expected, _error = parse_explicit_pairs(str(sizing.get("explicit_text") or ""))
        actual = [tuple(float(part) for part in pair) for pair in payload.get("explicit_pairs") or []]
        _add(
            found,
            "explicit pairs",
            [(round(a, 9), round(b, 9)) for a, b in expected]
            == [(round(a, 9), round(b, 9)) for a, b in actual],
        )
    return found


def request_matches_intent(payload: Mapping[str, Any], intent: Mapping[str, Any]) -> bool:
    return not mismatches_for_serialized_request(payload, intent)


def ordered_candidate_mappings(items: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "power_kw": float(item.get("power_kw")),
                "usable_energy_kwh": float(item.get("usable_energy_kwh")),
                "duration_hours": float(item.get("duration_hours")),
            }
        )
    return rows
