"""Selected-period equivalent-full-cycle budget from the annual limit."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.physics import equivalent_full_cycles, stored_throughput_kwh
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, FLOAT_EPS_KWH, INTERVAL_HOURS, TZ_NAME
from btm_sim.fluvius.periods import expected_quarter_hours


def calendar_year_physical_hours(year: int) -> float:
    """Physical hours in a Europe/Brussels calendar year, including clock changes."""
    return float(expected_quarter_hours(year)) * INTERVAL_HOURS


def selected_period_year_fraction(frame: pd.DataFrame) -> float:
    """Sum of each interval's hours as a fraction of its local calendar year.

    A complete calendar year, including leap year 2024, returns exactly 1.0.
    """
    if frame.empty:
        return 0.0
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    hours = (
        frame["interval_hours"].to_numpy(dtype=float)
        if "interval_hours" in frame.columns
        else np.full(len(frame), INTERVAL_HOURS, dtype=float)
    )
    years = local.dt.year.to_numpy()
    total = 0.0
    for year in np.unique(years):
        year_hours = calendar_year_physical_hours(int(year))
        if year_hours <= 0:
            continue
        total += float(hours[years == year].sum()) / year_hours
    return float(total)


def allowed_equivalent_full_cycles(config: BatteryConfig, year_fraction: float) -> float:
    return float(config.max_equivalent_full_cycles_per_year) * float(year_fraction)


def allowed_stored_throughput_kwh(config: BatteryConfig, year_fraction: float) -> float:
    return 2.0 * float(config.e_usable_kwh) * allowed_equivalent_full_cycles(config, year_fraction)


def cycle_limit_report(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    charge_pv_kwh: pd.Series | np.ndarray | None = None,
    discharge_ac_kwh: pd.Series | np.ndarray | None = None,
) -> dict[str, Any]:
    """Describe the configured cycle budget and whether the dispatch used it."""
    if charge_pv_kwh is None:
        charge_pv_kwh = frame["charge_pv_kwh"]
    if discharge_ac_kwh is None:
        customer = frame["discharge_load_kwh"].to_numpy(dtype=float)
        grid = (
            frame["discharge_grid_kwh"].to_numpy(dtype=float)
            if "discharge_grid_kwh" in frame.columns
            else np.zeros(len(frame), dtype=float)
        )
        discharge_ac_kwh = customer + grid
    year_fraction = selected_period_year_fraction(frame)
    allowed_cycles = allowed_equivalent_full_cycles(config, year_fraction)
    allowed_throughput = allowed_stored_throughput_kwh(config, year_fraction)
    actual_throughput = stored_throughput_kwh(charge_pv_kwh, discharge_ac_kwh, config)
    actual_cycles = equivalent_full_cycles(actual_throughput, config.e_usable_kwh)
    remaining_throughput = allowed_throughput - actual_throughput
    remaining_cycles = equivalent_full_cycles(max(remaining_throughput, 0.0), config.e_usable_kwh)
    binding = bool(actual_throughput + DOCUMENTED_TOLERANCE_KWH >= allowed_throughput)
    return {
        "max_equivalent_full_cycles_per_year": float(config.max_equivalent_full_cycles_per_year),
        "selected_period_year_fraction": year_fraction,
        "allowed_equivalent_full_cycles": allowed_cycles,
        "allowed_stored_throughput_kwh": allowed_throughput,
        "stored_throughput_kwh": actual_throughput,
        "equivalent_full_cycles": actual_cycles,
        "remaining_equivalent_full_cycles_allowance": remaining_cycles,
        "remaining_stored_throughput_kwh": remaining_throughput,
        "cycle_limit_binding": binding,
        "cycle_limit_tolerance_kwh": DOCUMENTED_TOLERANCE_KWH,
    }
