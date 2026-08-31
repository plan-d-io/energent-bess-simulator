"""Typed frozen request for one revenue battery-size sweep."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from btm_sim import __version__
from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_reusable_settings
from btm_sim.config.schema import (
    EconomicsConfig,
    ReportingConfig,
    SweepConfig,
    TariffConfig,
    battery_from_mapping,
    economics_from_mapping,
    format_hhmm,
    reporting_from_mapping,
    sweep_from_mapping,
    tariffs_from_mapping,
)
from btm_sim.progress import REQUEST_SCHEMA_VERSION, iso_utc
from btm_sim.run.request import (
    BATTERY_TOML_KEYS,
    FluviusInputRef,
    _fluvius_ref,
    _resolve_user_path,
    new_job_id,
)
from btm_sim.sweep.artifacts import SWEEP_ARTIFACT_SCHEMA_VERSION, SWEEP_REQUEST_FILENAME
from btm_sim.sweep.candidates import (
    CandidateBuild,
    SweepCandidate,
    attach_daily_diagnostics,
    build_candidates,
    candidate_from_mapping,
    parse_durations,
    parse_explicit_pairs,
    parse_mode,
)
from btm_sim.sweep.exceptions import SweepRequestError
from btm_sim.sweep.site import SiteAnalysis, analyse_site, materialize_selected_period, site_analysis_from_mapping


@dataclass(frozen=True)
class SweepRequest:
    """Frozen, already-resolved request for one revenue sweep."""

    request_schema_version: int
    job_id: str
    created_at_utc: str
    software_version: str
    artifact_schema_version: int
    site_label: str | None
    output_dir: Path
    period_id: str
    allow_unvalidated: bool
    acknowledge_site_boundary: bool
    detailed_solver_output: bool
    fluvius_inputs: tuple[FluviusInputRef, FluviusInputRef, FluviusInputRef]
    defaults_path: Path
    defaults_sha256: str
    run_toml_path: Path | None
    run_toml_sha256: str | None
    battery: BatteryConfig
    tariffs: TariffConfig
    reporting: ReportingConfig
    economics: EconomicsConfig
    sweep: SweepConfig
    mode: str
    durations_hours: tuple[float, ...]
    candidates: tuple[SweepCandidate, ...]
    site_analysis: SiteAnalysis
    removed_duplicates: tuple[dict[str, float], ...]
    manual_min_power_kw: float | None
    manual_max_power_kw: float | None
    manual_power_increment_kw: float | None
    explicit_pairs: tuple[tuple[float, float], ...]
    value_sources: dict[str, dict[str, str]]
    cli_overrides: tuple[str, ...]
    config_audit: dict[str, Any]

    def fluvius_paths(self) -> tuple[Path, Path, Path]:
        return tuple(item.path for item in self.fluvius_inputs)  # type: ignore[return-value]


def build_sweep_request(
    *,
    fluvius_paths: Sequence[str | Path],
    period_id: str,
    output_dir: str | Path,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
    site_label: str | None = None,
    defaults_path: str | Path | None = None,
    run_toml_path: str | Path | None = None,
    detailed_solver_output: bool = False,
    mode: str = "automatic",
    durations_hours: Sequence[float] | None = None,
    min_power_kw: float | None = None,
    max_power_kw: float | None = None,
    power_increment_kw: float | None = None,
    explicit_pairs: Sequence[tuple[float, float]] | None = None,
    cli: dict[str, Any] | None = None,
    job_id: str | None = None,
    cwd: Path | None = None,
) -> SweepRequest:
    """Build and validate a frozen sweep request. Precedence: CLI > run TOML > defaults."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    paths = [_resolve_user_path(path, cwd=cwd) for path in fluvius_paths]
    if len(paths) != 3:
        raise SweepRequestError(
            f"Provide exactly three Fluvius CSV exports, got {len(paths)}",
            category="invalid_request",
        )
    if len({str(path) for path in paths}) != 3:
        raise SweepRequestError("The three Fluvius input paths must be distinct", category="invalid_request")
    period = str(period_id).strip()
    if not period:
        raise SweepRequestError("A selected period ID is required", category="invalid_period")
    out = _resolve_user_path(output_dir, cwd=cwd)
    resolved_cli = {key: value for key, value in (cli or {}).items() if value is not None}
    resolved_cli["output_dir"] = str(out)
    resolved_cli.pop("output_root", None)
    resolved_cli.pop("dynamic_injection_prices", None)
    try:
        audit = resolve_reusable_settings(
            toml_path=None if run_toml_path is None else _resolve_user_path(run_toml_path, cwd=cwd),
            cli=resolved_cli,
            cwd=cwd,
            defaults_path=defaults_path,
            require_zero_initial_charge=True,
        )
    except ConfigError as exc:
        raise SweepRequestError(str(exc), category="invalid_configuration") from exc

    sweep: SweepConfig = audit["sweep"]
    durations = (
        tuple(sweep.default_durations_hours)
        if durations_hours is None
        else parse_durations(list(durations_hours), name="durations")
    )
    chosen_mode = parse_mode(mode)
    pairs = parse_explicit_pairs(explicit_pairs)
    materialized = materialize_selected_period(
        paths,
        period,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )
    site = analyse_site(materialized.frame, durations)
    built = _build_from_site(
        site,
        mode=chosen_mode,
        durations=durations,
        min_power_kw=min_power_kw,
        max_power_kw=max_power_kw,
        power_increment_kw=power_increment_kw,
        explicit_pairs=pairs,
    )
    inputs = tuple(_fluvius_ref(path) for path in paths)
    return SweepRequest(
        request_schema_version=REQUEST_SCHEMA_VERSION,
        job_id=job_id or new_job_id(),
        created_at_utc=iso_utc(),
        software_version=__version__,
        artifact_schema_version=SWEEP_ARTIFACT_SCHEMA_VERSION,
        site_label=None if site_label is None or not str(site_label).strip() else str(site_label).strip(),
        output_dir=out,
        period_id=period,
        allow_unvalidated=bool(allow_unvalidated),
        acknowledge_site_boundary=bool(acknowledge_site_boundary),
        detailed_solver_output=bool(detailed_solver_output),
        fluvius_inputs=inputs,  # type: ignore[arg-type]
        defaults_path=Path(audit["defaults_path"]),
        defaults_sha256=str(audit["defaults_sha256"]),
        run_toml_path=None if audit["run_toml_path"] is None else Path(audit["run_toml_path"]),
        run_toml_sha256=audit["run_toml_sha256"],
        battery=audit["battery"],
        tariffs=audit["tariffs"],
        reporting=audit["reporting"],
        economics=audit["economics"],
        sweep=sweep,
        mode=built.mode,
        durations_hours=built.durations_hours,
        candidates=built.candidates,
        site_analysis=site,
        removed_duplicates=built.removed_duplicates,
        manual_min_power_kw=None if min_power_kw is None else float(min_power_kw),
        manual_max_power_kw=None if max_power_kw is None else float(max_power_kw),
        manual_power_increment_kw=None if power_increment_kw is None else float(power_increment_kw),
        explicit_pairs=pairs,
        value_sources=dict(audit["value_sources"]),
        cli_overrides=tuple(audit["cli_overrides"]),
        config_audit={
            key: value
            for key, value in audit.items()
            if key
            not in {
                "battery",
                "tariffs",
                "reporting",
                "economics",
                "sweep",
                "dynamic_injection_prices",
                "output_dir",
                "output_root",
            }
        },
    )


