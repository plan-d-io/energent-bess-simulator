"""Load central defaults, merge a run TOML and CLI values, and validate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from btm_sim.config.defaults import (
    SOURCE_CLI,
    SOURCE_DEFAULTS_TOML,
    SOURCE_RUN_TOML,
    load_central_defaults,
    read_toml_file,
)
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.schema import (
    SimulationConfig,
    battery_from_mapping,
    economics_from_mapping,
    reporting_from_mapping,
    sweep_from_mapping,
    tariffs_from_mapping,
)
from btm_sim.fluvius.csv_io import sha256_file


def load_toml(path: Path) -> dict[str, Any]:
    """Load and validate a run-configuration TOML file."""
    return read_toml_file(Path(path), kind="run")


def resolve_simulation_config(
    *,
    toml_path: Path | None = None,
    cli: dict[str, Any] | None = None,
    cwd: Path | None = None,
    require_zero_initial_charge: bool = True,
    defaults_path: Path | str | None = None,
) -> tuple[SimulationConfig, dict[str, Any]]:
    """Return (config, audit). Precedence: CLI > run TOML > central defaults."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    cli = {key: value for key, value in (cli or {}).items() if value is not None}
    defaults = load_central_defaults(defaults_path, cwd=cwd)

    run_payload: dict[str, Any] = {}
    run_base: Path | None = None
    resolved_run_path: Path | None = None
    if toml_path is not None:
        resolved_run_path = Path(toml_path)
        if not resolved_run_path.exists():
            raise ConfigError(f"Run configuration file not found: {resolved_run_path}")
        run_payload = load_toml(resolved_run_path)
        run_base = resolved_run_path.parent

    merged, sources = _merge(defaults.payload(), run_payload, cli)
    if "power" in cli:
        merged.setdefault("battery", {})
        sources.setdefault("battery", {})
        if sources["battery"].get("charge_power_kw") != SOURCE_CLI:
            merged["battery"]["charge_power_kw"] = cli["power"]
            sources["battery"]["charge_power_kw"] = SOURCE_CLI
        if sources["battery"].get("discharge_power_kw") != SOURCE_CLI:
            merged["battery"]["discharge_power_kw"] = cli["power"]
            sources["battery"]["discharge_power_kw"] = SOURCE_CLI

    input_section = merged.get("input", {})
    output_section = merged.get("output", {})
    battery_section = merged.get("battery", {})
    tariff_section = merged.get("tariffs", {})
    reporting_section = merged.get("reporting", {})

    parquet = _required_path(
        input_section,
        "normalized_parquet",
        toml_base=run_base,
        cwd=cwd,
        from_cli="input" in cli,
    )
    if not parquet.exists():
        raise ConfigError(f"Input parquet not found: {parquet}")
    validation = _optional_path(
        output_or_input_section=input_section,
        key="validation_report",
        toml_base=run_base,
        cwd=cwd,
        from_cli="validation_report" in cli,
    )
    if validation is not None and not validation.exists():
        raise ConfigError(f"Validation report not found: {validation}")
    prices_path = _optional_path(
        output_or_input_section=input_section,
        key="dynamic_injection_prices",
        toml_base=run_base,
        cwd=cwd,
        from_cli="dynamic_injection_prices" in cli,
    )
    if prices_path is not None and not prices_path.exists():
        raise ConfigError(f"Dynamic injection price file not found: {prices_path}")
    output_dir = _optional_path(
        output_or_input_section=output_section,
        key="directory",
        toml_base=run_base,
        cwd=cwd,
        from_cli="output_dir" in cli,
    )
    output_root = _optional_path(
        output_or_input_section=output_section,
        key="root",
        toml_base=run_base,
        cwd=cwd,
        from_cli="output_root" in cli,
    )

    battery = battery_from_mapping(battery_section, sources=sources.get("battery"))
    tariffs = tariffs_from_mapping(tariff_section, sources=sources.get("tariffs"))
    reporting = reporting_from_mapping(reporting_section, sources=sources.get("reporting"))
    economics = economics_from_mapping(merged.get("economics", {}), sources=sources.get("economics"))
    sweep = sweep_from_mapping(merged.get("sweep", {}), sources=sources.get("sweep"))
    config = SimulationConfig(
        input_parquet=parquet,
        validation_report=validation,
        dynamic_injection_prices=prices_path,
        output_dir=output_dir,
        output_root=output_root,
        battery=battery,
        tariffs=tariffs,
        reporting=reporting,
        economics=economics,
        require_zero_initial_charge=require_zero_initial_charge,
    )
    audit = {
        "defaults_path": str(defaults.path),
        "defaults_sha256": defaults.sha256,
        "toml_path": None if resolved_run_path is None else str(resolved_run_path.resolve()),
        "toml_sha256": None if resolved_run_path is None else sha256_file(resolved_run_path),
        "run_toml_path": None if resolved_run_path is None else str(resolved_run_path.resolve()),
        "run_toml_sha256": None if resolved_run_path is None else sha256_file(resolved_run_path),
        "cli_overrides": sorted(cli.keys()),
        "value_sources": sources,
        "resolved": {**config.to_dict(), "sweep": sweep.to_dict()},
        "economics": economics,
        "sweep": sweep,
    }
    return config, audit


