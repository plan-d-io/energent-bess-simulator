"""Public candidate resolution for live Configure sizing."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from btm_sim.sweep import SweepCandidate, build_candidates

from ui.services.configure import (
    POWER_EXPLICIT,
    POWER_MANUAL,
    POWER_MODE_TO_CORE,
    POWER_SUGGESTED,
    empty_candidates,
    parse_explicit_pairs,
    resolved_duration_hours,
)
from ui.services.period_inspection import inspect_period_payloads, period_inspection_cache_key

_InspectFn = Callable[..., dict[str, Any]]
_BuilderFn = Callable[..., Any]


def _as_sweep_candidates(items: Sequence[Mapping[str, Any]]) -> list[SweepCandidate]:
    candidates: list[SweepCandidate] = []
    for item in items:
        candidates.append(
            SweepCandidate(
                candidate_id=str(item["candidate_id"]),
                power_kw=float(item["power_kw"]),
                usable_energy_kwh=float(item["usable_energy_kwh"]),
                duration_hours=float(item["duration_hours"]),
                exceeds_p95_daily_pv_surplus=bool(item.get("exceeds_p95_daily_pv_surplus", False)),
                exceeds_p95_daily_import=bool(item.get("exceeds_p95_daily_import", False)),
                source=str(item.get("source") or "automatic"),
            )
        )
    return candidates


def _site_p95(site: Mapping[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(site, Mapping):
        return None, None
    import_kwh = site.get("p95_daily_import_kwh")
    surplus_kwh = site.get("p95_daily_surplus_kwh")
    return (
        None if import_kwh is None else float(import_kwh),
        None if surplus_kwh is None else float(surplus_kwh),
    )


def _inspection_for_durations(
    state: Mapping[str, Any],
    durations: Sequence[float],
    *,
    inspect_fn: _InspectFn | None,
) -> dict[str, Any]:
    stored = state.get("period_inspection")
    stored_map = dict(stored) if isinstance(stored, Mapping) else {}
    site = stored_map.get("site_analysis") if isinstance(stored_map.get("site_analysis"), Mapping) else {}
    stored_hours = tuple(float(item) for item in (site.get("durations_hours") or ()))
    wanted = tuple(float(item) for item in durations)
    if stored_hours == wanted and stored_map.get("automatic_candidates") is not None:
        return stored_map
    payloads = tuple(state.get("upload_payloads") or ())
    if len(payloads) != 3:
        return stored_map
    fn = inspect_fn or inspect_period_payloads
    snapshot = fn(
        payloads,
        str(state.get("period_id") or ""),
        allow_unvalidated=bool(state.get("unvalidated_ack")),
        acknowledge_site_boundary=bool(state.get("site_boundary_ack")),
        signature=tuple(state.get("upload_signature") or ()),
        durations_hours=wanted,
    )
    return snapshot if isinstance(snapshot, dict) else stored_map


def resolve_live_candidates(
    state: Mapping[str, Any],
    *,
    inspect_fn: _InspectFn | None = None,
    builder: _BuilderFn | None = None,
) -> dict[str, Any]:
    configure = state.get("configure") if isinstance(state.get("configure"), Mapping) else {}
    sizing = dict(configure.get("sizing") or {})
    result = empty_candidates()
    durations, duration_error = resolved_duration_hours(sizing)
    if duration_error:
        result["error"] = duration_error
        return result
    result["durations_hours"] = list(durations)
    power_mode = str(sizing.get("power_mode") or POWER_SUGGESTED)
    result["mode"] = POWER_MODE_TO_CORE.get(power_mode)
    stored = state.get("period_inspection") if isinstance(state.get("period_inspection"), Mapping) else {}
    inspection = stored
    if power_mode == POWER_SUGGESTED:
        inspection = _inspection_for_durations(state, durations, inspect_fn=inspect_fn)
    site = inspection.get("site_analysis") if isinstance(inspection.get("site_analysis"), Mapping) else {}
    result["p995_import_kw"] = site.get("p995_import_kw")
    result["p995_surplus_kw"] = site.get("p995_surplus_kw")
    grid = site.get("power_grid_kw") or []
    result["power_range_kw"] = [float(item) for item in grid] if grid else None
    no_opportunity = bool(site.get("no_revenue_shifting_opportunity"))
    diagnostic = site.get("diagnostic")
    if power_mode == POWER_SUGGESTED and no_opportunity:
        result["suggested_blocked"] = True
        result["suggested_message"] = str(diagnostic or "No revenue-shifting opportunity in this period.")
        result["error"] = result["suggested_message"]
        return result
    automatic_items = list(inspection.get("automatic_candidates") or site.get("automatic_candidates") or [])
    import_kwh, surplus_kwh = _site_p95(site)
    kwargs: dict[str, Any] = {
        "mode": POWER_MODE_TO_CORE[power_mode],
        "durations_hours": durations,
        "automatic_candidates": _as_sweep_candidates(automatic_items) if power_mode == POWER_SUGGESTED else (),
        "site_p95_daily_import_kwh": import_kwh,
        "site_p95_daily_surplus_kwh": surplus_kwh,
        "no_revenue_shifting_opportunity": no_opportunity,
    }
    if power_mode == POWER_MANUAL:
        kwargs["min_power_kw"] = sizing.get("min_power_kw")
        kwargs["max_power_kw"] = sizing.get("max_power_kw")
        kwargs["power_increment_kw"] = sizing.get("power_increment_kw")
    if power_mode == POWER_EXPLICIT:
        pairs, parse_error = parse_explicit_pairs(str(sizing.get("explicit_text") or ""))
        if parse_error:
            result["error"] = parse_error
            return result
        kwargs["explicit_pairs"] = pairs
    fn = builder or build_candidates
    try:
        built = fn(**kwargs)
    except Exception as exc:
        message = str(exc).strip() or "The candidate list could not be built."
        result["error"] = message
        return result
    items = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in built.candidates]
    duplicates = [dict(item) for item in (built.removed_duplicates or ())]
    result.update(
        {
            "ok": True,
            "items": items,
            "removed_duplicates": duplicates,
            "count": len(items),
            "error": None,
            "mode": built.mode,
            "durations_hours": list(built.durations_hours),
        }
    )
    return result


def suggested_cache_key(
    signature: Sequence[Any],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
    durations_hours: Sequence[float],
    simulator_version: str | None = None,
) -> tuple[Any, ...]:
    return period_inspection_cache_key(
        signature,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
        simulator_version=simulator_version,
        durations_hours=tuple(float(item) for item in durations_hours),
    )
