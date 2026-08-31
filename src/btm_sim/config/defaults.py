"""Locate and load the project's central defaults TOML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from btm_sim.battery.config import BatteryConfig
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.schema import (
    EconomicsConfig,
    ReportingConfig,
    SweepConfig,
    TariffConfig,
    battery_from_mapping,
    economics_from_mapping,
    reporting_from_mapping,
    sweep_from_mapping,
    tariffs_from_mapping,
)
from btm_sim.fluvius.csv_io import sha256_file

SOURCE_DEFAULTS_TOML = "defaults_toml"
SOURCE_RUN_TOML = "run_toml"
SOURCE_CLI = "cli"

DEFAULTS_FILENAME = "defaults.toml"

ALLOWED_DEFAULTS_SECTIONS = {
    "battery": {
        "usable_energy_kwh",
        "charge_power_kw",
        "discharge_power_kw",
        "charge_efficiency",
        "discharge_efficiency",
        "initial_charge_kwh",
        "max_equivalent_full_cycles_per_year",
    },
    "tariffs": {
        "customer_sale_eur_per_mwh",
        "peak_export_eur_per_mwh",
        "offpeak_export_eur_per_mwh",
        "peak_start_local",
        "peak_end_local",
        "weekends_offpeak",
        "timezone",
    },
    "reporting": {
        "seasonal_plots",
        "winter_iso_week",
        "spring_iso_week",
        "summer_iso_week",
        "autumn_iso_week",
    },
    "economics": {
        "estimated_battery_cost_eur_per_kwh",
    },
    "sweep": {
        "evaluation_period_years",
        "default_durations_hours",
        "revenue_capture_threshold_pct",
    },
}

REQUIRED_DEFAULTS_KEYS = {
    section: frozenset(keys) for section, keys in ALLOWED_DEFAULTS_SECTIONS.items()
}

ALLOWED_RUN_SECTIONS = {
    **ALLOWED_DEFAULTS_SECTIONS,
    "input": {"normalized_parquet", "validation_report", "dynamic_injection_prices"},
    "output": {"root", "directory"},
    "sweep": ALLOWED_DEFAULTS_SECTIONS["sweep"] | {"estimated_battery_cost_eur_per_kwh"},
}


@dataclass(frozen=True)
class CentralDefaults:
    """Validated reusable settings from a central defaults file."""

    battery: BatteryConfig
    tariffs: TariffConfig
    reporting: ReportingConfig
    economics: EconomicsConfig
    sweep: SweepConfig
    path: Path
    sha256: str

    def payload(self) -> dict[str, dict[str, Any]]:
        tariffs = self.tariffs.to_dict()
        return {
            "battery": {
                "usable_energy_kwh": self.battery.e_usable_kwh,
                "charge_power_kw": self.battery.p_charge_kw,
                "discharge_power_kw": self.battery.p_discharge_kw,
                "charge_efficiency": self.battery.eta_charge,
                "discharge_efficiency": self.battery.eta_discharge,
                "initial_charge_kwh": self.battery.soc_initial_kwh,
                "max_equivalent_full_cycles_per_year": self.battery.max_equivalent_full_cycles_per_year,
            },
            "tariffs": tariffs,
            "reporting": self.reporting.to_dict(),
            "economics": self.economics.to_dict(),
            "sweep": {
                "evaluation_period_years": self.sweep.evaluation_period_years,
                "default_durations_hours": list(self.sweep.default_durations_hours),
                "revenue_capture_threshold_pct": self.sweep.revenue_capture_threshold_pct,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "battery": self.battery.to_dict(),
            "tariffs": self.tariffs.to_dict(),
            "reporting": self.reporting.to_dict(),
            "economics": self.economics.to_dict(),
            "sweep": self.sweep.to_dict(),
        }


def standard_defaults_path() -> Path:
    """Return the project's ``configs/defaults.toml``, independent of cwd."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "configs" / DEFAULTS_FILENAME
        if candidate.is_file():
            return candidate
    raise ConfigError(
        "Central defaults file not found: looked for configs/"
        f"{DEFAULTS_FILENAME} from the application location ({here})"
    )


