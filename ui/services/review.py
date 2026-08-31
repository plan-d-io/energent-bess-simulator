"""Serialisable Review model, stale-state rules and display mapping."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, MutableMapping

from btm_sim.market import standard_day_ahead_prices_path

from ui.flow import displayed_site_name, is_saved_example
from ui.services.configure import (
    CAPEX_CAPTION,
    MODE_ONE,
    MODE_SIZE,
    POWER_MANUAL,
    POWER_MODE_LABELS,
    POWER_SUGGESTED,
    POWER_EXPLICIT,
    REASON_CANDIDATES,
    REASON_PRICES,
    SWEEP_COST_CAPTION,
    configure_entry_reason,
    estimated_capex_eur,
    format_eur,
    format_percent,
    format_power_range,
    freeze_configure_snapshot,
    prices_cover_period,
    round_trip_percent,
    selected_period_record,
)
from ui.services.period import (
    is_complete_year,
    meter_boundary_facts,
    unvalidated_dates_from_inspection,
)
from ui.services.period_inspection import as_serialisable
from ui.services.request_intent import cli_overrides_from_intent, ordered_candidate_mappings
from ui.services.uploads import ROLE_LABELS, ROLE_ORDER, format_row_count

REASON_STALE = "Return to Configure options and confirm the simulation settings."
REASON_UPLOADS = "Return to Upload data and provide three Fluvius CSV files."
REASON_DEFAULTS = (
    "The central defaults have changed. Return to Configure options and confirm the simulation settings."
)
REASON_DEMO_IDENTITY = "The saved Ganda Cars example is not available."
REASON_PARTIAL = (
    "Acknowledge that results for this partial period will be annualised for the sizing estimate."
)
PARTIAL_ACK_LABEL = (
    "I understand that results for this partial period will be annualised for the sizing estimate."
)
PARTIAL_WARNING = "This is a partial calendar window. Battery sizing results will be annualised."
DEMO_NOTE = "The stored result will be opened and no simulation will run."
SOLVER_CHECKBOX = "Show detailed solver output in the run log"
SOLVER_HELP = (
    "Adds detailed solver messages to the run log for diagnosis. "
    "It does not change the result."
)
CASES_CAPTION = (
    "The optimised cases use the complete selected period in advance. "
    "They are best-case results, not operating forecasts."
)
ONE_LEAD = "Confirm configuration before running the simulation."
SIZE_LEAD = "Confirm the inputs before running the battery-size comparison."
ACTION_ONE = "Run simulation"
ACTION_SIZE = "Run battery-size comparison"
ACTION_DEMO = "View saved demonstration results"
STALE_TITLE = "The frozen settings are no longer current."
STALE_BODY = "Return to Configure options and confirm the simulation settings."

COMPARISON_CASES = (
    (
        "No battery",
        "Baseline for comparison, constructed from measured consumption, PV production and grid exchange.",
    ),
    (
        "Rule-based control",
        "Rule-based EMS approximation without foresight.",
    ),
    (
        "Self-consumption",
        "Maximise useful PV energy supplied to the customer instead of injecting it into the grid. Uses perfect foresight.",
    ),
    (
        "Peak reduction",
        "Minimise the highest 15-minute grid import in each calendar month, with self-consumption as secondary objective. Uses perfect foresight.",
    ),
    (
        "Revenue maximisation",
        "Maximise Energent revenue from customer PV sales and grid injection under peak/off-peak tariff regime. Uses perfect foresight, never charges from the grid.",
    ),
    (
        "Dynamic injection tariff",
        "Prioritise self-consumption, then use remaining battery flexibility for favourable day-ahead injection. Uses perfect foresight, never charges from the grid.",
    ),
)

_POWER_LABEL = {
    POWER_SUGGESTED: POWER_MODE_LABELS[0],
    POWER_MANUAL: POWER_MODE_LABELS[1],
    POWER_EXPLICIT: POWER_MODE_LABELS[2],
}


def _price_basename() -> str:
    try:
        return standard_day_ahead_prices_path().name
    except Exception:
        return "da_prices_qh.parquet"


def snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(as_serialisable(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stored_snapshot(state: Mapping[str, Any]) -> dict[str, Any] | None:
    configure = state.get("configure")
    if not isinstance(configure, Mapping):
        return None
    snapshot = configure.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    return dict(snapshot)


def _inspection(state: Mapping[str, Any]) -> Mapping[str, Any] | None:
    inspection = state.get("period_inspection")
    return inspection if isinstance(inspection, Mapping) else None


def _coverage(state: Mapping[str, Any]) -> Mapping[str, Any] | None:
    coverage = state.get("price_coverage")
    return coverage if isinstance(coverage, Mapping) else None


def requires_partial_period_ack(state: Mapping[str, Any], snapshot: Mapping[str, Any] | None = None) -> bool:
    payload = snapshot if snapshot is not None else stored_snapshot(state)
    if not isinstance(payload, Mapping):
        return False
    if is_saved_example(state) or payload.get("demo"):
        return False
    if str(payload.get("analysis_mode") or "") != MODE_SIZE:
        return False
    return not is_complete_year(selected_period_record(state))


def _live_uploads_ok(state: Mapping[str, Any]) -> bool:
    if is_saved_example(state):
        return True
    payloads = state.get("upload_payloads") or ()
    return len(tuple(payloads)) == 3


def _defaults_identity_ok(state: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
    if is_saved_example(state) or snapshot.get("demo"):
        return True
    configure = state.get("configure") if isinstance(state.get("configure"), Mapping) else {}
    signature = snapshot.get("defaults_signature")
    current = configure.get("defaults_signature") if isinstance(configure, Mapping) else None
    return bool(signature) and signature == current


def _demo_identity_ok(state: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
    if not is_saved_example(state) and not snapshot.get("demo"):
        return True
    configure = state.get("configure") if isinstance(state.get("configure"), Mapping) else {}
    identity = snapshot.get("saved_identity")
    current = configure.get("saved_identity") if isinstance(configure, Mapping) else None
    return bool(identity) and identity == current


def _sizing_ready(snapshot: Mapping[str, Any]) -> bool:
    sizing = snapshot.get("sizing") if isinstance(snapshot.get("sizing"), Mapping) else {}
    candidates = list(sizing.get("candidates") or [])
    durations = list(sizing.get("durations_hours") or [])
    return bool(candidates) and bool(durations)


def snapshot_block_reason(state: Mapping[str, Any]) -> str | None:
    """Highest-priority real readiness reason, excluding the execution boundary."""
    snapshot = stored_snapshot(state)
    if snapshot is None:
        return REASON_STALE
    expected_demo = is_saved_example(state)
    expected_source = "saved" if expected_demo else "live"
    if str(snapshot.get("analysis_mode") or "") != str(state.get("analysis_mode") or ""):
        return REASON_STALE
    if bool(snapshot.get("demo")) != expected_demo:
        return REASON_STALE
    if str(snapshot.get("source") or "") != expected_source:
        return REASON_STALE
    if str(snapshot.get("site_name") or "") != displayed_site_name(state):
        return REASON_STALE
    if str(snapshot.get("period_id") or "") != str(state.get("period_id") or ""):
        return REASON_STALE
    if configure_entry_reason(state, demo=expected_demo):
        return REASON_STALE
    try:
        fresh = freeze_configure_snapshot(state)
    except Exception:
        return REASON_STALE
    if as_serialisable(fresh) != as_serialisable(snapshot):
        return REASON_STALE
    if not _live_uploads_ok(state):
        return REASON_UPLOADS
    if not _defaults_identity_ok(state, snapshot):
        return REASON_DEFAULTS
    if not _demo_identity_ok(state, snapshot):
        return REASON_DEMO_IDENTITY
    mode = str(snapshot.get("analysis_mode") or MODE_ONE)
    if mode == MODE_ONE and not prices_cover_period(_coverage(state)):
        return REASON_PRICES
    if mode == MODE_SIZE and not _sizing_ready(snapshot):
        return REASON_CANDIDATES
    if requires_partial_period_ack(state, snapshot):
        review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
        if not bool(review.get("partial_period_ack")):
            return REASON_PARTIAL
    return None


def review_action_reason(state: Mapping[str, Any]) -> str | None:
    return snapshot_block_reason(state)


def review_is_ready(state: Mapping[str, Any]) -> bool:
    return snapshot_block_reason(state) is None


def snapshot_is_stale(state: Mapping[str, Any]) -> bool:
    return snapshot_block_reason(state) == REASON_STALE or stored_snapshot(state) is None


def default_review_state(fingerprint: str) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "detailed_solver_output": False,
        "partial_period_ack": False,
        "intent": None,
    }


def ensure_review_initialized(state: MutableMapping[str, Any]) -> dict[str, Any]:
    snapshot = stored_snapshot(state)
    if snapshot is None:
        state.pop("review", None)
        return dict(state)
    digest = snapshot_fingerprint(snapshot)
    current = state.get("review")
    if isinstance(current, Mapping) and str(current.get("fingerprint") or "") == digest:
        return dict(state)
    state["review"] = default_review_state(digest)
    state["review"]["intent"] = build_request_intent(state)
    json.dumps(state["review"])
    return dict(state)


def apply_review_fields(
    state: MutableMapping[str, Any],
    *,
    detailed_solver_output: bool | None = None,
    partial_period_ack: bool | None = None,
) -> dict[str, Any]:
    ensure_review_initialized(state)
    review = state.setdefault("review", default_review_state(""))
    if detailed_solver_output is not None:
        review["detailed_solver_output"] = bool(detailed_solver_output)
    if partial_period_ack is not None:
        review["partial_period_ack"] = bool(partial_period_ack)
    if stored_snapshot(state) is not None:
        review["intent"] = build_request_intent(state)
    json.dumps(review)
    return dict(state)


def _durations_label(hours: list[Any]) -> str:
    parts = []
    for item in hours:
        number = float(item)
        parts.append(f"{number:g} h")
    return ", ".join(parts)


def _battery_summary(one: Mapping[str, Any]) -> str:
    usable = f"{float(one['usable_kwh']):g} kWh"
    charge = float(one["charge_kw"])
    discharge = float(one["discharge_kw"])
    if abs(charge - discharge) < 1e-9:
        return f"{usable} · {charge:g} kW"
    return f"{usable} · {charge:g} / {discharge:g} kW"


def _roles_line(state: Mapping[str, Any]) -> str:
    ingest = state.get("ingest_snapshot") if isinstance(state.get("ingest_snapshot"), Mapping) else {}
    roles = ingest.get("roles") if isinstance(ingest, Mapping) else None
    labels: list[str] = []
    if isinstance(roles, Mapping):
        for key in ROLE_ORDER:
            item = roles.get(key)
            if isinstance(item, Mapping) or item:
                labels.append(ROLE_LABELS.get(key, key))
    if not labels:
        labels = [ROLE_LABELS[key] for key in ROLE_ORDER]
    return " · ".join(labels)


def _period_kind_line(period: Mapping[str, Any] | None, snapshot: Mapping[str, Any]) -> str:
    label = str((period or {}).get("label") or snapshot.get("period_id") or "")
    if is_complete_year(period):
        kind = "complete calendar year"
    else:
        kind = "partial calendar period"
    if label:
        return f"{label} · {kind}"
    return kind


def _ack_records(state: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    period = selected_period_record(state)
    inspection = _inspection(state)
    if snapshot.get("unvalidated_ack"):
        count = int((period or {}).get("n_unvalidated") or 0)
        dates = unvalidated_dates_from_inspection(period, inspection)
        date_text = ", ".join(dates) if dates else ""
        if date_text:
            lines.append(
                f"{format_row_count(count)} unvalidated quarter-hours acknowledged ({date_text})."
            )
        else:
            lines.append(f"{format_row_count(count)} unvalidated quarter-hours acknowledged.")
    if snapshot.get("site_boundary_ack"):
        lines.append("Meter-boundary condition acknowledged.")
    return lines


def _ack_boundary_detail(state: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    if not snapshot.get("site_boundary_ack"):
        return ()
    return meter_boundary_facts(_inspection(state))


def build_request_intent(state: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = stored_snapshot(state) or {}
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    shared = dict(snapshot.get("shared") or {})
    intent: dict[str, Any] = {
        "analysis_mode": snapshot.get("analysis_mode"),
        "site_label": snapshot.get("site_name"),
        "period_id": snapshot.get("period_id"),
        "allow_unvalidated": bool(snapshot.get("unvalidated_ack")),
        "acknowledge_site_boundary": bool(snapshot.get("site_boundary_ack")),
        "detailed_solver_output": bool(review.get("detailed_solver_output")),
        "shared": shared,
        "one_battery": snapshot.get("one_battery"),
        "sizing": None,
        "price_dataset_basename": None,
    }
    if str(snapshot.get("analysis_mode") or MODE_ONE) == MODE_ONE:
        intent["price_dataset_basename"] = _price_basename()
    else:
        sizing = dict(snapshot.get("sizing") or {})
        intent["sizing"] = {
            **sizing,
            "candidates": ordered_candidate_mappings(sizing.get("candidates") or []),
        }
    intent["cli"] = cli_overrides_from_intent(intent)
    return as_serialisable(intent)


def build_display_model(state: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = stored_snapshot(state) or {}
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    demo = is_saved_example(state)
    mode = str(snapshot.get("analysis_mode") or MODE_ONE)
    period = selected_period_record(state) or {}
    coverage = _coverage(state) or {}
    shared = dict(snapshot.get("shared") or {})
    one = dict(snapshot.get("one_battery") or {})
    sizing = dict(snapshot.get("sizing") or {})
    candidates = ordered_candidate_mappings(sizing.get("candidates") or [])
    powers = [item["power_kw"] for item in candidates]
    if mode == MODE_ONE:
        summary = [
            ("Site", str(snapshot.get("site_name") or displayed_site_name(state))),
            ("Period", str(period.get("label") or snapshot.get("period_id") or "")),
            ("Battery", _battery_summary(one) if one else "—"),
            ("Analysis", "Single battery, multiple dispatch strategies"),
        ]
        primary = ACTION_DEMO if demo else ACTION_ONE
        lead = ONE_LEAD
    else:
        summary = [
            ("Site", str(snapshot.get("site_name") or displayed_site_name(state))),
            ("Period", str(period.get("label") or snapshot.get("period_id") or "")),
            ("Battery sizes", format_row_count(len(candidates))),
            ("Dispatch strategy", "Revenue maximisation"),
        ]
        primary = ACTION_DEMO if demo else ACTION_SIZE
        lead = SIZE_LEAD
    round_trip = format_percent(round_trip_percent(shared)) if shared else "—"
    model: dict[str, Any] = {
        "lead": lead,
        "demo": demo,
        "demo_note": DEMO_NOTE if demo else None,
        "mode": mode,
        "primary_label": primary,
        "summary": summary,
        "cases": list(COMPARISON_CASES) if mode == MODE_ONE else [],
        "cases_caption": CASES_CAPTION if mode == MODE_ONE else None,
        "battery_rows": [],
        "candidate_summary": None,
        "candidate_rows": [],
        "candidate_note": None,
        "screening_rows": [],
        "partial_required": requires_partial_period_ack(state, snapshot),
        "partial_ack": bool(review.get("partial_period_ack")),
        "partial_label": PARTIAL_ACK_LABEL,
        "partial_warning": PARTIAL_WARNING,
        "revenue_rows": [
            ("Customer PV-sale tariff", f"{float(shared.get('customer_sale_eur_per_mwh') or 0):g} EUR/MWh"),
            ("Peak injection tariff", f"{float(shared.get('peak_export_eur_per_mwh') or 0):g} EUR/MWh"),
            ("Off-peak injection tariff", f"{float(shared.get('offpeak_export_eur_per_mwh') or 0):g} EUR/MWh"),
            ("Peak-period start (local time)", str(shared.get("peak_start_local") or "")),
            ("Peak-period end (local time)", str(shared.get("peak_end_local") or "")),
            ("Weekends off-peak", "Yes" if shared.get("weekends_offpeak") else "No"),
            ("Timezone", str(shared.get("timezone") or "Europe/Brussels")),
        ],
        "data_rows": [
            ("Period", _period_kind_line(period, snapshot)),
            ("Quarter-hours", format_row_count(int(period.get("n_intervals") or 0))),
            ("Source", "Demo mode" if demo else "Uploaded Fluvius data"),
            ("Meter roles", _roles_line(state)),
        ],
        "ack_records": _ack_records(state, snapshot),
        "ack_boundary_detail": list(_ack_boundary_detail(state, snapshot)),
        "capex_caption": CAPEX_CAPTION,
        "screening_caption": SWEEP_COST_CAPTION,
        "detailed_solver_output": bool(review.get("detailed_solver_output")),
        "reporting_rows": [
            ("Seasonal plots", "Yes" if shared.get("seasonal_plots") else "No"),
            ("Winter ISO week", str(shared.get("winter_iso_week") or "")),
            ("Spring ISO week", str(shared.get("spring_iso_week") or "")),
            ("Summer ISO week", str(shared.get("summer_iso_week") or "")),
            ("Autumn ISO week", str(shared.get("autumn_iso_week") or "")),
        ],
        "intent": build_request_intent(state),
        "fingerprint": snapshot_fingerprint(snapshot) if snapshot else None,
    }
    if mode == MODE_ONE and one:
        model["battery_rows"] = [
            ("Usable capacity", f"{float(one['usable_kwh']):g} kWh"),
            ("Charge power", f"{float(one['charge_kw']):g} kW"),
            ("Discharge power", f"{float(one['discharge_kw']):g} kW"),
            ("Charge efficiency", format_percent(float(shared["eta_charge"]) * 100.0)),
            ("Discharge efficiency", format_percent(float(shared["eta_discharge"]) * 100.0)),
            ("Round-trip efficiency", round_trip),
            ("Initial stored energy", "0 kWh"),
            ("Maximum equivalent full cycles per year", f"{float(shared['max_efc_per_year']):g}"),
            (
                "Estimated battery cost",
                f"{float(shared['cost_eur_per_kwh']):g} EUR/kWh usable capacity",
            ),
            ("Estimated battery CAPEX", format_eur(estimated_capex_eur(one, shared))),
        ]
        basename = str(coverage.get("source_basename") or _price_basename())
        matched = coverage.get("selected_row_count")
        if prices_cover_period(coverage) and matched is not None:
            coverage_line = f"{basename} · {format_row_count(int(matched))} quarter-hours matched exactly"
        elif prices_cover_period(coverage):
            coverage_line = f"{basename} · exact coverage"
        else:
            coverage_line = f"{basename} · not covered"
        model["data_rows"].append(("Day-ahead prices", coverage_line))
    if mode == MODE_SIZE:
        model["candidate_summary"] = {
            "mode": _POWER_LABEL.get(str(sizing.get("power_mode") or ""), str(sizing.get("power_mode") or "")),
            "durations": _durations_label(list(sizing.get("durations_hours") or [])),
            "count": format_row_count(len(candidates)),
            "power_range": format_power_range(powers) or "—",
            "dispatch": "Revenue maximisation",
            "manual": None,
        }
        if str(sizing.get("power_mode") or "") == POWER_MANUAL:
            model["candidate_summary"]["manual"] = (
                f"{float(sizing.get('min_power_kw') or 0):g}–{float(sizing.get('max_power_kw') or 0):g} kW, "
                f"step {float(sizing.get('power_increment_kw') or 0):g} kW"
            )
        model["candidate_rows"] = [
            {
                "Candidate": item["candidate_id"],
                "Power (kW)": item["power_kw"],
                "Usable energy (kWh)": item["usable_energy_kwh"],
                "Duration (h)": item["duration_hours"],
            }
            for item in candidates
        ]
        duplicates = list(sizing.get("removed_duplicates") or [])
        model["candidate_note"] = (
            f"Removed {len(duplicates)} duplicate size(s)." if duplicates else None
        )
        model["screening_rows"] = [
            (
                "Estimated battery cost",
                f"{float(shared.get('cost_eur_per_kwh') or 0):g} EUR/kWh usable capacity",
            ),
            ("Evaluation period", f"{float(sizing.get('evaluation_years') or 0):g} years"),
            ("Revenue-capture threshold", format_percent(float(sizing.get("capture_pct") or 0))),
            ("Charge efficiency", format_percent(float(shared["eta_charge"]) * 100.0)),
            ("Discharge efficiency", format_percent(float(shared["eta_discharge"]) * 100.0)),
            ("Round-trip efficiency", round_trip),
            ("Maximum equivalent full cycles per year", f"{float(shared['max_efc_per_year']):g}"),
            ("Initial stored energy", "0 kWh"),
        ]
    return as_serialisable(model)


def build_review_model(state: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = stored_snapshot(state)
    digest = snapshot_fingerprint(snapshot) if snapshot else None
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    blocked = snapshot_block_reason(state)
    payload = {
        "ready": blocked is None,
        "block_reason": blocked,
        "action_reason": blocked,
        "snapshot": snapshot,
        "fingerprint": digest,
        "detailed_solver_output": bool(review.get("detailed_solver_output")),
        "partial_period_ack": bool(review.get("partial_period_ack")),
        "display": None if snapshot is None else build_display_model(state),
        "intent": None if snapshot is None else build_request_intent(state),
    }
    json.dumps(payload)
    return as_serialisable(payload)
