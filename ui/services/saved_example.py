"""Read verified Ganda Cars saved-example metadata. Does not import V1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ui.services.period_inspection import as_serialisable
from ui.services.uploads import (
    ROLE_LABELS,
    ROLE_ORDER,
    ROLE_REGISTERS,
    _issue_dict,
    _period_dict,
    _role_dict,
    _source_dict,
)

SITE_NAME = "Ganda Cars"
EXPECTED_PERIOD_ID = "2024"
EXPECTED_UNVALIDATED_COUNT = 96
EXPECTED_UNVALIDATED_DATE = "2024-10-02"
EXPECTED_SWEEP_CANDIDATE_COUNT = 18
EXPECTED_SWEEP_MODE = "automatic"


@dataclass(frozen=True)
class SavedExample:
    ok: bool
    site_name: str
    rows: tuple[dict[str, str], ...]
    error: str | None


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def candidate_report_paths(root: Path | None = None) -> tuple[Path, ...]:
    base = project_root() if root is None else Path(root)
    return (
        base / "ui" / "demo_artifacts" / "ganda_cars_2024_sweep" / "validation_report.json",
    )


def sweep_artifact_dir(root: Path | None = None) -> Path:
    base = project_root() if root is None else Path(root)
    return base / "ui" / "demo_artifacts" / "ganda_cars_2024_sweep"


def compare_artifact_dir(root: Path | None = None) -> Path:
    base = project_root() if root is None else Path(root)
    return base / "ui" / "demo_artifacts" / "ganda_cars_2024_compare"


def default_sample_dir(root: Path | None = None) -> Path:
    base = project_root() if root is None else Path(root)
    return base / "reference" / "input_samples" / "ganda_cars"


def load_saved_example(
    *,
    root: Path | None = None,
    report_path: Path | None = None,
    sample_dir: Path | None = None,
) -> SavedExample:
    """Resolve display metadata from verified validation material and source files."""
    blocked = SavedExample(
        ok=False,
        site_name=SITE_NAME,
        rows=(),
        error="The saved Ganda Cars example is not available.",
    )
    try:
        report, used_report = _read_report(root=root, report_path=report_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return blocked
    if used_report is None or not isinstance(report, Mapping):
        return blocked

    roles = report.get("roles") or {}
    sources = list(report.get("sources") or [])
    samples = default_sample_dir(root) if sample_dir is None else Path(sample_dir)
    verify_source_files = report_path is not None or sample_dir is not None
    rows: list[dict[str, str]] = []
    for role in ROLE_ORDER:
        meta = roles.get(role)
        if not isinstance(meta, Mapping):
            return blocked
        register = str(meta.get("register") or ROLE_REGISTERS[role])
        unit = str(meta.get("unit") or "")
        filename = _filename_for_register(sources, register)
        if not filename or not unit:
            return blocked
        if verify_source_files and not _source_file_exists(filename, sources, samples):
            return blocked
        rows.append(
            {
                "Role": ROLE_LABELS[role],
                "File": filename,
                "Detected register": register,
                "Unit": unit,
            }
        )
    if len(rows) != 3:
        return blocked
    return SavedExample(ok=True, site_name=SITE_NAME, rows=tuple(rows), error=None)


def _blocked_snapshot(message: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "roles": {},
        "sources": [],
        "issues": [],
        "periods": [],
        "dst": {},
        "error": {
            "code": "SAVED_EXAMPLE_UNAVAILABLE",
            "message": message or "The saved Ganda Cars example is not available.",
        },
    }


def project_validation_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Project a verified validation report into the live ingest snapshot shape."""
    roles_in = report.get("roles") or {}
    roles = {
        str(name): _role_dict(meta)
        for name, meta in dict(roles_in).items()
        if isinstance(meta, Mapping)
    }
    sources = [
        _source_dict(item)
        for item in list(report.get("sources") or [])
        if isinstance(item, Mapping)
    ]
    if report.get("issues"):
        raw_issues = list(report.get("issues") or [])
    else:
        raw_issues = list(report.get("fatal") or []) + list(report.get("warnings") or [])
    issues = [_issue_dict(item) for item in raw_issues]
    periods = [
        _period_dict(item)
        for item in list(report.get("periods") or [])
        if isinstance(item, Mapping) or hasattr(item, "to_dict")
    ]
    dst_in = report.get("dst") or {}
    dst = dict(dst_in) if isinstance(dst_in, Mapping) else {}
    simultaneous = report.get("simultaneous_import_export")
    fatals = [item for item in issues if item.get("severity") == "fatal"]
    missing = [role for role in ROLE_ORDER if role not in roles]
    ok = bool(report.get("ok", not fatals)) and not fatals and not missing
    snapshot: dict[str, Any] = {
        "ok": ok,
        "roles": roles,
        "sources": sources,
        "issues": issues,
        "periods": periods,
        "dst": dst,
        "error": None,
    }
    if isinstance(simultaneous, Mapping) and simultaneous:
        snapshot["simultaneous_import_export"] = {
            key: simultaneous[key]
            for key in ("n_intervals", "threshold_kwh", "note")
            if key in simultaneous
        }
    return snapshot