def _build_from_site(
    site: SiteAnalysis,
    *,
    mode: str,
    durations: tuple[float, ...],
    min_power_kw: float | None,
    max_power_kw: float | None,
    power_increment_kw: float | None,
    explicit_pairs: tuple[tuple[float, float], ...],
) -> CandidateBuild:
    return build_candidates(
        mode=mode,
        durations_hours=durations,
        automatic_candidates=site.automatic_candidates,
        site_p95_daily_import_kwh=site.p95_daily_import_kwh,
        site_p95_daily_surplus_kwh=site.p95_daily_surplus_kwh,
        min_power_kw=min_power_kw,
        max_power_kw=max_power_kw,
        power_increment_kw=power_increment_kw,
        explicit_pairs=explicit_pairs,
        no_revenue_shifting_opportunity=site.no_revenue_shifting_opportunity,
    )


def serialize_sweep_request(request: SweepRequest) -> dict[str, Any]:
    return {
        "request_schema_version": request.request_schema_version,
        "job_id": request.job_id,
        "created_at_utc": request.created_at_utc,
        "software_version": request.software_version,
        "artifact_schema_version": request.artifact_schema_version,
        "site_label": request.site_label,
        "output_dir": str(request.output_dir),
        "period_id": request.period_id,
        "allow_unvalidated": request.allow_unvalidated,
        "acknowledge_site_boundary": request.acknowledge_site_boundary,
        "detailed_solver_output": request.detailed_solver_output,
        "fluvius_inputs": [item.to_dict() for item in request.fluvius_inputs],
        "defaults_path": str(request.defaults_path),
        "defaults_sha256": request.defaults_sha256,
        "run_toml_path": None if request.run_toml_path is None else str(request.run_toml_path),
        "run_toml_sha256": request.run_toml_sha256,
        "battery": request.battery.to_dict(),
        "tariffs": request.tariffs.to_dict(),
        "reporting": request.reporting.to_dict(),
        "economics": request.economics.to_dict(),
        "sweep": request.sweep.to_dict(),
        "mode": request.mode,
        "durations_hours": list(request.durations_hours),
        "candidates": [item.to_dict() for item in request.candidates],
        "site_analysis": request.site_analysis.to_dict(),
        "removed_duplicates": list(request.removed_duplicates),
        "manual_range": {
            "min_power_kw": request.manual_min_power_kw,
            "max_power_kw": request.manual_max_power_kw,
            "power_increment_kw": request.manual_power_increment_kw,
        },
        "explicit_pairs": [list(pair) for pair in request.explicit_pairs],
        "value_sources": request.value_sources,
        "cli_overrides": list(request.cli_overrides),
        "config_audit": request.config_audit,
    }