def resolve_defaults_path(path: Path | str | None, *, cwd: Path | None = None) -> Path:
    """Resolve an explicit defaults path against cwd, or the standard file."""
    if path is None:
        return standard_defaults_path()
    resolved = Path(path)
    if not resolved.is_absolute():
        base = Path.cwd() if cwd is None else Path(cwd)
        resolved = (base / resolved).resolve()
    return resolved


def load_central_defaults(path: Path | str | None = None, *, cwd: Path | None = None) -> CentralDefaults:
    """Load and validate central defaults without requiring input or output paths."""
    defaults_path = resolve_defaults_path(path, cwd=cwd)
    if not defaults_path.exists():
        raise ConfigError(f"Central defaults file not found: {defaults_path}")
    payload = read_toml_file(defaults_path, kind="defaults")
    _require_complete_defaults(payload, defaults_path)
    sources = {
        section: {key: SOURCE_DEFAULTS_TOML for key in body} for section, body in payload.items()
    }
    try:
        battery = battery_from_mapping(payload["battery"], sources=sources.get("battery"))
        tariffs = tariffs_from_mapping(payload["tariffs"], sources=sources.get("tariffs"))
        reporting = reporting_from_mapping(payload["reporting"], sources=sources.get("reporting"))
        economics = economics_from_mapping(payload["economics"], sources=sources.get("economics"))
        sweep_payload = dict(payload["sweep"])
        sweep_payload.setdefault(
            "estimated_battery_cost_eur_per_kwh",
            economics.estimated_battery_cost_eur_per_kwh,
        )
        sweep = sweep_from_mapping(sweep_payload, sources=sources.get("sweep"))
    except ConfigError as exc:
        raise _prefixed_defaults_error(exc) from exc
    return CentralDefaults(
        battery=battery,
        tariffs=tariffs,
        reporting=reporting,
        economics=economics,
        sweep=sweep,
        path=defaults_path,
        sha256=sha256_file(defaults_path),
    )


def read_toml_file(path: Path, *, kind: str) -> dict[str, Any]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        label = "central defaults file" if kind == "defaults" else "run configuration file"
        raise ConfigError(f"Invalid TOML in {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"TOML root must be a table: {path}")
    if kind == "defaults":
        if "input" in payload or "output" in payload:
            raise ConfigError(
                f"Central defaults file must not define [input] or [output]: {path}. "
                "Site paths belong in the run configuration or on the command line."
            )
        allowed = ALLOWED_DEFAULTS_SECTIONS
        file_label = "central defaults file"
    else:
        allowed = ALLOWED_RUN_SECTIONS
        file_label = "run configuration file"
    _reject_unknown_keys(payload, allowed, path=path, file_label=file_label)
    return payload


def _require_complete_defaults(payload: dict[str, Any], path: Path) -> None:
    missing: list[str] = []
    for section, keys in REQUIRED_DEFAULTS_KEYS.items():
        if section not in payload:
            missing.extend(f"{section}.{key}" for key in sorted(keys))
            continue
        body = payload[section]
        missing.extend(f"{section}.{key}" for key in sorted(keys - set(body)))
    if missing:
        raise ConfigError(
            f"Central defaults file {path} is missing required setting(s): " + ", ".join(missing)
        )


def _reject_unknown_keys(
    payload: dict[str, Any],
    allowed: dict[str, set[str]],
    *,
    path: Path,
    file_label: str,
) -> None:
    for section, body in payload.items():
        if section not in allowed:
            raise ConfigError(f"Unknown section [{section}] in {file_label} {path}")
        if not isinstance(body, dict):
            raise ConfigError(f"Section [{section}] in {file_label} {path} must be a table")
        unknown = set(body) - allowed[section]
        if unknown:
            raise ConfigError(
                f"Unknown key(s) in [{section}] of {file_label} {path}: " + ", ".join(sorted(unknown))
            )


def _prefixed_defaults_error(exc: ConfigError) -> ConfigError:
    text = str(exc)
    if text.startswith("Invalid central default"):
        return exc
    return ConfigError(f"Invalid central default: {text}")
