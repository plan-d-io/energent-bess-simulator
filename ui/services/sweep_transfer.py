"""Central transfer from a sweep candidate into editable one-battery Configure."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping

from btm_sim.sweep import load_sweep_request, serialize_sweep_request

from ui.flow import (
    CONFIGURE_WIDGET_PREFIX,
    DEMO_CHECKBOX_KEY,
    REVIEW_WIDGET_PREFIX,
    ROUTE_LIVE,
    SITE_WIDGET_KEY,
    UPLOAD_ORIGIN_TRANSFER,
    UPLOAD_WIDGET_PREFIX,
    apply_analysis_mode,
    store_saved_period_context,
)
from ui.services.configure import MODE_ONE, apply_configure_fields
from ui.services.saved_example import (
    SITE_NAME,
    default_sample_dir,
    load_saved_example,
    load_saved_period_context,
)
from ui.services.sweep_display import load_sweep_display, lookup_candidate
from ui.services.sweep_format import TRANSFER_DISABLED_NOTE
from ui.services.uploads import file_signature, inspect_fluvius_payloads, snapshot_is_ready


def one_battery_from_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    power = float(candidate["power_kw"])
    return {
        "usable_kwh": float(candidate["usable_energy_kwh"]),
        "power_kw": power,
        "split_power": False,
        "charge_kw": power,
        "discharge_kw": power,
    }


def shared_from_sweep_request(request: Mapping[str, Any]) -> dict[str, Any]:
    battery = request.get("battery") if isinstance(request.get("battery"), Mapping) else {}
    tariffs = request.get("tariffs") if isinstance(request.get("tariffs"), Mapping) else {}
    economics = request.get("economics") if isinstance(request.get("economics"), Mapping) else {}
    reporting = request.get("reporting") if isinstance(request.get("reporting"), Mapping) else {}
    fields: dict[str, Any] = {}
    if battery.get("eta_charge") is not None:
        fields["eta_charge"] = float(battery["eta_charge"])
    if battery.get("eta_discharge") is not None:
        fields["eta_discharge"] = float(battery["eta_discharge"])
    if battery.get("max_equivalent_full_cycles_per_year") is not None:
        fields["max_efc_per_year"] = float(battery["max_equivalent_full_cycles_per_year"])
    if tariffs.get("customer_sale_eur_per_mwh") is not None:
        fields["customer_sale_eur_per_mwh"] = float(tariffs["customer_sale_eur_per_mwh"])
    if tariffs.get("peak_export_eur_per_mwh") is not None:
        fields["peak_export_eur_per_mwh"] = float(tariffs["peak_export_eur_per_mwh"])
    if tariffs.get("offpeak_export_eur_per_mwh") is not None:
        fields["offpeak_export_eur_per_mwh"] = float(tariffs["offpeak_export_eur_per_mwh"])
    if tariffs.get("peak_start_local"):
        fields["peak_start_local"] = str(tariffs["peak_start_local"])
    if tariffs.get("peak_end_local"):
        fields["peak_end_local"] = str(tariffs["peak_end_local"])
    if "weekends_offpeak" in tariffs:
        fields["weekends_offpeak"] = bool(tariffs["weekends_offpeak"])
    if tariffs.get("timezone"):
        fields["timezone"] = str(tariffs["timezone"])
    if economics.get("estimated_battery_cost_eur_per_kwh") is not None:
        fields["cost_eur_per_kwh"] = float(economics["estimated_battery_cost_eur_per_kwh"])
    if "seasonal_plots" in reporting:
        fields["seasonal_plots"] = bool(reporting["seasonal_plots"])
    for season in ("winter_iso_week", "spring_iso_week", "summer_iso_week", "autumn_iso_week"):
        if reporting.get(season) is not None:
            fields[season] = int(reporting[season])
    return fields


def load_frozen_sweep_request(folder: Path | str) -> dict[str, Any]:
    path = Path(folder) / "sweep_request.json"
    return serialize_sweep_request(load_sweep_request(path))


def ganda_sample_payloads(
    *,
    root: Path | None = None,
    sample_dir: Path | None = None,
) -> list[tuple[str, bytes]] | None:
    example = load_saved_example(root=root, sample_dir=sample_dir)
    if not example.ok:
        return None
    folder = default_sample_dir(root) if sample_dir is None else Path(sample_dir)
    payloads: list[tuple[str, bytes]] = []
    for row in example.rows:
        filename = str(row.get("File") or "")
        path = folder / filename
        if not filename or not path.is_file():
            return None
        payloads.append((filename, path.read_bytes()))
    if len(payloads) != 3:
        return None
    return payloads


def demo_transfer_available(
    *,
    root: Path | None = None,
    sample_dir: Path | None = None,
) -> tuple[bool, str | None]:
    if ganda_sample_payloads(root=root, sample_dir=sample_dir) is None:
        return False, TRANSFER_DISABLED_NOTE
    return True, None


def _candidate_fields_ok(candidate: Mapping[str, Any] | None) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    try:
        float(candidate["power_kw"])
        float(candidate["usable_energy_kwh"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _stored_sweep_candidate(folder: Path | str, candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        model = load_sweep_display(Path(folder))
    except Exception:
        return None
    cid = str(candidate.get("candidate_id") or "")
    match = lookup_candidate(model.summary, cid)
    if match is None or not _candidate_fields_ok(match):
        return None
    return match


def _load_request(folder: Path | str) -> dict[str, Any] | None:
    try:
        request = load_frozen_sweep_request(folder)
    except Exception:
        return None
    if not isinstance(request, Mapping):
        return None
    shared = shared_from_sweep_request(request)
    if "eta_charge" not in shared or "cost_eur_per_kwh" not in shared:
        return None
    return dict(request)


def _prepare_demo_transfer(
    *,
    candidate: Mapping[str, Any],
    folder: Path | str,
    inspect: Callable[..., Any] | None,
    root: Path | None,
    sample_dir: Path | None,
) -> dict[str, Any]:
    stored = _stored_sweep_candidate(folder, candidate)
    if stored is None:
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    request = _load_request(folder)
    if request is None:
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    payloads = ganda_sample_payloads(root=root, sample_dir=sample_dir)
    if payloads is None:
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    inspect_fn = inspect_fluvius_payloads if inspect is None else inspect
    snapshot = inspect_fn(payloads)
    if not snapshot_is_ready(snapshot if isinstance(snapshot, Mapping) else None):
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    period = load_saved_period_context(root=root)
    if not period.get("ok"):
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    try:
        one_battery_from_candidate(stored)
        shared_from_sweep_request(request)
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    return {
        "ok": True,
        "candidate": stored,
        "request": request,
        "payloads": tuple(payloads),
        "snapshot": dict(snapshot),
        "period": dict(period),
    }


def _land_on_configure(state: MutableMapping[str, Any]) -> None:
    state["step"] = 4
    state["max_step"] = 4
    for key in ("review", "job", "results", "launch_error"):
        state.pop(key, None)
    configure = state.get("configure")
    if isinstance(configure, dict):
        configure["snapshot"] = None
        configure["source"] = "live"
        configure["saved_identity"] = None


def _commit_demo_transfer(
    state: MutableMapping[str, Any],
    prepared: Mapping[str, Any],
) -> None:
    payloads = tuple(prepared["payloads"])
    snapshot = dict(prepared["snapshot"])
    state["data_route"] = ROUTE_LIVE
    state["upload_origin"] = UPLOAD_ORIGIN_TRANSFER
    state["upload_generation"] = int(state.get("upload_generation") or 0) + 1
    state["site_name"] = SITE_NAME
    state["upload_payloads"] = payloads
    state["upload_signature"] = file_signature(payloads)
    state["ingest_snapshot"] = snapshot
    state["data_ready"] = True
    state["inspecting"] = False
    state["adapter_error"] = None
    state["upload_messages"] = []
    store_saved_period_context(state, prepared["period"])
    apply_analysis_mode(state, MODE_ONE)
    apply_configure_fields(
        state,
        shared=shared_from_sweep_request(prepared["request"]),
        one_battery=one_battery_from_candidate(prepared["candidate"]),
    )
    _land_on_configure(state)


def transfer_live_candidate(
    state: MutableMapping[str, Any],
    *,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    if not _candidate_fields_ok(candidate):
        return {"ok": False, "reason": TRANSFER_DISABLED_NOTE}
    apply_analysis_mode(state, MODE_ONE)
    apply_configure_fields(state, one_battery=one_battery_from_candidate(candidate))
    _land_on_configure(state)
    return {"ok": True}


def transfer_demo_candidate(
    state: MutableMapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    folder: Path | str,
    inspect: Callable[..., Any] | None = None,
    root: Path | None = None,
    sample_dir: Path | None = None,
) -> dict[str, Any]:
    prepared = _prepare_demo_transfer(
        candidate=candidate,
        folder=folder,
        inspect=inspect,
        root=root,
        sample_dir=sample_dir,
    )
    if not prepared.get("ok"):
        return {"ok": False, "reason": prepared.get("reason") or TRANSFER_DISABLED_NOTE}
    _commit_demo_transfer(state, prepared)
    return {"ok": True}


def clear_transfer_widget_keys(session: MutableMapping[str, Any], *, demo_off: bool) -> None:
    if demo_off:
        session[DEMO_CHECKBOX_KEY] = False
        session[SITE_WIDGET_KEY] = SITE_NAME
    for key in list(session.keys()):
        text = str(key)
        if (
            text.startswith(CONFIGURE_WIDGET_PREFIX)
            or text.startswith(REVIEW_WIDGET_PREFIX)
            or text.startswith(UPLOAD_WIDGET_PREFIX)
        ):
            session.pop(key, None)