def resolve_reusable_settings(
    *,
    toml_path: Path | None = None,
    cli: dict[str, Any] | None = None,
    cwd: Path | None = None,
    defaults_path: Path | str | None = None,
    require_zero_initial_charge: bool = True,
) -> dict[str, Any]:
    """Resolve battery, tariff, reporting, and optional price/output paths.

    Unlike :func:`resolve_simulation_config`, this does not require a
    normalized parquet. End-to-end runs use it before Fluvius ingestion.
    """
    cwd = Path.cwd() if cwd is None else Path(cwd)
    cli = {key: value for key, value in (cli or {}).items() if value is not None}
    defaults = load_central_defaults(defaults_path, cwd=cwd)

    run_payload: dict[str, Any] = {}
    run_base: Path | None = None
    resolved_run_path: Path | None = None
    if toml_path is not None:
        resolved_run_path = Path(toml_path)
        if not resolved_run_path.exists():
            raise ConfigError(f"Run configuration file not found: {resolved_run_path}")
        run_payload = load_toml(resolved_run_path)
        run_base = resolved_run_path.parent

    merged, sources = _merge(defaults.payload(), run_payload, cli)
    if "power" in cli:
        merged.setdefault("battery", {})
        sources.setdefault("battery", {})
        if sources["battery"].get("charge_power_kw") != SOURCE_CLI:
            merged["battery"]["charge_power_kw"] = cli["power"]
            sources["battery"]["charge_power_kw"] = SOURCE_CLI
        if sources["battery"].get("discharge_power_kw") != SOURCE_CLI:
            merged["battery"]["discharge_power_kw"] = cli["power"]
            sources["battery"]["discharge_power_kw"] = SOURCE_CLI

    battery_section = merged.get("battery", {})
    tariff_section = merged.get("tariffs", {})
    reporting_section = merged.get("reporting", {})
    output_section = merged.get("output", {})
    input_section = merged.get("input", {})

    prices_path = _optional_path(
        output_or_input_section=input_section,
        key="dynamic_injection_prices",
        toml_base=run_base,
        cwd=cwd,
        from_cli="dynamic_injection_prices" in cli,
    )
    if prices_path is not None and not prices_path.exists():
        raise ConfigError(f"Dynamic injection price file not found: {prices_path}")
    output_dir = _optional_path(
        output_or_input_section=output_section,
        key="directory",
        toml_base=run_base,
        cwd=cwd,
        from_cli="output_dir" in cli,
    )
    output_root = _optional_path(
        output_or_input_section=output_section,
        key="root",
        toml_base=run_base,
        cwd=cwd,
        from_cli="output_root" in cli,
    )
    battery = battery_from_mapping(battery_section, sources=sources.get("battery"))
    tariffs = tariffs_from_mapping(tariff_section, sources=sources.get("tariffs"))
    reporting = reporting_from_mapping(reporting_section, sources=sources.get("reporting"))
    economics = economics_from_mapping(merged.get("economics", {}), sources=sources.get("economics"))
    sweep = sweep_from_mapping(merged.get("sweep", {}), sources=sources.get("sweep"))
    if require_zero_initial_charge and abs(battery.soc_initial_kwh) > 0.0:
        raise ConfigError(
            "The unified comparison requires battery.initial_charge_kwh = 0. "
            "A non-zero starting charge would count energy from before the "
            "selected period as additional PV. Use a standalone command if you "
            f"need another starting charge (got {battery.soc_initial_kwh} kWh)."
        )
    audit = {
        "defaults_path": str(defaults.path),
        "defaults_sha256": defaults.sha256,
        "toml_path": None if resolved_run_path is None else str(resolved_run_path.resolve()),
        "toml_sha256": None if resolved_run_path is None else sha256_file(resolved_run_path),
        "run_toml_path": None if resolved_run_path is None else str(resolved_run_path.resolve()),
        "run_toml_sha256": None if resolved_run_path is None else sha256_file(resolved_run_path),
        "cli_overrides": sorted(cli.keys()),
        "value_sources": sources,
        "resolved": {
            "input": {
                "normalized_parquet": None,
                "validation_report": None,
                "dynamic_injection_prices": None if prices_path is None else str(prices_path),
            },
            "output": {
                "directory": None if output_dir is None else str(output_dir),
                "root": None if output_root is None else str(output_root),
            },
            "battery": battery.to_dict(),
            "tariffs": tariffs.to_dict(),
            "reporting": reporting.to_dict(),
            "economics": economics.to_dict(),
            "sweep": sweep.to_dict(),
        },
        "battery": battery,
        "tariffs": tariffs,
        "reporting": reporting,
        "economics": economics,
        "sweep": sweep,
        "dynamic_injection_prices": prices_path,
        "output_dir": output_dir,
        "output_root": output_root,
    }
    return audit