def load_saved_snapshot(
    *,
    root: Path | None = None,
    report_path: Path | None = None,
    sample_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a Step 2 snapshot from verified Ganda metadata, or a blocked result."""
    example = load_saved_example(root=root, report_path=report_path, sample_dir=sample_dir)
    if not example.ok:
        return _blocked_snapshot(example.error)
    try:
        report, used_report = _read_report(root=root, report_path=report_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _blocked_snapshot()
    if used_report is None or not isinstance(report, Mapping):
        return _blocked_snapshot()
    if report.get("periods") is not None and not isinstance(report.get("periods"), list):
        return _blocked_snapshot("The saved Ganda Cars example is not available.")
    snapshot = project_validation_report(report)
    roles = snapshot.get("roles") or {}
    if not all(isinstance(roles.get(role), Mapping) and roles[role].get("register") for role in ROLE_ORDER):
        return _blocked_snapshot()
    if not isinstance(snapshot.get("periods"), list):
        return _blocked_snapshot()
    return snapshot


def _read_report(
    *,
    root: Path | None,
    report_path: Path | None,
) -> tuple[dict[str, Any], Path | None]:
    paths: Sequence[Path]
    if report_path is not None:
        paths = (Path(report_path),)
    else:
        paths = candidate_report_paths(root)
    for path in paths:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload, path
    return {}, None


def _filename_for_register(sources: Sequence[Mapping[str, Any]], register: str) -> str:
    for source in sources:
        registers = [str(item) for item in (source.get("registers") or [])]
        if register in registers:
            return Path(str(source.get("path") or "")).name
    return ""


def _source_file_exists(
    filename: str,
    sources: Sequence[Mapping[str, Any]],
    sample_dir: Path,
) -> bool:
    candidates = [sample_dir / filename]
    for source in sources:
        raw = source.get("path")
        if not raw:
            continue
        path = Path(str(raw))
        if path.name == filename:
            candidates.append(path)
    return any(path.is_file() for path in candidates)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _blocked_period_context(message: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "SAVED_EXAMPLE_UNAVAILABLE",
            "message": message or "The saved Ganda Cars example is not available.",
        },
        "period_id": EXPECTED_PERIOD_ID,
        "unvalidated_ack": False,
        "site_boundary_ack": False,
        "period_inspection": None,
        "period_inspection_key": None,
        "price_coverage": None,
        "price_coverage_key": None,
        "selected_period": None,
    }


def load_saved_period_context(
    *,
    root: Path | None = None,
    report_path: Path | None = None,
    site_analysis_path: Path | None = None,
    sweep_request_path: Path | None = None,
    compare_metadata_path: Path | None = None,
) -> dict[str, Any]:
    """Read-only Ganda 2024 period, acknowledgements, site analysis and price audit."""
    try:
        report, used_report = _read_report(root=root, report_path=report_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _blocked_period_context()
    if used_report is None or not isinstance(report, Mapping):
        return _blocked_period_context()

    sweep_dir = sweep_artifact_dir(root)
    compare_dir = compare_artifact_dir(root)
    analysis = _read_json(
        Path(site_analysis_path) if site_analysis_path is not None else sweep_dir / "site_analysis.json"
    )
    request = _read_json(
        Path(sweep_request_path) if sweep_request_path is not None else sweep_dir / "sweep_request.json"
    )
    metadata = _read_json(
        Path(compare_metadata_path)
        if compare_metadata_path is not None
        else compare_dir / "run_metadata.json"
    )
    if analysis is None or request is None or metadata is None:
        return _blocked_period_context()

    periods = [item for item in (report.get("periods") or []) if isinstance(item, Mapping)]
    selected = report.get("selected_period")
    if not isinstance(selected, Mapping):
        selected = next((item for item in periods if str(item.get("id")) == EXPECTED_PERIOD_ID), None)
    if not isinstance(selected, Mapping) or str(selected.get("id")) != EXPECTED_PERIOD_ID:
        return _blocked_period_context()
    if str(request.get("period_id") or "") != EXPECTED_PERIOD_ID:
        return _blocked_period_context()

    policy = report.get("unvalidated_policy") if isinstance(report.get("unvalidated_policy"), Mapping) else {}
    n_unvalidated = int(policy.get("n_unvalidated_in_selected_period") or selected.get("n_unvalidated") or 0)
    dates = [str(item) for item in (policy.get("dates") or [])]
    if n_unvalidated != EXPECTED_UNVALIDATED_COUNT or EXPECTED_UNVALIDATED_DATE not in dates:
        return _blocked_period_context()
    if int(selected.get("n_unvalidated") or 0) != EXPECTED_UNVALIDATED_COUNT:
        return _blocked_period_context()

    n_intervals = int(selected.get("n_intervals") or 0)
    if int(analysis.get("n_intervals") or 0) != n_intervals or n_intervals <= 0:
        return _blocked_period_context()

    boundary = report.get("site_boundary_policy") if isinstance(report.get("site_boundary_policy"), Mapping) else {}
    if bool(boundary.get("acknowledge_site_boundary")):
        return _blocked_period_context()

    issues = list(report.get("issues") or []) or list(report.get("fatal") or []) + list(report.get("warnings") or [])
    for item in issues:
        if isinstance(item, Mapping) and item.get("code") in {"NEGATIVE_LOAD", "EXPORT_EXCEEDS_PV"}:
            return _blocked_period_context()

    audit = metadata.get("dynamic_injection_prices")
    if not isinstance(audit, Mapping):
        return _blocked_period_context()
    if int(audit.get("selected_row_count") or 0) != n_intervals:
        return _blocked_period_context()

    site_analysis = as_serialisable(analysis)
    report_snapshot = as_serialisable(
        {
            "unvalidated_policy": dict(policy),
            "site_boundary_policy": dict(boundary),
            "simultaneous_import_export": report.get("simultaneous_import_export"),
            "dst": report.get("dst") or {},
            "selected_period": dict(selected),
        }
    )
    inspection = {
        "ok": True,
        "requires_site_boundary_acknowledgement": False,
        "period_id": EXPECTED_PERIOD_ID,
        "selected_period": as_serialisable(selected),
        "fatal": [],
        "warnings": [],
        "report": report_snapshot,
        "site_analysis": site_analysis,
        "automatic_candidates": list(site_analysis.get("automatic_candidates") or []),
    }
    source = audit.get("source_path")
    price_coverage = {
        "covered": True,
        "unavailable": False,
        "one_battery_unavailable": False,
        "selected_row_count": int(audit["selected_row_count"]),
        "source_basename": Path(str(source)).name if source else None,
        "coverage_utc": audit.get("coverage_utc"),
        "native_resolution_counts": audit.get("native_resolution_counts"),
        "hourly_values_repeated": audit.get("hourly_values_repeated"),
        "error": None,
    }
    return {
        "ok": True,
        "error": None,
        "period_id": EXPECTED_PERIOD_ID,
        "unvalidated_ack": True,
        "site_boundary_ack": False,
        "selected_period": as_serialisable(selected),
        "unvalidated_dates": tuple(dates),
        "period_inspection": inspection,
        "period_inspection_key": "saved:ganda:2024",
        "price_coverage": price_coverage,
        "price_coverage_key": "saved:ganda:2024:prices",
    }


def _blocked_configure_context(message: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "SAVED_EXAMPLE_UNAVAILABLE",
            "message": message or "The saved Ganda Cars example is not available.",
        },
        "configure": None,
    }


def _duration_flags(hours: Sequence[Any]) -> dict[str, Any]:
    selected = {round(float(item), 12) for item in hours}
    custom = [
        float(item)
        for item in hours
        if round(float(item), 12) not in {1.0, 2.0, 4.0, 6.0}
    ]
    custom_text = ", ".join(
        str(int(item)) if abs(item - round(item)) < 1e-9 else f"{item:g}" for item in custom
    )
    return {
        "duration_1h": 1.0 in selected,
        "duration_2h": 2.0 in selected,
        "duration_4h": 4.0 in selected,
        "duration_6h": 6.0 in selected,
        "custom_hours_text": custom_text,
    }


def load_saved_configure_context(
    *,
    root: Path | None = None,
    resolved_config_path: Path | None = None,
    sweep_request_path: Path | None = None,
    site_analysis_path: Path | None = None,
) -> dict[str, Any]:
    """Read-only Ganda Configure values from the matching saved artifacts."""
    period = load_saved_period_context(root=root)
    if not period.get("ok"):
        return _blocked_configure_context()
    sweep_dir = sweep_artifact_dir(root)
    compare_dir = compare_artifact_dir(root)
    resolved = _read_json(
        Path(resolved_config_path) if resolved_config_path is not None else compare_dir / "resolved_config.json"
    )
    request = _read_json(
        Path(sweep_request_path) if sweep_request_path is not None else sweep_dir / "sweep_request.json"
    )
    analysis = _read_json(
        Path(site_analysis_path) if site_analysis_path is not None else sweep_dir / "site_analysis.json"
    )
    if resolved is None or request is None or analysis is None:
        return _blocked_configure_context()
    battery = (resolved.get("resolved") or {}).get("battery")
    tariffs = (resolved.get("resolved") or {}).get("tariffs")
    reporting = (resolved.get("resolved") or {}).get("reporting")
    economics = (resolved.get("resolved") or {}).get("economics")
    if not all(isinstance(item, Mapping) for item in (battery, tariffs, reporting, economics)):
        return _blocked_configure_context()
    if str(request.get("site_label") or "") != SITE_NAME:
        return _blocked_configure_context()
    if str(request.get("period_id") or "") != EXPECTED_PERIOD_ID:
        return _blocked_configure_context()
    if str(request.get("mode") or "") != EXPECTED_SWEEP_MODE:
        return _blocked_configure_context()
    candidates = [item for item in (request.get("candidates") or []) if isinstance(item, Mapping)]
    if len(candidates) != EXPECTED_SWEEP_CANDIDATE_COUNT:
        return _blocked_configure_context()
    durations = [float(item) for item in (request.get("durations_hours") or [])]
    if not durations:
        return _blocked_configure_context()
    req_sweep = request.get("sweep") if isinstance(request.get("sweep"), Mapping) else {}
    if "evaluation_period_years" not in req_sweep or "revenue_capture_threshold_pct" not in req_sweep:
        return _blocked_configure_context()
    charge_kw = float(battery["p_charge_kw"])
    discharge_kw = float(battery["p_discharge_kw"])
    split = abs(charge_kw - discharge_kw) > 1e-12
    site = as_serialisable(analysis)
    shared = {
        "eta_charge": float(battery["eta_charge"]),
        "eta_discharge": float(battery["eta_discharge"]),
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
    configure = as_serialisable(
        {
            "source": "saved",
            "defaults_basename": None,
            "defaults_signature": None,
            "saved_identity": {
                "period_id": EXPECTED_PERIOD_ID,
                "site_name": SITE_NAME,
                "compare_artifact": "resolved_config.json",
                "sweep_artifact": "sweep_request.json",
                "candidate_count": len(candidates),
            },
            "shared": shared,
            "one_battery": {
                "usable_kwh": float(battery["e_usable_kwh"]),
                "power_kw": charge_kw,
                "split_power": split,
                "charge_kw": charge_kw,
                "discharge_kw": discharge_kw,
            },
            "sizing": {
                **_duration_flags(durations),
                "power_mode": "suggested",
                "min_power_kw": None,
                "max_power_kw": None,
                "power_increment_kw": None,
                "explicit_text": "",
                "evaluation_years": float(req_sweep["evaluation_period_years"]),
                "capture_pct": float(req_sweep["revenue_capture_threshold_pct"]),
            },
            "candidates": {
                "ok": True,
                "mode": str(request.get("mode")),
                "durations_hours": durations,
                "items": [as_serialisable(item) for item in candidates],
                "removed_duplicates": [],
                "count": len(candidates),
                "error": None,
                "suggested_blocked": False,
                "suggested_message": None,
                "power_range_kw": list(site.get("power_grid_kw") or []),
                "p995_import_kw": site.get("p995_import_kw"),
                "p995_surplus_kw": site.get("p995_surplus_kw"),
            },
            "snapshot": None,
        }
    )
    return {
        "ok": True,
        "error": None,
        "configure": configure,
        "period_id": EXPECTED_PERIOD_ID,
    }
