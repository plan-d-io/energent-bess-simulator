"""Command-line entry point for the revenue-first LP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from btm_sim.battery.config import BatteryConfigError
from btm_sim.cli_battery import add_battery_arguments, battery_config_from_args
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.schema import TariffConfig, parse_hhmm
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.revenue import optimize_revenue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btm-revenue",
        description=(
            "Best-case Energent PV revenue optimization using the complete year "
            "in advance. Totals exclude CAPEX, OPEX, financing, taxes, and "
            "customer import costs. Not a forecast, profit, or NPV."
        ),
    )
    parser.add_argument("input", type=Path, help="normalized_input.parquet from btm-normalize")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for revenue_dispatch.csv and revenue_summary.json",
    )
    add_battery_arguments(parser, required=True)
    parser.add_argument(
        "--customer-rate",
        type=float,
        default=130.0,
        help="Customer PV-sale rate, EUR/MWh (default: 130)",
    )
    parser.add_argument(
        "--export-peak-rate",
        type=float,
        default=60.0,
        help="Peak-period export rate, EUR/MWh (default: 60)",
    )
    parser.add_argument(
        "--export-offpeak-rate",
        type=float,
        default=30.0,
        help="Off-peak export rate, EUR/MWh (default: 30)",
    )
    parser.add_argument("--peak-start", default="08:00", help="Local peak start HH:MM, inclusive (default: 08:00)")
    parser.add_argument("--peak-end", default="20:00", help="Local peak end HH:MM, exclusive (default: 20:00)")
    parser.add_argument(
        "--weekends-offpeak",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Treat all Saturday and Sunday intervals as off-peak (default: true)",
    )
    parser.add_argument("--timezone", default="Europe/Brussels", help="Timezone for tariff classification")
    return parser


def _tariffs_from_args(args: argparse.Namespace) -> TariffConfig:
    try:
        return TariffConfig(
            customer_sale_eur_per_mwh=args.customer_rate,
            peak_export_eur_per_mwh=args.export_peak_rate,
            offpeak_export_eur_per_mwh=args.export_offpeak_rate,
            peak_start_local=parse_hhmm(args.peak_start, name="peak_start"),
            peak_end_local=parse_hhmm(args.peak_end, name="peak_end"),
            weekends_offpeak=bool(args.weekends_offpeak),
            timezone=args.timezone,
        )
    except ConfigError as exc:
        raise BatteryConfigError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 2
    try:
        config = battery_config_from_args(args)
        tariffs = _tariffs_from_args(args)
    except (BatteryConfigError, ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = optimize_revenue(
            pd.read_parquet(args.input),
            config,
            tariffs,
            output_dir=args.output_dir,
            source_path=args.input,
        )
    except OptimizerError as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "status": exc.status,
            "stage": exc.stage,
            "details": exc.details,
        }
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 1

    revenue = result.summary["revenue"]
    print(
        json.dumps(
            {
                "ok": result.ok,
                "result_description": result.summary["result_description"],
                "battery_limits_and_balances": result.summary["battery_limits_and_balances"],
                "case": result.summary["case"],
                "n_intervals": result.summary["n_intervals"],
                "soc_initial_kwh": result.summary["soc_initial_kwh"],
                "soc_final_kwh": result.summary["soc_final_kwh"],
                "total_energent_pv_revenue_eur": revenue["total_energent_pv_revenue_eur"],
                "revenue_change_eur": revenue["revenue_change_eur"],
                "annual_peak_kw": result.summary["peaks_kw"]["annual_max"],
                "solver": result.summary["solver"],
                "objective_steps": result.summary["objective_steps"],
                "outputs": result.summary.get("outputs"),
            },
            indent=2,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