def write_sweep_request(request: SweepRequest, path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else request.output_dir / SWEEP_REQUEST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(serialize_sweep_request(request), indent=2) + "\n", encoding="utf-8")
    return target


def load_sweep_request(path: str | Path) -> SweepRequest:
    request_path = Path(path)
    if not request_path.exists():
        raise SweepRequestError(f"Sweep request file not found: {request_path}", category="invalid_request")
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SweepRequestError(f"Sweep request is not valid JSON: {exc}", category="invalid_request") from exc
    if not isinstance(payload, dict):
        raise SweepRequestError("Sweep request JSON root must be an object", category="invalid_request")
    return sweep_request_from_payload(payload)


def sweep_request_from_payload(payload: dict[str, Any]) -> SweepRequest:
    version = payload.get("request_schema_version")
    if version != REQUEST_SCHEMA_VERSION:
        raise SweepRequestError(
            f"Unsupported request_schema_version {version!r}; expected {REQUEST_SCHEMA_VERSION}",
            category="invalid_request",
        )
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise SweepRequestError("Frozen request is missing job_id", category="invalid_request")
    period = str(payload.get("period_id") or "").strip()
    if not period:
        raise SweepRequestError("Frozen request is missing period_id", category="invalid_period")
    output_dir = payload.get("output_dir")
    if not output_dir:
        raise SweepRequestError("Frozen request is missing output_dir", category="invalid_request")
    raw_inputs = payload.get("fluvius_inputs")
    if not isinstance(raw_inputs, list) or len(raw_inputs) != 3:
        raise SweepRequestError("Frozen request must list exactly three Fluvius inputs", category="invalid_request")
    inputs: list[FluviusInputRef] = []
    for item in raw_inputs:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise SweepRequestError("Each Fluvius input needs path and sha256", category="invalid_request")
        path = Path(str(item["path"]))
        inputs.append(
            FluviusInputRef(
                path=path,
                original_name=str(item.get("original_name") or path.name),
                sha256=str(item["sha256"]),
            )
        )
    try:
        battery = _battery_from_frozen(payload.get("battery"))
        tariffs = _tariffs_from_frozen(payload.get("tariffs"))
        reporting = _reporting_from_frozen(payload.get("reporting"))
        sweep = _sweep_from_frozen(payload.get("sweep"))
        economics = _economics_from_frozen(payload, sweep)
        site = site_analysis_from_mapping(payload.get("site_analysis") or {})
        candidates = tuple(candidate_from_mapping(item) for item in payload.get("candidates") or [])
    except (ConfigError, BatteryConfigError, SweepRequestError) as exc:
        raise SweepRequestError(str(exc), category="invalid_configuration") from exc
    if abs(battery.soc_initial_kwh) > 0.0:
        raise SweepRequestError(
            "The sweep requires battery.initial_charge_kwh = 0",
            category="invalid_configuration",
        )
    defaults_path = payload.get("defaults_path")
    if not defaults_path:
        raise SweepRequestError("Frozen request is missing defaults_path", category="invalid_request")
    artifact_version = int(payload.get("artifact_schema_version") or SWEEP_ARTIFACT_SCHEMA_VERSION)
    if artifact_version != SWEEP_ARTIFACT_SCHEMA_VERSION:
        raise SweepRequestError(
            f"Frozen request artifact_schema_version {artifact_version} is not {SWEEP_ARTIFACT_SCHEMA_VERSION}",
            category="invalid_request",
        )
    if not candidates:
        raise SweepRequestError("Frozen request must contain at least one candidate", category="invalid_request")
    candidates = attach_daily_diagnostics(
        candidates,
        site_p95_daily_import_kwh=site.p95_daily_import_kwh,
        site_p95_daily_surplus_kwh=site.p95_daily_surplus_kwh,
    )
    manual = payload.get("manual_range") or {}
    audit = payload.get("config_audit") if isinstance(payload.get("config_audit"), dict) else {}
    raw_pairs = payload.get("explicit_pairs") or []
    pairs = tuple((float(item[0]), float(item[1])) for item in raw_pairs)
    return SweepRequest(
        request_schema_version=REQUEST_SCHEMA_VERSION,
        job_id=job_id,
        created_at_utc=str(payload.get("created_at_utc") or iso_utc()),
        software_version=str(payload.get("software_version") or __version__),
        artifact_schema_version=artifact_version,
        site_label=payload.get("site_label"),
        output_dir=Path(str(output_dir)),
        period_id=period,
        allow_unvalidated=bool(payload.get("allow_unvalidated", False)),
        acknowledge_site_boundary=bool(payload.get("acknowledge_site_boundary", False)),
        detailed_solver_output=bool(payload.get("detailed_solver_output", False)),
        fluvius_inputs=tuple(inputs),  # type: ignore[arg-type]
        defaults_path=Path(str(defaults_path)),
        defaults_sha256=str(payload.get("defaults_sha256") or ""),
        run_toml_path=None if not payload.get("run_toml_path") else Path(str(payload["run_toml_path"])),
        run_toml_sha256=payload.get("run_toml_sha256"),
        battery=battery,
        tariffs=tariffs,
        reporting=reporting,
        economics=economics,
        sweep=sweep,
        mode=parse_mode(str(payload.get("mode") or "automatic")),
        durations_hours=tuple(float(item) for item in payload.get("durations_hours") or sweep.default_durations_hours),
        candidates=candidates,
        site_analysis=site,
        removed_duplicates=tuple(payload.get("removed_duplicates") or ()),
        manual_min_power_kw=None if manual.get("min_power_kw") is None else float(manual["min_power_kw"]),
        manual_max_power_kw=None if manual.get("max_power_kw") is None else float(manual["max_power_kw"]),
        manual_power_increment_kw=(
            None if manual.get("power_increment_kw") is None else float(manual["power_increment_kw"])
        ),
        explicit_pairs=pairs,
        value_sources=dict(payload.get("value_sources") or {}),
        cli_overrides=tuple(payload.get("cli_overrides") or ()),
        config_audit=audit,
    )


