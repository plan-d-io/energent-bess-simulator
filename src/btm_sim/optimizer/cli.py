"""Command-line entry point for the self-consumption-first LP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from btm_sim.battery.config import BatteryConfigError
from btm_sim.cli_battery import add_battery_arguments, battery_config_from_args
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.self_consumption import optimize_self_consumption


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btm-self-consumption",
        description=(
            "Best-case self-consumption optimization using the complete year in "
            "advance. Not a forecast or expected operational saving."
        ),
    )
    parser.add_argument("input", type=Path, help="normalized_input.parquet from btm-normalize")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for self_consumption_dispatch.csv and self_consumption_summary.json",
    )
    add_battery_arguments(parser, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 2
    try:
        config = battery_config_from_args(args)
    except BatteryConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        result = optimize_self_consumption(
            pd.read_parquet(args.input),
            config,
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
                "useful_additional_pv_kwh": result.summary["energy_kwh"]["useful_additional_pv"],
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
