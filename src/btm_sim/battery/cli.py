"""Command-line entry point for the diagnostic reference controller."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.battery.dispatch import run_reference_controller, write_reference_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btm-reference",
        description=(
            "Run the simple reference controller on a normalized Fluvius parquet. "
            "It looks only at the current quarter-hour and is a check on battery "
            "physics, not a best-case result."
        ),
    )
    parser.add_argument("input", type=Path, help="normalized_input.parquet from btm-normalize")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for reference_dispatch.csv and reference_summary.json",
    )
    parser.add_argument("--e-usable", type=float, required=True, help="Usable stored energy, kWh")
    parser.add_argument("--p-charge", type=float, default=None, help="Maximum AC charge power, kW")
    parser.add_argument("--p-discharge", type=float, default=None, help="Maximum AC discharge power, kW")
    parser.add_argument(
        "--power",
        type=float,
        default=None,
        help="Optional symmetric AC power in kW; sets both charge and discharge ratings",
    )
    parser.add_argument("--eta-charge", type=float, required=True, help="AC-to-stored charge efficiency in (0, 1]")
    parser.add_argument("--eta-discharge", type=float, required=True, help="Stored-to-AC discharge efficiency in (0, 1]")
    parser.add_argument(
        "--soc-initial",
        type=float,
        default=0.0,
        help="Initial stored energy in kWh (default 0: empty, as specified for this diagnostic controller)",
    )
    parser.add_argument(
        "--max-equivalent-full-cycles-per-year",
        type=float,
        default=400.0,
        dest="max_equivalent_full_cycles_per_year",
        help="Maximum equivalent full cycles per year; prorated by the selected period (default: 400)",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> BatteryConfig:
    p_charge = args.p_charge
    p_discharge = args.p_discharge
    if args.power is not None:
        if p_charge is None:
            p_charge = args.power
        if p_discharge is None:
            p_discharge = args.power
    if p_charge is None or p_discharge is None:
        raise BatteryConfigError("Provide --p-charge and --p-discharge, or --power for both")
    return BatteryConfig(
        e_usable_kwh=args.e_usable,
        p_charge_kw=p_charge,
        p_discharge_kw=p_discharge,
        eta_charge=args.eta_charge,
        eta_discharge=args.eta_discharge,
        soc_initial_kwh=args.soc_initial,
        max_equivalent_full_cycles_per_year=args.max_equivalent_full_cycles_per_year,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 2
    try:
        config = _config_from_args(args)
    except BatteryConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    frame = pd.read_parquet(args.input)
    result = run_reference_controller(frame, config)
    write_reference_outputs(result, args.output_dir, source_path=args.input)
    print(
        json.dumps(
            {
                "ok": result.summary["ok"],
                "result_description": result.summary["result_description"],
                "battery_limits_and_balances": (
                    "passed" if result.summary["ok"] else "failed"
                ),
                "n_intervals": result.summary["n_intervals"],
                "soc_initial_kwh": result.summary["soc_initial_kwh"],
                "soc_final_kwh": result.summary["soc_final_kwh"],
                "useful_additional_pv_kwh": result.summary["energy_kwh"]["useful_additional_pv"],
                "annual_peak_kw": result.summary["peaks_kw"]["annual_max"],
                "total_loss_kwh": result.summary["energy_kwh"]["total_loss"],
                "equivalent_full_cycles": result.summary["throughput"]["equivalent_full_cycles"],
                "outputs": result.summary.get("outputs"),
            },
            indent=2,
        )
    )
    return 0 if result.feasibility_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
