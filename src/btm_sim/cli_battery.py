"""Shared CLI flags for BatteryConfig. Command names stay unchanged."""

from __future__ import annotations

import argparse

from btm_sim.battery.config import BatteryConfig, BatteryConfigError


def add_battery_arguments(
    parser: argparse.ArgumentParser,
    *,
    required: bool,
    starting_value_note: str | None = None,
) -> None:
    if starting_value_note is not None:
        extra = ". " + starting_value_note
    elif required:
        extra = ""
    else:
        extra = ". Required unless set in --config"
    parser.add_argument(
        "--e-usable",
        type=float,
        required=required,
        default=None,
        help="Usable stored energy, kWh" + extra,
    )
    power_note = extra if starting_value_note is not None else ""
    parser.add_argument(
        "--p-charge",
        type=float,
        default=None,
        help="Maximum AC charge power, kW" + power_note,
    )
    parser.add_argument(
        "--p-discharge",
        type=float,
        default=None,
        help="Maximum AC discharge power, kW" + power_note,
    )
    parser.add_argument(
        "--power",
        type=float,
        default=None,
        help="Optional symmetric AC power in kW; sets both charge and discharge ratings"
        + power_note,
    )
    parser.add_argument(
        "--eta-charge",
        type=float,
        required=required,
        default=None,
        help="AC-to-stored charge efficiency in (0, 1]" + extra,
    )
    parser.add_argument(
        "--eta-discharge",
        type=float,
        required=required,
        default=None,
        help="Stored-to-AC discharge efficiency in (0, 1]" + extra,
    )
    parser.add_argument(
        "--soc-initial",
        type=float,
        default=None,
        help="Initial stored energy in kWh" + (power_note or " (default: 0)"),
    )
    parser.add_argument(
        "--max-equivalent-full-cycles-per-year",
        type=float,
        default=None,
        dest="max_equivalent_full_cycles_per_year",
        help="Maximum equivalent full cycles per year; prorated by the selected period"
        + (extra if starting_value_note is not None else " (default: 400)"),
    )


def battery_config_from_args(args: argparse.Namespace) -> BatteryConfig:
    p_charge = args.p_charge
    p_discharge = args.p_discharge
    if args.power is not None:
        if p_charge is None:
            p_charge = args.power
        if p_discharge is None:
            p_discharge = args.power
    if p_charge is None or p_discharge is None:
        raise BatteryConfigError("Provide --p-charge and --p-discharge, or --power for both")
    soc = 0.0 if args.soc_initial is None else args.soc_initial
    max_cycles = args.max_equivalent_full_cycles_per_year
    if max_cycles is None:
        max_cycles = 400.0
    return BatteryConfig(
        e_usable_kwh=args.e_usable,
        p_charge_kw=p_charge,
        p_discharge_kw=p_discharge,
        eta_charge=args.eta_charge,
        eta_discharge=args.eta_discharge,
        soc_initial_kwh=soc,
        max_equivalent_full_cycles_per_year=max_cycles,
    )
