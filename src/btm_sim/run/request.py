"""Typed end-to-end run request: builder, serializer, and reload validation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from btm_sim import __version__
from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.compare.artifacts import ARTIFACT_SCHEMA_VERSION
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_reusable_settings
from btm_sim.config.schema import (
    EconomicsConfig,
    ReportingConfig,
    TariffConfig,
    battery_from_mapping,
    economics_from_mapping,
    format_hhmm,
    reporting_from_mapping,
    tariffs_from_mapping,
)
from btm_sim.fluvius.csv_io import sha256_file
from btm_sim.progress import REQUEST_SCHEMA_VERSION, iso_utc
from btm_sim.run.exceptions import RunRequestError

BATTERY_TOML_KEYS = {
    "e_usable_kwh": "usable_energy_kwh",
    "p_charge_kw": "charge_power_kw",
    "p_discharge_kw": "discharge_power_kw",
    "eta_charge": "charge_efficiency",
    "eta_discharge": "discharge_efficiency",
    "soc_initial_kwh": "initial_charge_kwh",
    "max_equivalent_full_cycles_per_year": "max_equivalent_full_cycles_per_year",
}


@dataclass(frozen=True)
class FluviusInputRef:
    path: Path
    original_name: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "original_name": self.original_name,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class EndToEndRunRequest:
    """Frozen, already-resolved request for one end-to-end comparison."""

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
    dynamic_injection_prices: Path | None
    prices_override: bool
    defaults_path: Path
    defaults_sha256: str
    run_toml_path: Path | None
    run_toml_sha256: str | None
    battery: BatteryConfig
    tariffs: TariffConfig
    reporting: ReportingConfig
    economics: EconomicsConfig
    value_sources: dict[str, dict[str, str]]
    cli_overrides: tuple[str, ...]
    config_audit: dict[str, Any]

    def fluvius_paths(self) -> tuple[Path, Path, Path]:
        return tuple(item.path for item in self.fluvius_inputs)  # type: ignore[return-value]


def new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"btm-{stamp}-{uuid.uuid4().hex[:8]}"


def _resolve_user_path(value: str | Path, *, cwd: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    else:
        path = path.resolve()
    return path


def _fluvius_ref(path: Path) -> FluviusInputRef:
    if not path.exists():
        raise RunRequestError(f"Fluvius input file not found: {path}", category="missing_input")
    if not path.is_file():
        raise RunRequestError(f"Fluvius input is not a file: {path}", category="missing_input")
    return FluviusInputRef(path=path, original_name=path.name, sha256=sha256_file(path))


def build_run_request(
    *,
    fluvius_paths: Sequence[str | Path],
    period_id: str,
    output_dir: str | Path,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
    site_label: str | None = None,
    defaults_path: str | Path | None = None,
    run_toml_path: str | Path | None = None,
    dynamic_injection_prices: str | Path | None = None,
    detailed_solver_output: bool = False,
    cli: dict[str, Any] | None = None,
    job_id: str | None = None,
    cwd: Path | None = None,
) -> EndToEndRunRequest:
    """Build and validate a frozen request from live inputs and config layers.

    Precedence for ordinary construction: explicit ``cli`` values, then the run
    TOML, then ``configs/defaults.toml`` (or ``defaults_path``).
    """
    cwd = Path.cwd() if cwd is None else Path(cwd)
    paths = [_resolve_user_path(path, cwd=cwd) for path in fluvius_paths]
    if len(paths) != 3:
        raise RunRequestError(
            f"Provide exactly three Fluvius CSV exports, got {len(paths)}",
            category="invalid_request",
        )
    if len({str(path) for path in paths}) != 3:
        raise RunRequestError("The three Fluvius input paths must be distinct", category="invalid_request")
    period = str(period_id).strip()
    if not period:
        raise RunRequestError("A selected period ID is required", category="invalid_period")
    out = _resolve_user_path(output_dir, cwd=cwd)
    resolved_cli = {key: value for key, value in (cli or {}).items() if value is not None}
    if dynamic_injection_prices is not None:
        resolved_cli["dynamic_injection_prices"] = str(
            _resolve_user_path(dynamic_injection_prices, cwd=cwd)
        )
    resolved_cli["output_dir"] = str(out)
    resolved_cli.pop("output_root", None)
    try:
        audit = resolve_reusable_settings(
            toml_path=None if run_toml_path is None else _resolve_user_path(run_toml_path, cwd=cwd),
            cli=resolved_cli,
            cwd=cwd,
            defaults_path=defaults_path,
            require_zero_initial_charge=True,
        )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc

    inputs = tuple(_fluvius_ref(path) for path in paths)
    prices = audit["dynamic_injection_prices"]
    prices_override = prices is not None
    return EndToEndRunRequest(
        request_schema_version=REQUEST_SCHEMA_VERSION,
        job_id=job_id or new_job_id(),
        created_at_utc=iso_utc(),
        software_version=__version__,
        artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
        site_label=None if site_label is None or not str(site_label).strip() else str(site_label).strip(),
        output_dir=out,
        period_id=period,
        allow_unvalidated=bool(allow_unvalidated),
        acknowledge_site_boundary=bool(acknowledge_site_boundary),
        detailed_solver_output=bool(detailed_solver_output),
        fluvius_inputs=inputs,  # type: ignore[arg-type]
        dynamic_injection_prices=prices,
        prices_override=prices_override,
        defaults_path=Path(audit["defaults_path"]),
        defaults_sha256=str(audit["defaults_sha256"]),
        run_toml_path=None if audit["run_toml_path"] is None else Path(audit["run_toml_path"]),
        run_toml_sha256=audit["run_toml_sha256"],
        battery=audit["battery"],
        tariffs=audit["tariffs"],
        reporting=audit["reporting"],
        economics=audit["economics"],
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


def serialize_run_request(request: EndToEndRunRequest) -> dict[str, Any]:
    """Return the machine-readable frozen request; callers should not rebuild it by hand."""
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
        "prices": {
            "path": None if request.dynamic_injection_prices is None else str(request.dynamic_injection_prices),
            "override": request.prices_override,
        },
        "defaults_path": str(request.defaults_path),
        "defaults_sha256": request.defaults_sha256,
        "run_toml_path": None if request.run_toml_path is None else str(request.run_toml_path),
        "run_toml_sha256": request.run_toml_sha256,
        "battery": request.battery.to_dict(),
        "tariffs": request.tariffs.to_dict(),
        "reporting": request.reporting.to_dict(),
        "economics": request.economics.to_dict(),
        "value_sources": request.value_sources,
        "cli_overrides": list(request.cli_overrides),
        "config_audit": request.config_audit,
    }


def write_run_request(request: EndToEndRunRequest, path: str | Path | None = None) -> Path:
    """Write the frozen request JSON. Default: ``<output_dir>/run_request.json``."""
    target = Path(path) if path is not None else request.output_dir / "run_request.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(serialize_run_request(request), indent=2) + "\n", encoding="utf-8")
    return target


def load_run_request(path: str | Path) -> EndToEndRunRequest:
    """Reload and re-validate a frozen request. Does not re-merge ``defaults.toml``."""
    request_path = Path(path)
    if not request_path.exists():
        raise RunRequestError(f"Run request file not found: {request_path}", category="invalid_request")
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunRequestError(f"Run request is not valid JSON: {exc}", category="invalid_request") from exc
    if not isinstance(payload, dict):
        raise RunRequestError("Run request JSON root must be an object", category="invalid_request")
    return request_from_payload(payload)


def request_from_payload(payload: dict[str, Any]) -> EndToEndRunRequest:
    """Validate frozen values in the worker process without rereading defaults."""
    version = payload.get("request_schema_version")
    if version != REQUEST_SCHEMA_VERSION:
        raise RunRequestError(
            f"Unsupported request_schema_version {version!r}; expected {REQUEST_SCHEMA_VERSION}",
            category="invalid_request",
        )
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise RunRequestError("Frozen request is missing job_id", category="invalid_request")
    period = str(payload.get("period_id") or "").strip()
    if not period:
        raise RunRequestError("Frozen request is missing period_id", category="invalid_period")
    output_dir = payload.get("output_dir")
    if not output_dir:
        raise RunRequestError("Frozen request is missing output_dir", category="invalid_request")
    raw_inputs = payload.get("fluvius_inputs")
    if not isinstance(raw_inputs, list) or len(raw_inputs) != 3:
        raise RunRequestError("Frozen request must list exactly three Fluvius inputs", category="invalid_request")
    inputs: list[FluviusInputRef] = []
    for item in raw_inputs:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise RunRequestError("Each Fluvius input needs path and sha256", category="invalid_request")
        path = Path(str(item["path"]))
        inputs.append(
            FluviusInputRef(
                path=path,
                original_name=str(item.get("original_name") or path.name),
                sha256=str(item["sha256"]),
            )
        )
    prices = payload.get("prices") or {}
    prices_path = prices.get("path")
    try:
        battery = _battery_from_frozen(payload.get("battery"))
        tariffs = _tariffs_from_frozen(payload.get("tariffs"))
        reporting = _reporting_from_frozen(payload.get("reporting"))
        economics = _economics_from_frozen(payload)
    except (ConfigError, BatteryConfigError, RunRequestError) as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc
    if abs(battery.soc_initial_kwh) > 0.0:
        raise RunRequestError(
            "The unified comparison requires battery.initial_charge_kwh = 0",
            category="invalid_configuration",
        )
    defaults_path = payload.get("defaults_path")
    if not defaults_path:
        raise RunRequestError("Frozen request is missing defaults_path", category="invalid_request")
    artifact_version = int(payload.get("artifact_schema_version") or ARTIFACT_SCHEMA_VERSION)
    if artifact_version != ARTIFACT_SCHEMA_VERSION:
        raise RunRequestError(
            f"Frozen request artifact_schema_version {artifact_version} is not {ARTIFACT_SCHEMA_VERSION}",
            category="invalid_request",
        )
    audit = payload.get("config_audit") if isinstance(payload.get("config_audit"), dict) else {}
    return EndToEndRunRequest(
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
        dynamic_injection_prices=None if not prices_path else Path(str(prices_path)),
        prices_override=bool(prices.get("override", prices_path is not None)),
        defaults_path=Path(str(defaults_path)),
        defaults_sha256=str(payload.get("defaults_sha256") or ""),
        run_toml_path=None if not payload.get("run_toml_path") else Path(str(payload["run_toml_path"])),
        run_toml_sha256=payload.get("run_toml_sha256"),
        battery=battery,
        tariffs=tariffs,
        reporting=reporting,
        economics=economics,
        value_sources=dict(payload.get("value_sources") or {}),
        cli_overrides=tuple(payload.get("cli_overrides") or ()),
        config_audit=audit,
    )


def validate_frozen_inputs(request: EndToEndRunRequest) -> None:
    """Re-read source files at execution time and refuse changed or missing inputs."""
    seen: set[str] = set()
    for item in request.fluvius_inputs:
        path = item.path
        if str(path) in seen:
            raise RunRequestError("The three Fluvius input paths must be distinct", category="invalid_request")
        seen.add(str(path))
        if not path.exists():
            raise RunRequestError(f"Fluvius input file not found: {path}", category="missing_input")
        actual = sha256_file(path)
        if actual != item.sha256:
            raise RunRequestError(
                f"Fluvius file changed after the request was frozen: {item.original_name}",
                category="missing_input",
            )
    if request.dynamic_injection_prices is not None and not request.dynamic_injection_prices.exists():
        raise RunRequestError(
            f"Dynamic injection price file not found: {request.dynamic_injection_prices}",
            category="price_coverage",
        )


def _battery_from_frozen(values: Any) -> BatteryConfig:
    if not isinstance(values, dict):
        raise RunRequestError("Frozen request is missing battery settings", category="invalid_configuration")
    mapped = {BATTERY_TOML_KEYS.get(key, key): value for key, value in values.items()}
    return battery_from_mapping(mapped)


def _tariffs_from_frozen(values: Any) -> TariffConfig:
    if not isinstance(values, dict):
        raise RunRequestError("Frozen request is missing tariff settings", category="invalid_configuration")
    work = dict(values)
    if "peak_start_local" in work and not isinstance(work["peak_start_local"], str):
        work["peak_start_local"] = format_hhmm(work["peak_start_local"])
    if "peak_end_local" in work and not isinstance(work["peak_end_local"], str):
        work["peak_end_local"] = format_hhmm(work["peak_end_local"])
    return tariffs_from_mapping(work)


def _reporting_from_frozen(values: Any) -> ReportingConfig:
    if not isinstance(values, dict):
        raise RunRequestError("Frozen request is missing reporting settings", category="invalid_configuration")
    return reporting_from_mapping(values)


def _economics_from_frozen(payload: dict[str, Any]) -> EconomicsConfig:
    values = payload.get("economics")
    if not isinstance(values, dict):
        audit = payload.get("config_audit") if isinstance(payload.get("config_audit"), dict) else {}
        resolved = audit.get("resolved") if isinstance(audit.get("resolved"), dict) else {}
        values = resolved.get("economics")
        if not isinstance(values, dict):
            sweep = resolved.get("sweep") if isinstance(resolved.get("sweep"), dict) else {}
            cost = sweep.get("estimated_battery_cost_eur_per_kwh", 300.0)
            values = {"estimated_battery_cost_eur_per_kwh": cost}
    return economics_from_mapping(values)