def _merge(
    defaults_payload: dict[str, Any],
    run_payload: dict[str, Any],
    cli: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    merged: dict[str, Any] = {section: dict(body) for section, body in defaults_payload.items()}
    sources: dict[str, dict[str, str]] = {
        section: {key: SOURCE_DEFAULTS_TOML for key in body} for section, body in defaults_payload.items()
    }
    for section, body in run_payload.items():
        merged.setdefault(section, {})
        sources.setdefault(section, {})
        for key, value in body.items():
            merged[section][key] = value
            sources[section][key] = SOURCE_RUN_TOML

    cli_map = {
        "validation_report": ("input", "validation_report"),
        "dynamic_injection_prices": ("input", "dynamic_injection_prices"),
        "output_dir": ("output", "directory"),
        "output_root": ("output", "root"),
        "e_usable": ("battery", "usable_energy_kwh"),
        "p_charge": ("battery", "charge_power_kw"),
        "p_discharge": ("battery", "discharge_power_kw"),
        "eta_charge": ("battery", "charge_efficiency"),
        "eta_discharge": ("battery", "discharge_efficiency"),
        "soc_initial": ("battery", "initial_charge_kwh"),
        "max_equivalent_full_cycles_per_year": ("battery", "max_equivalent_full_cycles_per_year"),
        "customer_rate": ("tariffs", "customer_sale_eur_per_mwh"),
        "export_peak_rate": ("tariffs", "peak_export_eur_per_mwh"),
        "export_offpeak_rate": ("tariffs", "offpeak_export_eur_per_mwh"),
        "peak_start": ("tariffs", "peak_start_local"),
        "peak_end": ("tariffs", "peak_end_local"),
        "weekends_offpeak": ("tariffs", "weekends_offpeak"),
        "timezone": ("tariffs", "timezone"),
        "seasonal_plots": ("reporting", "seasonal_plots"),
        "winter_iso_week": ("reporting", "winter_iso_week"),
        "spring_iso_week": ("reporting", "spring_iso_week"),
        "summer_iso_week": ("reporting", "summer_iso_week"),
        "autumn_iso_week": ("reporting", "autumn_iso_week"),
        "estimated_battery_cost_eur_per_kwh": ("economics", "estimated_battery_cost_eur_per_kwh"),
        "evaluation_period_years": ("sweep", "evaluation_period_years"),
        "default_durations_hours": ("sweep", "default_durations_hours"),
        "revenue_capture_threshold_pct": ("sweep", "revenue_capture_threshold_pct"),
    }
    if "input" in cli:
        merged.setdefault("input", {})["normalized_parquet"] = cli["input"]
        sources.setdefault("input", {})["normalized_parquet"] = SOURCE_CLI
    for cli_key, target in cli_map.items():
        if cli_key not in cli:
            continue
        section, key = target
        merged.setdefault(section, {})[key] = cli[cli_key]
        sources.setdefault(section, {})[key] = SOURCE_CLI
    if "output_dir" in cli:
        merged.setdefault("output", {}).pop("root", None)
        sources.setdefault("output", {}).pop("root", None)
    if "output_root" in cli:
        merged.setdefault("output", {}).pop("directory", None)
        sources.setdefault("output", {}).pop("directory", None)
    _reconcile_economics_cost(merged, sources, run_payload)
    return merged, sources


_COST_KEY = "estimated_battery_cost_eur_per_kwh"


def _reconcile_economics_cost(
    merged: dict[str, Any],
    sources: dict[str, dict[str, str]],
    run_payload: dict[str, Any],
) -> None:
    """Treat [sweep] cost as a deprecated alias of the shared [economics] cost."""
    run_economics = (run_payload.get("economics") or {}).get(_COST_KEY)
    run_alias = (run_payload.get("sweep") or {}).get(_COST_KEY)
    if run_economics is not None and run_alias is not None and float(run_economics) != float(run_alias):
        raise ConfigError(
            "Conflicting estimated_battery_cost_eur_per_kwh values: "
            f"economics.estimated_battery_cost_eur_per_kwh={run_economics!r} and "
            f"sweep.estimated_battery_cost_eur_per_kwh={run_alias!r}. "
            "Supply the cost once, or use the same value in both places."
        )
    economics = merged.setdefault("economics", {})
    economics_sources = sources.setdefault("economics", {})
    sweep = merged.setdefault("sweep", {})
    sweep_sources = sources.setdefault("sweep", {})
    if run_alias is not None and economics_sources.get(_COST_KEY) == SOURCE_DEFAULTS_TOML:
        economics[_COST_KEY] = run_alias
        economics_sources[_COST_KEY] = SOURCE_RUN_TOML
    if _COST_KEY in economics:
        sweep[_COST_KEY] = economics[_COST_KEY]
        sweep_sources[_COST_KEY] = economics_sources[_COST_KEY]


def _required_path(
    section: dict[str, Any],
    key: str,
    *,
    toml_base: Path | None,
    cwd: Path,
    from_cli: bool,
) -> Path:
    if key not in section:
        raise ConfigError(f"input.{key} is required")
    return _resolve_path(section[key], toml_base=toml_base, cwd=cwd, from_cli=from_cli)


def _optional_path(
    *,
    output_or_input_section: dict[str, Any],
    key: str,
    toml_base: Path | None,
    cwd: Path,
    from_cli: bool,
) -> Path | None:
    if key not in output_or_input_section or output_or_input_section[key] in (None, ""):
        return None
    return _resolve_path(
        output_or_input_section[key],
        toml_base=toml_base,
        cwd=cwd,
        from_cli=from_cli,
    )


def _resolve_path(value: Path | str, *, toml_base: Path | None, cwd: Path, from_cli: bool) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    base = cwd if from_cli or toml_base is None else toml_base
    return (base / path).resolve()