def validate_frozen_sweep_inputs(request: SweepRequest) -> None:
    from btm_sim.fluvius.csv_io import sha256_file

    seen: set[str] = set()
    for item in request.fluvius_inputs:
        path = item.path
        if str(path) in seen:
            raise SweepRequestError("The three Fluvius input paths must be distinct", category="invalid_request")
        seen.add(str(path))
        if not path.exists():
            raise SweepRequestError(f"Fluvius input file not found: {path}", category="missing_input")
        actual = sha256_file(path)
        if actual != item.sha256:
            raise SweepRequestError(
                f"Fluvius file changed after the request was frozen: {item.original_name}",
                category="missing_input",
            )


def _battery_from_frozen(values: Any) -> BatteryConfig:
    if not isinstance(values, dict):
        raise SweepRequestError("Frozen request is missing battery settings", category="invalid_configuration")
    mapped = {BATTERY_TOML_KEYS.get(key, key): value for key, value in values.items()}
    return battery_from_mapping(mapped)


def _tariffs_from_frozen(values: Any) -> TariffConfig:
    if not isinstance(values, dict):
        raise SweepRequestError("Frozen request is missing tariff settings", category="invalid_configuration")
    work = dict(values)
    if "peak_start_local" in work and not isinstance(work["peak_start_local"], str):
        work["peak_start_local"] = format_hhmm(work["peak_start_local"])
    if "peak_end_local" in work and not isinstance(work["peak_end_local"], str):
        work["peak_end_local"] = format_hhmm(work["peak_end_local"])
    return tariffs_from_mapping(work)


def _reporting_from_frozen(values: Any) -> ReportingConfig:
    if not isinstance(values, dict):
        raise SweepRequestError("Frozen request is missing reporting settings", category="invalid_configuration")
    return reporting_from_mapping(values)


def _sweep_from_frozen(values: Any) -> SweepConfig:
    if not isinstance(values, dict):
        raise SweepRequestError("Frozen request is missing sweep settings", category="invalid_configuration")
    return sweep_from_mapping(values)


def _economics_from_frozen(payload: dict[str, Any], sweep: SweepConfig) -> EconomicsConfig:
    values = payload.get("economics")
    if not isinstance(values, dict):
        values = {"estimated_battery_cost_eur_per_kwh": sweep.estimated_battery_cost_eur_per_kwh}
    return economics_from_mapping(values)
