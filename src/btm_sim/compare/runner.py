"""Run all six dispatch cases and write one auditable comparison directory."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from btm_sim.optimizer.backend import OptimizerBackend

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.dispatch import run_reference_controller
from btm_sim.battery.physics import check_dispatch_feasibility
from btm_sim.compare.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    build_comparison_dispatch,
    dispatch_csv_reconciles,
    input_quality,
    sibling_validation,
    write_run_directory,
)
from btm_sim.compare.exceptions import ComparisonError
from btm_sim.compare.metrics import (
    AVERAGE_MONTHLY_PEAK_DESCRIPTION,
    ENERGENT_PV_REVENUE_NOTE,
    HIGHEST_INTERVAL_VS_MONTHLY_NOTE,
    MONTHLY_PEAKS_DESCRIPTION,
    SCENARIO_LABELS,
    SCENARIO_ORDER,
    attach_baseline_dispatch,
    ensure_grid_import_kw,
    scenario_metrics,
)
from btm_sim.compare.monthly import build_monthly_summary, reconcile_monthly_summary
from btm_sim.compare.period import describe_selected_period
from btm_sim.compare.plots import write_seasonal_plots
from btm_sim.compare.weeks import select_seasonal_weeks
from btm_sim.config.schema import EconomicsConfig, ReportingConfig, SimulationConfig, TariffConfig
from btm_sim.economics import (
    attach_comparison_payback,
    comparison_economics_payload,
    economics_cost_source,
)
from btm_sim.fluvius.constants import CANONICAL_COLUMNS
from btm_sim.fluvius.csv_io import sha256_file
from btm_sim.market.prices import PriceDataError, load_day_ahead_prices
from btm_sim.optimizer.dynamic_injection import optimize_dynamic_injection
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.peak_reduction import optimize_peak_reduction
from btm_sim.optimizer.revenue import optimize_revenue
from btm_sim.optimizer.self_consumption import optimize_self_consumption
from btm_sim.settlement.tariffs import tariff_schedule_dict

Clock = Callable[[], datetime]


@dataclass
class ComparisonRun:
    directory: Path
    summary: dict[str, Any]
    metadata: dict[str, Any]
    dispatch: pd.DataFrame
    config: BatteryConfig
    tariffs: TariffConfig

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))


def comparison_config(config: BatteryConfig) -> BatteryConfig:
    """Require 0 kWh initial charge for the unified comparison."""
    if abs(config.soc_initial_kwh) > 0.0:
        raise ComparisonError(
            "The unified comparison requires initial battery charge of 0 kWh. "
            "A non-zero starting charge would count energy from before the selected "
            "period as additional PV. Use a standalone command if you need another "
            f"starting charge (got {config.soc_initial_kwh} kWh)."
        )
    return config


def resolve_run_directory(
    *,
    output_dir: str | Path | None = None,
    output_root: str | Path | None = None,
    clock: Clock | None = None,
) -> Path:
    if output_dir is not None and output_root is not None:
        raise ComparisonError("Provide only one of output_dir or output_root")
    if output_dir is not None:
        return Path(output_dir)
    if output_root is None:
        raise ComparisonError("Provide output_dir or output_root")
    stamp = (clock or _utc_now)().strftime("%Y%m%dT%H%M%SZ")
    return Path(output_root) / f"btm_compare_{stamp}"


def run_comparison_from_resolved(
    frame: pd.DataFrame,
    config: SimulationConfig,
    *,
    audit: dict[str, Any] | None = None,
    toml_path: str | Path | None = None,
    clock: Clock | None = None,
    source_path: str | Path | None = None,
    progress: Any = None,
    output_flag: int = 0,
) -> ComparisonRun:
    """Run the unified comparison from a resolved typed configuration."""
    return run_comparison(
        frame,
        config.battery,
        tariffs=config.tariffs,
        reporting=config.reporting,
        output_dir=config.output_dir,
        output_root=config.output_root,
        source_path=source_path or config.input_parquet,
        validation_report=config.validation_report,
        clock=clock,
        create_plots=config.reporting.seasonal_plots,
        config_audit=audit,
        toml_path=toml_path or (None if audit is None else audit.get("toml_path")),
        dynamic_injection_prices=config.dynamic_injection_prices,
        progress=progress,
        output_flag=output_flag,
        economics=config.economics,
    )


def run_comparison(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    tariffs: TariffConfig | None = None,
    reporting: ReportingConfig | None = None,
    output_dir: str | Path | None = None,
    output_root: str | Path | None = None,
    source_path: str | Path | None = None,
    validation_report: str | Path | None = None,
    clock: Clock | None = None,
    create_plots: bool = True,
    config_audit: dict[str, Any] | None = None,
    toml_path: str | Path | None = None,
    dynamic_injection_prices: str | Path | None = None,
    progress: Any = None,
    output_flag: int = 0,
    economics: EconomicsConfig | None = None,
    optimizer_backend: OptimizerBackend | None = None,
) -> ComparisonRun:
    """Run six cases from one canonical frame and write the comparison folder."""
    missing = [column for column in CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise ComparisonError(f"normalized input is missing columns: {missing}")
    if frame.empty:
        raise ComparisonError("Cannot compare an empty interval frame")

    tariffs = tariffs if tariffs is not None else TariffConfig()
    reporting = reporting if reporting is not None else ReportingConfig()
    economics = economics if economics is not None else EconomicsConfig()
    clock = clock or _utc_now
    generated_at = clock()
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    run_dir = resolve_run_directory(output_dir=output_dir, output_root=output_root, clock=lambda: generated_at)
    source = Path(source_path) if source_path is not None else None
    report_path = Path(validation_report) if validation_report is not None else None
    work = frame.sort_values("timestamp_utc").reset_index(drop=True)
    battery = comparison_config(config)
    from btm_sim.progress import STAGE_CHECK_PRICES, STAGE_WRITE_ARTIFACTS, stage_scope

    with stage_scope(progress, STAGE_CHECK_PRICES):
        try:
            prices = load_day_ahead_prices(work["timestamp_utc"], path=dynamic_injection_prices)
        except PriceDataError as exc:
            raise ComparisonError(str(exc)) from exc

    cases, solvers, checks = _run_cases(
        work,
        battery,
        tariffs,
        prices.prices_eur_mwh(),
        progress=progress,
        output_flag=output_flag,
        optimizer_backend=optimizer_backend,
    )
    with stage_scope(progress, STAGE_WRITE_ARTIFACTS):
        dispatch = build_comparison_dispatch(work, cases, tariffs, da_prices=prices.prices_eur_mwh())
        scenarios = {
            name: scenario_metrics(ensure_grid_import_kw(cases[name]), battery, scenario=name, tariffs=tariffs)
            for name in SCENARIO_ORDER
        }
        year_fraction = float(scenarios["no_battery"]["selected_period_year_fraction"])
        scenarios = attach_comparison_payback(
            scenarios,
            usable_energy_kwh=battery.e_usable_kwh,
            cost_eur_per_kwh=economics.estimated_battery_cost_eur_per_kwh,
            year_fraction=year_fraction,
        )
        cost_source = economics_cost_source(config_audit)
        shared_capex = scenarios["reference"]["estimated_battery_capex_eur"]
        economics_payload = comparison_economics_payload(
            cost_eur_per_kwh=economics.estimated_battery_cost_eur_per_kwh,
            capex_eur=float(shared_capex),
            year_fraction=year_fraction,
            cost_source=cost_source,
        )
        monthly_rows = build_monthly_summary(dispatch, battery, tariffs)
        try:
            dispatch_csv_reconciles(dispatch, {"scenarios": scenarios}, battery, tariffs)
            reconcile_monthly_summary(monthly_rows, {"scenarios": scenarios})
        except ValueError as exc:
            raise ComparisonError(
                "Comparison artifacts do not reconcile; the run folder was not published. " + str(exc)
            ) from exc
        weeks = select_seasonal_weeks(work, season_weeks=reporting.season_weeks())
        period = describe_selected_period(work)
        quality = input_quality(work)
        validation = sibling_validation(source, report_path)
        summary = {
            "ok": True,
            "result_description": (
                "Unified comparison of no-battery, simple reference, best-case "
                "self-consumption, best-case peak-reduction, best-case Energent "
                "PV revenue, and dynamic injection tariff cases. Revenue totals "
                "exclude battery CAPEX, OPEX, financing, taxes, and customer import costs."
            ),
            "initial_soc_kwh": 0.0,
            "initial_soc_note": (
                "This comparison uses 0 kWh initial battery charge so energy carried "
                "in from before the selected period is not counted as additional PV."
            ),
            "software_version": _software_version(),
            "generated_at_utc": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n_intervals": int(len(work)),
            "selected_period": period,
            "monthly_peaks_description": MONTHLY_PEAKS_DESCRIPTION,
            "average_monthly_peak_description": AVERAGE_MONTHLY_PEAK_DESCRIPTION,
            "peak_reduction_note": HIGHEST_INTERVAL_VS_MONTHLY_NOTE,
            "energent_pv_revenue_note": ENERGENT_PV_REVENUE_NOTE,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "scenario_order": list(SCENARIO_ORDER),
            "battery": battery.to_dict(),
            "tariffs": tariff_schedule_dict(tariffs),
            "reporting": reporting.to_dict(),
            "economics": economics_payload,
            "dynamic_injection_prices": prices.aligned_audit(),
            "scenarios": scenarios,
            "seasonal_plots": weeks,
            "checks": checks,
            "solvers": solvers,
        }
        metadata = {
            "generated_at_utc": summary["generated_at_utc"],
            "software_version": _software_version(),
            "package": "btm-sim",
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "scenario_order": list(SCENARIO_ORDER),
            "input": {
                "original_path": None if source is None else str(source),
                "sha256": None if source is None or not source.exists() else sha256_file(source),
            },
            "selected_period": period,
            "data_quality": quality,
            "validation_report": validation,
            "battery": battery.to_dict(),
            "battery_as_supplied": config.to_dict(),
            "tariffs": tariff_schedule_dict(tariffs),
            "reporting": reporting.to_dict(),
            "economics": economics_payload,
            "scenarios": {name: SCENARIO_LABELS[name] for name in SCENARIO_ORDER},
            "dynamic_injection_prices": prices.aligned_audit(),
            "solvers": solvers,
            "soc_conventions": {
                "comparison_initial_kwh": 0.0,
                "optimized_terminal_equals_initial": True,
                "reference_reports_terminal_without_forcing": True,
                "note": summary["initial_soc_note"],
            },
            "filenames": [],
            "checks": checks,
        }
        paths = write_run_directory(
            run_dir=run_dir,
            source_path=source,
            frame=work,
            dispatch=dispatch,
            summary=summary,
            metadata=metadata,
            config=battery,
            monthly_rows=monthly_rows,
            aligned_prices=prices.frame,
        )
        resolved_files = _write_resolved_config(
            run_dir,
            battery=battery,
            tariffs=tariffs,
            reporting=reporting,
            economics=economics,
            source=source,
            output_dir=output_dir,
            output_root=output_root,
            validation_report=report_path,
            audit=config_audit,
            toml_path=toml_path,
        )
        plot_files: list[str] = []
        if create_plots and reporting.seasonal_plots:
            plot_files = write_seasonal_plots(dispatch, battery, weeks, run_dir / "plots")
        filenames = [path.name for path in paths.values()] + list(resolved_files) + plot_files
        if plot_files:
            filenames.append("plots/")
        metadata["filenames"] = sorted(set(filenames))
        summary["seasonal_plots"] = weeks
        (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        (run_dir / "comparison_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return ComparisonRun(
        directory=run_dir,
        summary=summary,
        metadata=metadata,
        dispatch=dispatch,
        config=battery,
        tariffs=tariffs,
    )


def _run_cases(
    frame: pd.DataFrame,
    config: BatteryConfig,
    tariffs: TariffConfig,
    prices_eur_mwh,
    *,
    progress: Any = None,
    output_flag: int = 0,
    optimizer_backend: OptimizerBackend | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any], dict[str, Any]]:
    from btm_sim.progress import (
        STAGE_OPTIMIZE_DYNAMIC_INJECTION,
        STAGE_OPTIMIZE_PEAK_REDUCTION,
        STAGE_OPTIMIZE_REVENUE,
        STAGE_OPTIMIZE_SELF_CONSUMPTION,
        STAGE_RUN_REFERENCE,
        stage_scope,
    )

    baseline = attach_baseline_dispatch(frame, config)
    baseline_check = check_dispatch_feasibility(baseline, config).to_dict()
    if not baseline_check["ok"]:
        raise ComparisonError("No-battery dispatch failed battery-limit or energy-balance checks")

    with stage_scope(progress, STAGE_RUN_REFERENCE):
        try:
            reference = run_reference_controller(frame, config)
        except Exception as exc:
            raise ComparisonError(f"Simple reference controller failed: {exc}") from exc
        if not reference.feasibility_ok:
            raise ComparisonError(
                "Simple reference controller failed battery-limit or energy-balance checks"
            )

    with stage_scope(progress, STAGE_OPTIMIZE_SELF_CONSUMPTION):
        try:
            self_consumption = _optimize_self_consumption(
                frame, config, output_flag=output_flag, optimizer_backend=optimizer_backend
            )
        except OptimizerError as exc:
            raise ComparisonError(f"Best-case self-consumption solve failed: {exc}") from exc
    with stage_scope(progress, STAGE_OPTIMIZE_PEAK_REDUCTION):
        try:
            peak_reduction = _optimize_peak_reduction(
                frame, config, output_flag=output_flag, optimizer_backend=optimizer_backend
            )
        except OptimizerError as exc:
            raise ComparisonError(f"Best-case peak-reduction solve failed: {exc}") from exc
    with stage_scope(progress, STAGE_OPTIMIZE_REVENUE):
        try:
            revenue = _optimize_revenue(
                frame,
                config,
                tariffs,
                output_flag=output_flag,
                customer_first=self_consumption,
                optimizer_backend=optimizer_backend,
            )
        except OptimizerError as exc:
            raise ComparisonError(f"Best-case revenue solve failed: {exc}") from exc
    with stage_scope(progress, STAGE_OPTIMIZE_DYNAMIC_INJECTION):
        try:
            dynamic = _optimize_dynamic_injection(
                frame,
                config,
                prices_eur_mwh,
                tariffs=tariffs,
                customer_first=self_consumption,
                output_flag=output_flag,
                optimizer_backend=optimizer_backend,
            )
        except OptimizerError as exc:
            raise ComparisonError(f"Dynamic injection solve failed: {exc}") from exc

    cases = {
        "no_battery": ensure_grid_import_kw(baseline),
        "reference": ensure_grid_import_kw(reference.frame),
        "self_consumption": ensure_grid_import_kw(self_consumption.frame),
        "peak_reduction": ensure_grid_import_kw(peak_reduction.frame),
        "revenue": ensure_grid_import_kw(revenue.frame),
        "dynamic_injection": ensure_grid_import_kw(dynamic.frame),
    }
    solvers = {
        "self_consumption": self_consumption.summary.get("solver"),
        "peak_reduction": peak_reduction.summary.get("solver"),
        "revenue": revenue.summary.get("solver"),
        "dynamic_injection": dynamic.summary.get("solver"),
    }
    checks = {
        "no_battery": {
            "battery_limits_and_balances": "passed" if baseline_check["ok"] else "failed",
            "feasibility": baseline_check,
        },
        "reference": {
            "battery_limits_and_balances": "passed" if reference.feasibility_ok else "failed",
            "feasibility": reference.summary.get("feasibility"),
        },
        "self_consumption": {
            "battery_limits_and_balances": self_consumption.summary.get("battery_limits_and_balances"),
            "feasibility": self_consumption.summary.get("feasibility"),
        },
        "peak_reduction": {
            "battery_limits_and_balances": peak_reduction.summary.get("battery_limits_and_balances"),
            "feasibility": peak_reduction.summary.get("feasibility"),
        },
        "revenue": {
            "battery_limits_and_balances": revenue.summary.get("battery_limits_and_balances"),
            "feasibility": revenue.summary.get("feasibility"),
        },
        "dynamic_injection": {
            "battery_limits_and_balances": dynamic.summary.get("battery_limits_and_balances"),
            "feasibility": dynamic.summary.get("feasibility"),
        },
    }
    return cases, solvers, checks


def _optimize_self_consumption(frame, config, *, output_flag, optimizer_backend):
    if optimizer_backend is None:
        return optimize_self_consumption(frame, config, output_flag=output_flag)
    return optimizer_backend.optimize_self_consumption(frame, config, output_flag=output_flag)


def _optimize_peak_reduction(frame, config, *, output_flag, optimizer_backend):
    if optimizer_backend is None:
        return optimize_peak_reduction(frame, config, output_flag=output_flag)
    return optimizer_backend.optimize_peak_reduction(frame, config, output_flag=output_flag)


def _optimize_revenue(frame, config, tariffs, *, output_flag, customer_first, optimizer_backend):
    if optimizer_backend is None:
        return optimize_revenue(
            frame, config, tariffs, output_flag=output_flag, customer_first=customer_first
        )
    return optimizer_backend.optimize_revenue(
        frame, config, tariffs, output_flag=output_flag, customer_first=customer_first
    )


def _optimize_dynamic_injection(
    frame, config, prices_eur_mwh, *, tariffs, customer_first, output_flag, optimizer_backend
):
    if optimizer_backend is None:
        return optimize_dynamic_injection(
            frame,
            config,
            prices_eur_mwh,
            tariffs=tariffs,
            customer_first=customer_first,
            output_flag=output_flag,
        )
    return optimizer_backend.optimize_dynamic_injection(
        frame,
        config,
        prices_eur_mwh,
        tariffs=tariffs,
        customer_first=customer_first,
        output_flag=output_flag,
    )


def _write_resolved_config(
    run_dir: Path,
    *,
    battery: BatteryConfig,
    tariffs: TariffConfig,
    reporting: ReportingConfig,
    economics: EconomicsConfig,
    source: Path | None,
    output_dir: str | Path | None,
    output_root: str | Path | None,
    validation_report: Path | None,
    audit: dict[str, Any] | None,
    toml_path: str | Path | None,
) -> list[str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": {
            "defaults_path": None if audit is None else audit.get("defaults_path"),
            "defaults_sha256": None if audit is None else audit.get("defaults_sha256"),
            "toml_path": None if audit is None else audit.get("toml_path"),
            "toml_sha256": None if audit is None else audit.get("toml_sha256"),
            "run_toml_path": None if audit is None else audit.get("run_toml_path"),
            "run_toml_sha256": None if audit is None else audit.get("run_toml_sha256"),
            "cli_overrides": [] if audit is None else audit.get("cli_overrides", []),
        },
        "value_sources": None if audit is None else audit.get("value_sources"),
        "resolved": {
            "input": {
                "normalized_parquet": None if source is None else str(source),
                "validation_report": None if validation_report is None else str(validation_report),
            },
            "output": {
                "directory": None if output_dir is None else str(output_dir),
                "root": None if output_root is None else str(output_root),
                "run_directory": str(run_dir),
            },
            "battery": battery.to_dict(),
            "tariffs": tariffs.to_dict(),
            "reporting": reporting.to_dict(),
            "economics": economics.to_dict(),
        },
    }
    if audit is not None and "resolved" in audit:
        payload["resolved"] = {**audit["resolved"], **payload["resolved"]}
    resolved_path = run_dir / "resolved_config.json"
    resolved_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    written = ["resolved_config.json"]
    defaults_toml = None if audit is None else audit.get("defaults_path")
    if defaults_toml:
        defaults_file = Path(str(defaults_toml))
        if defaults_file.exists():
            shutil.copy2(defaults_file, run_dir / "source_defaults.toml")
            written.append("source_defaults.toml")
    source_toml = Path(toml_path) if toml_path else None
    if source_toml is None and audit and audit.get("toml_path"):
        source_toml = Path(str(audit["toml_path"]))
    if source_toml is not None and source_toml.exists():
        shutil.copy2(source_toml, run_dir / "source_config.toml")
        written.append("source_config.toml")
    return written


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _software_version() -> str:
    from btm_sim import __version__

    return __version__
