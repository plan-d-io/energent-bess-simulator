"""Command-line entry point for the unified six-case comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from btm_sim.cli_battery import add_battery_arguments
from btm_sim.compare.exceptions import ComparisonError
from btm_sim.compare.metrics import (
    AVERAGE_MONTHLY_PEAK_DESCRIPTION,
    ENERGENT_PV_REVENUE_NOTE,
    HIGHEST_INTERVAL_VS_MONTHLY_NOTE,
    MONTHLY_PEAKS_DESCRIPTION,
    NOT_APPLICABLE,
    SCENARIO_ORDER,
)
from btm_sim.compare.runner import run_comparison_from_resolved
from btm_sim.config.defaults import standard_defaults_path
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_simulation_config
from btm_sim.optimizer.exceptions import OptimizerError

_STARTING_VALUE_NOTE = "Starting value comes from the selected central defaults file"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btm-compare",
        description=(
            "Compare no-battery, simple reference, best-case self-consumption, "
            "best-case peak-reduction, best-case Energent PV revenue, and dynamic "
            "injection tariff cases from one normalized parquet. The comparison "
            "requires 0 kWh initial battery charge. New runs start with values from "
            "the central defaults file. A run configuration or explicit command-line "
            "value can override them. The audit folder records every effective value "
            "and its source. Every new run includes all six cases."
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="normalized_input.parquet from btm-normalize. Required unless set in the run configuration",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Optional run configuration TOML (input/output paths and optional overrides). "
            "This is not the central defaults file. Relative paths in the file resolve "
            "from that file's directory"
        ),
    )
    parser.add_argument(
        "--defaults",
        type=Path,
        default=None,
        help=(
            "Central defaults TOML (battery, tariffs, reporting). "
            f"Default: {standard_defaults_path()}"
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Exact run directory (for tests and scripted use)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Parent folder; a timestamped btm_compare_* directory is created inside it",
    )
    parser.add_argument(
        "--validation-report",
        type=Path,
        default=None,
        help="Optional validation_report.json to copy into run metadata",
    )
    parser.add_argument(
        "--dynamic-injection-prices",
        type=Path,
        default=None,
        help=(
            "Compatible day-ahead price Parquet for the dynamic-injection case. "
            "Default: the project's data/market/da_prices_qh.parquet"
        ),
    )
    add_battery_arguments(parser, required=False, starting_value_note=_STARTING_VALUE_NOTE)
    parser.add_argument(
        "--customer-rate",
        type=float,
        default=None,
        help="Customer PV-sale rate, EUR/MWh. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--export-peak-rate",
        type=float,
        default=None,
        help="Peak-period export rate, EUR/MWh. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--export-offpeak-rate",
        type=float,
        default=None,
        help="Off-peak export rate, EUR/MWh. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--peak-start",
        default=None,
        help="Local peak start HH:MM, inclusive. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--peak-end",
        default=None,
        help="Local peak end HH:MM, exclusive. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--weekends-offpeak",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Treat all Saturday and Sunday intervals as off-peak. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="Timezone for tariff classification. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--seasonal-plots",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write fixed seasonal dispatch plots. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--winter-iso-week",
        type=int,
        default=None,
        help="ISO week for the winter plot. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--spring-iso-week",
        type=int,
        default=None,
        help="ISO week for the spring plot. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--summer-iso-week",
        type=int,
        default=None,
        help="ISO week for the summer plot. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--autumn-iso-week",
        type=int,
        default=None,
        help="ISO week for the autumn plot. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--estimated-battery-cost-eur-per-kwh",
        "--estimated-battery-cost",
        type=float,
        default=None,
        dest="estimated_battery_cost_eur_per_kwh",
        help="Estimated battery cost in EUR/kWh usable. " + _STARTING_VALUE_NOTE,
    )
    return parser


def _cli_overrides(args: argparse.Namespace) -> dict:
    values = {
        "input": args.input,
        "validation_report": args.validation_report,
        "dynamic_injection_prices": args.dynamic_injection_prices,
        "output_dir": args.output_dir,
        "output_root": args.output_root,
        "e_usable": args.e_usable,
        "p_charge": args.p_charge,
        "p_discharge": args.p_discharge,
        "power": args.power,
        "eta_charge": args.eta_charge,
        "eta_discharge": args.eta_discharge,
        "soc_initial": args.soc_initial,
        "max_equivalent_full_cycles_per_year": args.max_equivalent_full_cycles_per_year,
        "customer_rate": args.customer_rate,
        "export_peak_rate": args.export_peak_rate,
        "export_offpeak_rate": args.export_offpeak_rate,
        "peak_start": args.peak_start,
        "peak_end": args.peak_end,
        "weekends_offpeak": args.weekends_offpeak,
        "timezone": args.timezone,
        "seasonal_plots": args.seasonal_plots,
        "winter_iso_week": args.winter_iso_week,
        "spring_iso_week": args.spring_iso_week,
        "summer_iso_week": args.summer_iso_week,
        "autumn_iso_week": args.autumn_iso_week,
        "estimated_battery_cost_eur_per_kwh": args.estimated_battery_cost_eur_per_kwh,
    }
    return {key: value for key, value in values.items() if value is not None}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.config is None and args.input is None:
        print("Provide a normalized parquet path or a run configuration (--config)", file=sys.stderr)
        return 2
    try:
        config, audit = resolve_simulation_config(
            toml_path=args.config,
            cli=_cli_overrides(args),
            require_zero_initial_charge=True,
            defaults_path=args.defaults,
        )
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = run_comparison_from_resolved(
            pd.read_parquet(config.input_parquet),
            config,
            audit=audit,
            toml_path=args.config,
            source_path=config.input_parquet,
        )
    except (ComparisonError, OptimizerError, ConfigError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(_console_overview(result.summary, result.directory), indent=2))
    return 0 if result.ok else 1


def _console_overview(summary: dict, directory: Path) -> dict:
    period = summary["selected_period"]
    cases = []
    for name in SCENARIO_ORDER:
        row = summary["scenarios"][name]
        revenue = row["revenue"]
        cases.append(
            {
                "scenario": name,
                "highest_15min_grid_import_kw": _round(row["annual_peak_kw"], 1),
                "reduction_in_highest_15min_grid_import_kw": _round(row["annual_peak_reduction_kw"], 1),
                "reduction_in_highest_15min_grid_import_pct": _pct(row["annual_peak_reduction_pct"]),
                "average_monthly_peak_kw": _round(row["average_monthly_peak_kw"], 1),
                "reduction_in_average_monthly_peak_kw": _round(row["average_monthly_peak_reduction_kw"], 1),
                "reduction_in_average_monthly_peak_pct": _pct(row["average_monthly_peak_reduction_pct"]),
                "grid_electricity_imported_kwh": _round(row["grid_import_kwh"], 3),
                "pv_injected_into_the_grid_kwh": _round(row["grid_export_kwh"], 3),
                "battery_conversion_losses_kwh": _round(row["total_loss_kwh"], 3),
                "energent_pv_revenue_eur": _round(revenue["total_energent_pv_revenue_eur"], 2),
                "useful_pv_delivered_kwh": _round(row["useful_pv_delivered_kwh"], 3),
                "additional_useful_pv_kwh": _round(row["additional_useful_pv_kwh"], 3),
                "additional_useful_pv_pct_of_total_pv": _pct(row["additional_useful_pv_pct_of_total_pv"]),
                "useful_self_consumption_pct_before": _pct(row["useful_self_consumption_pct_before"]),
                "useful_self_consumption_pct_after": _pct(row["useful_self_consumption_pct_after"]),
                "useful_self_consumption_change_pp": _pp(row["useful_self_consumption_change_pp"]),
                "stored_throughput_kwh": _round(row["stored_throughput_kwh"], 3),
                "equivalent_full_cycles": _round(row["equivalent_full_cycles"], 3),
                "soc_final_kwh": _round(row["soc_final_kwh"], 3),
                "revenue_change_eur": _round(revenue["revenue_change_eur"], 2),
                "revenue_change_pct": _pct(revenue["revenue_change_pct"]),
                "annual_revenue_uplift_eur": _round(row.get("annual_revenue_uplift_eur"), 2),
                "simple_payback_years": _round(row.get("simple_payback_years"), 2),
                "extra_customer_sale_eur": _round(revenue["extra_customer_sale_eur"], 2),
                "foregone_export_eur": _round(revenue["foregone_export_eur"], 2),
            }
        )
    return {
        "ok": summary["ok"],
        "output_dir": str(directory),
        "selected_period": {
            "start_local": period["start_local"],
            "end_local_exclusive": period["end_local_exclusive"],
            "kind": period["kind"],
            "label": period["label"],
            "n_intervals": period["n_intervals"],
        },
        "initial_soc_kwh": 0.0,
        "initial_soc_note": summary["initial_soc_note"],
        "monthly_peaks_description": MONTHLY_PEAKS_DESCRIPTION,
        "average_monthly_peak_description": AVERAGE_MONTHLY_PEAK_DESCRIPTION,
        "peak_reduction_note": HIGHEST_INTERVAL_VS_MONTHLY_NOTE,
        "energent_pv_revenue_note": ENERGENT_PV_REVENUE_NOTE,
        "artifact_schema_version": summary.get("artifact_schema_version"),
        "total_pv_production_kwh": _round(summary["scenarios"]["no_battery"]["total_pv_production_kwh"], 3),
        "cases": cases,
        "seasonal_plots": {
            "included": [
                {
                    "season": item["season"],
                    "iso_week": item["iso_week"],
                    "start_local": item["start_local"],
                    "end_local_exclusive": item["end_local_exclusive"],
                }
                for item in summary["seasonal_plots"]["included"]
            ],
            "omitted_seasons": summary["seasonal_plots"]["omitted_seasons"],
            "note": summary["seasonal_plots"]["note"],
        },
        "solvers": summary["solvers"],
    }


def _round(value: float | None, digits: int) -> float | str:
    if value is None:
        return NOT_APPLICABLE
    return round(float(value), digits)


def _pct(value: float | None) -> str:
    if value is None:
        return NOT_APPLICABLE
    return f"{float(value):.2f}%"


def _pp(value: float | None) -> str:
    if value is None:
        return NOT_APPLICABLE
    return f"{float(value):.2f} percentage points"


if __name__ == "__main__":
    raise SystemExit(main())
