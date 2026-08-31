"""Validated dataclasses for tariffs, reporting, and a full simulation run."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.config.exceptions import ConfigError
from btm_sim.fluvius.constants import TZ_NAME

REQUIRED_BATTERY_KEYS = (
    "usable_energy_kwh",
    "charge_power_kw",
    "discharge_power_kw",
    "charge_efficiency",
    "discharge_efficiency",
)

REQUIRED_TARIFF_KEYS = (
    "customer_sale_eur_per_mwh",
    "peak_export_eur_per_mwh",
    "offpeak_export_eur_per_mwh",
    "peak_start_local",
    "peak_end_local",
    "weekends_offpeak",
    "timezone",
)

REQUIRED_REPORTING_KEYS = (
    "seasonal_plots",
    "winter_iso_week",
    "spring_iso_week",
    "summer_iso_week",
    "autumn_iso_week",
)

REQUIRED_ECONOMICS_KEYS = ("estimated_battery_cost_eur_per_kwh",)

REQUIRED_SWEEP_KEYS = (
    "evaluation_period_years",
    "default_durations_hours",
    "revenue_capture_threshold_pct",
)


def invalid_setting(source: str, dotted: str, message: str) -> ConfigError:
    if source == "defaults_toml":
        return ConfigError(f"Invalid central default `{dotted}`: {message}")
    if source == "cli":
        return ConfigError(f"Invalid command-line value for `{dotted}`: {message}")
    return ConfigError(f"Invalid run configuration `{dotted}`: {message}")


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{name} must be a finite number, got {value!r}")
    return number


def parse_hhmm(value: str, *, name: str) -> time:
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ConfigError(f"expected HH:MM, got {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"expected HH:MM, got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"expected HH:MM, got {value!r}")
    del name
    return time(hour, minute)


def format_hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


@dataclass(frozen=True)
class TariffConfig:
    """Energent PV sale and export rates. Classification uses interval start."""

    customer_sale_eur_per_mwh: float = 130.0
    peak_export_eur_per_mwh: float = 60.0
    offpeak_export_eur_per_mwh: float = 30.0
    peak_start_local: time = time(8, 0)
    peak_end_local: time = time(20, 0)
    weekends_offpeak: bool = True
    timezone: str = TZ_NAME

    def __post_init__(self) -> None:
        for name in (
            "customer_sale_eur_per_mwh",
            "peak_export_eur_per_mwh",
            "offpeak_export_eur_per_mwh",
        ):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
            if getattr(self, name) < 0:
                raise ConfigError(f"{name} must be >= 0")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unsupported timezone: {self.timezone!r}") from exc
        if self.peak_start_local >= self.peak_end_local:
            raise ConfigError(
                "peak_start_local must be earlier than peak_end_local on the same local day "
                f"(got {format_hhmm(self.peak_start_local)} to {format_hhmm(self.peak_end_local)})"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["peak_start_local"] = format_hhmm(self.peak_start_local)
        payload["peak_end_local"] = format_hhmm(self.peak_end_local)
        return payload

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class ReportingConfig:
    seasonal_plots: bool = True
    winter_iso_week: int = 3
    spring_iso_week: int = 19
    summer_iso_week: int = 26
    autumn_iso_week: int = 41

    def __post_init__(self) -> None:
        for name in ("winter_iso_week", "spring_iso_week", "summer_iso_week", "autumn_iso_week"):
            week = int(getattr(self, name))
            if not 1 <= week <= 53:
                raise ConfigError(f"{name} must be an ISO week in 1..53, got {week}")
            object.__setattr__(self, name, week)

    def season_weeks(self) -> dict[str, int]:
        return {
            "winter": self.winter_iso_week,
            "spring": self.spring_iso_week,
            "summer": self.summer_iso_week,
            "autumn": self.autumn_iso_week,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EconomicsConfig:
    """Shared battery-cost assumption for one-battery comparison and size sweep."""

    estimated_battery_cost_eur_per_kwh: float = 300.0

    def __post_init__(self) -> None:
        cost = _finite("estimated_battery_cost_eur_per_kwh", self.estimated_battery_cost_eur_per_kwh)
        if cost <= 0:
            raise ConfigError("estimated_battery_cost_eur_per_kwh must be > 0")
        object.__setattr__(self, "estimated_battery_cost_eur_per_kwh", cost)

    def to_dict(self) -> dict[str, Any]:
        return {"estimated_battery_cost_eur_per_kwh": self.estimated_battery_cost_eur_per_kwh}


@dataclass(frozen=True)
class SweepConfig:
    """Screening assumptions for the revenue battery-size sweep."""

    estimated_battery_cost_eur_per_kwh: float = 300.0
    evaluation_period_years: float = 10.0
    default_durations_hours: tuple[float, ...] = (2.0, 4.0)
    revenue_capture_threshold_pct: float = 95.0

    def __post_init__(self) -> None:
        cost = _finite("estimated_battery_cost_eur_per_kwh", self.estimated_battery_cost_eur_per_kwh)
        if cost <= 0:
            raise ConfigError("estimated_battery_cost_eur_per_kwh must be > 0")
        object.__setattr__(self, "estimated_battery_cost_eur_per_kwh", cost)
        years = _finite("evaluation_period_years", self.evaluation_period_years)
        if years <= 0:
            raise ConfigError("evaluation_period_years must be > 0")
        object.__setattr__(self, "evaluation_period_years", years)
        durations = _normalized_durations(self.default_durations_hours)
        object.__setattr__(self, "default_durations_hours", durations)
        threshold = _finite("revenue_capture_threshold_pct", self.revenue_capture_threshold_pct)
        if not 0 < threshold <= 100:
            raise ConfigError("revenue_capture_threshold_pct must be > 0 and at most 100")
        object.__setattr__(self, "revenue_capture_threshold_pct", threshold)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_battery_cost_eur_per_kwh": self.estimated_battery_cost_eur_per_kwh,
            "evaluation_period_years": self.evaluation_period_years,
            "default_durations_hours": list(self.default_durations_hours),
            "revenue_capture_threshold_pct": self.revenue_capture_threshold_pct,
        }


def _normalized_durations(raw: Any) -> tuple[float, ...]:
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        values = [_finite("default_durations_hours", part) for part in parts]
    elif isinstance(raw, (list, tuple)):
        if any(isinstance(item, (list, tuple, dict)) for item in raw):
            raise ConfigError("default_durations_hours must be a list of numbers")
        values = [_finite("default_durations_hours", item) for item in raw]
    else:
        raise ConfigError("default_durations_hours must be a list of numbers")
    if not values:
        raise ConfigError("default_durations_hours must not be empty")
    if any(value <= 0 for value in values):
        raise ConfigError("default_durations_hours values must be > 0")
    rounded = [round(value, 12) for value in values]
    if len(set(rounded)) != len(rounded):
        raise ConfigError("default_durations_hours values must be unique")
    return tuple(sorted(values))


@dataclass(frozen=True)
class SimulationConfig:
    """Resolved settings shared by the Python API, CLI, and audit folder."""

    input_parquet: Path
    battery: BatteryConfig
    tariffs: TariffConfig = field(default_factory=TariffConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    economics: EconomicsConfig = field(default_factory=EconomicsConfig)
    output_dir: Path | None = None
    output_root: Path | None = None
    validation_report: Path | None = None
    dynamic_injection_prices: Path | None = None
    require_zero_initial_charge: bool = True

    def __post_init__(self) -> None:
        if self.output_dir is not None and self.output_root is not None:
            raise ConfigError("Provide only one of output.directory and output.root")
        if self.output_dir is None and self.output_root is None:
            raise ConfigError("Provide output.directory or output.root")
        if self.require_zero_initial_charge and abs(self.battery.soc_initial_kwh) > 0.0:
            raise ConfigError(
                "The unified comparison requires battery.initial_charge_kwh = 0. "
                "A non-zero starting charge would count energy from before the "
                "selected period as additional PV. Use a standalone command if you "
                f"need another starting charge (got {self.battery.soc_initial_kwh} kWh)."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": {
                "normalized_parquet": str(self.input_parquet),
                "validation_report": None if self.validation_report is None else str(self.validation_report),
                "dynamic_injection_prices": None
                if self.dynamic_injection_prices is None
                else str(self.dynamic_injection_prices),
            },
            "output": {
                "directory": None if self.output_dir is None else str(self.output_dir),
                "root": None if self.output_root is None else str(self.output_root),
            },
            "battery": self.battery.to_dict(),
            "tariffs": self.tariffs.to_dict(),
            "reporting": self.reporting.to_dict(),
            "economics": self.economics.to_dict(),
        }


def battery_from_mapping(
    values: dict[str, Any],
    *,
    sources: dict[str, str] | None = None,
) -> BatteryConfig:
    missing = [key for key in REQUIRED_BATTERY_KEYS if key not in values]
    if missing:
        raise ConfigError("Missing battery setting(s): " + ", ".join(f"battery.{key}" for key in missing))
    try:
        return BatteryConfig(
            e_usable_kwh=_finite("usable_energy_kwh", values["usable_energy_kwh"]),
            p_charge_kw=_finite("charge_power_kw", values["charge_power_kw"]),
            p_discharge_kw=_finite("discharge_power_kw", values["discharge_power_kw"]),
            eta_charge=_finite("charge_efficiency", values["charge_efficiency"]),
            eta_discharge=_finite("discharge_efficiency", values["discharge_efficiency"]),
            soc_initial_kwh=_finite("initial_charge_kwh", values.get("initial_charge_kwh", 0.0)),
            max_equivalent_full_cycles_per_year=_finite(
                "max_equivalent_full_cycles_per_year",
                values.get("max_equivalent_full_cycles_per_year", 400.0),
            ),
        )
    except BatteryConfigError as exc:
        key = str(exc).split()[0]
        toml_key = {
            "e_usable_kwh": "usable_energy_kwh",
            "p_charge_kw": "charge_power_kw",
            "p_discharge_kw": "discharge_power_kw",
            "eta_charge": "charge_efficiency",
            "eta_discharge": "discharge_efficiency",
            "soc_initial_kwh": "initial_charge_kwh",
            "max_equivalent_full_cycles_per_year": "max_equivalent_full_cycles_per_year",
        }.get(key, key)
        source = (sources or {}).get(toml_key, "run_toml")
        raise invalid_setting(source, f"battery.{toml_key}", str(exc)) from exc
    except ConfigError as exc:
        text = str(exc)
        key = text.split()[0] if text else "battery"
        source = (sources or {}).get(key, "run_toml")
        raise invalid_setting(source, f"battery.{key}", text) from exc


def tariffs_from_mapping(
    values: dict[str, Any],
    *,
    sources: dict[str, str] | None = None,
) -> TariffConfig:
    missing = [key for key in REQUIRED_TARIFF_KEYS if key not in values]
    if missing:
        raise ConfigError("Missing tariff setting(s): " + ", ".join(f"tariffs.{key}" for key in missing))
    sources = sources or {}

    def _time(key: str) -> time:
        raw = values[key]
        if isinstance(raw, time):
            return raw
        try:
            return parse_hhmm(str(raw), name=key)
        except ConfigError as exc:
            raise invalid_setting(sources.get(key, "run_toml"), f"tariffs.{key}", str(exc)) from exc

    try:
        return TariffConfig(
            customer_sale_eur_per_mwh=values["customer_sale_eur_per_mwh"],
            peak_export_eur_per_mwh=values["peak_export_eur_per_mwh"],
            offpeak_export_eur_per_mwh=values["offpeak_export_eur_per_mwh"],
            peak_start_local=_time("peak_start_local"),
            peak_end_local=_time("peak_end_local"),
            weekends_offpeak=bool(values["weekends_offpeak"]),
            timezone=str(values["timezone"]),
        )
    except ConfigError as exc:
        text = str(exc)
        if text.startswith("Invalid "):
            raise
        key = "timezone" if "timezone" in text.lower() else "peak_start_local"
        if "must be >= 0" in text:
            key = text.split()[0]
        raise invalid_setting(sources.get(key, "run_toml"), f"tariffs.{key}", text) from exc


def reporting_from_mapping(
    values: dict[str, Any],
    *,
    sources: dict[str, str] | None = None,
) -> ReportingConfig:
    missing = [key for key in REQUIRED_REPORTING_KEYS if key not in values]
    if missing:
        raise ConfigError("Missing reporting setting(s): " + ", ".join(f"reporting.{key}" for key in missing))
    sources = sources or {}
    try:
        return ReportingConfig(
            seasonal_plots=bool(values["seasonal_plots"]),
            winter_iso_week=int(values["winter_iso_week"]),
            spring_iso_week=int(values["spring_iso_week"]),
            summer_iso_week=int(values["summer_iso_week"]),
            autumn_iso_week=int(values["autumn_iso_week"]),
        )
    except ConfigError as exc:
        text = str(exc)
        if text.startswith("Invalid "):
            raise
        key = text.split()[0] if text else "reporting"
        raise invalid_setting(sources.get(key, "run_toml"), f"reporting.{key}", text) from exc


def economics_from_mapping(
    values: dict[str, Any],
    *,
    sources: dict[str, str] | None = None,
) -> EconomicsConfig:
    missing = [key for key in REQUIRED_ECONOMICS_KEYS if key not in values]
    if missing:
        raise ConfigError(
            "Missing economics setting(s): " + ", ".join(f"economics.{key}" for key in missing)
        )
    sources = sources or {}
    try:
        return EconomicsConfig(estimated_battery_cost_eur_per_kwh=values["estimated_battery_cost_eur_per_kwh"])
    except ConfigError as exc:
        text = str(exc)
        if text.startswith("Invalid "):
            raise
        raise invalid_setting(
            sources.get("estimated_battery_cost_eur_per_kwh", "run_toml"),
            "economics.estimated_battery_cost_eur_per_kwh",
            text,
        ) from exc


def sweep_from_mapping(
    values: dict[str, Any],
    *,
    sources: dict[str, str] | None = None,
) -> SweepConfig:
    missing = [key for key in REQUIRED_SWEEP_KEYS if key not in values]
    if missing:
        raise ConfigError("Missing sweep setting(s): " + ", ".join(f"sweep.{key}" for key in missing))
    sources = sources or {}
    try:
        return SweepConfig(
            estimated_battery_cost_eur_per_kwh=values.get("estimated_battery_cost_eur_per_kwh", 300.0),
            evaluation_period_years=values["evaluation_period_years"],
            default_durations_hours=values["default_durations_hours"],
            revenue_capture_threshold_pct=values["revenue_capture_threshold_pct"],
        )
    except ConfigError as exc:
        text = str(exc)
        if text.startswith("Invalid "):
            raise
        key = text.split()[0] if text else "sweep"
        if key not in REQUIRED_SWEEP_KEYS:
            if "durations" in text:
                key = "default_durations_hours"
            elif "cost" in text:
                key = "estimated_battery_cost_eur_per_kwh"
            elif "evaluation" in text:
                key = "evaluation_period_years"
            elif "threshold" in text:
                key = "revenue_capture_threshold_pct"
        raise invalid_setting(sources.get(key, "run_toml"), f"sweep.{key}", text) from exc
