"""Deterministic myopic PV-only self-consumption reference controller."""

from __future__ import annotations

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import allowed_stored_throughput_kwh, selected_period_year_fraction
from btm_sim.battery.physics import net_availability
from btm_sim.fluvius.constants import FLOAT_EPS_KWH, INTERVAL_HOURS


def reference_actions(
    frame: pd.DataFrame,
    config: BatteryConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return charge, discharge, soc_start, soc_end arrays for the diagnostic controller.

    Processes rows in chronological order. Charges only from net export and
    discharges only to net import. Never both in the same interval. Starts at
    ``config.soc_initial_kwh`` (empty by default) and does not force a terminal SoC.
    """
    n = len(frame)
    dt = (
        frame["interval_hours"].to_numpy(dtype=float)
        if "interval_hours" in frame.columns
        else np.full(n, INTERVAL_HOURS)
    )
    import0 = frame["grid_import_baseline_kwh"].to_numpy(dtype=float)
    export0 = frame["grid_export_baseline_kwh"].to_numpy(dtype=float)
    net_export, net_import = net_availability(import0, export0)

    charge = np.zeros(n, dtype=float)
    discharge = np.zeros(n, dtype=float)
    soc = np.zeros(n + 1, dtype=float)
    soc[0] = config.soc_initial_kwh
    remaining_throughput = allowed_stored_throughput_kwh(config, selected_period_year_fraction(frame))

    for i in range(n):
        stored = soc[i]
        if remaining_throughput <= FLOAT_EPS_KWH:
            remaining_throughput = 0.0
        elif net_export[i] > FLOAT_EPS_KWH and config.p_charge_kw > FLOAT_EPS_KWH and config.e_usable_kwh > FLOAT_EPS_KWH:
            room_ac = max(config.e_usable_kwh - stored, 0.0) / config.eta_charge
            budget_ac = remaining_throughput / config.eta_charge
            charge[i] = min(net_export[i], config.p_charge_kw * dt[i], room_ac, budget_ac)
            if charge[i] < FLOAT_EPS_KWH:
                charge[i] = 0.0
        elif net_import[i] > FLOAT_EPS_KWH and config.p_discharge_kw > FLOAT_EPS_KWH and stored > FLOAT_EPS_KWH:
            room_ac = stored * config.eta_discharge
            budget_ac = remaining_throughput * config.eta_discharge
            discharge[i] = min(net_import[i], config.p_discharge_kw * dt[i], room_ac, budget_ac)
            if discharge[i] < FLOAT_EPS_KWH:
                discharge[i] = 0.0
        soc[i + 1] = (
            stored
            + config.eta_charge * charge[i]
            - discharge[i] / config.eta_discharge
        )
        remaining_throughput -= config.eta_charge * charge[i] + discharge[i] / config.eta_discharge
        if remaining_throughput < 0.0 and abs(remaining_throughput) <= FLOAT_EPS_KWH:
            remaining_throughput = 0.0
        if abs(soc[i + 1]) <= FLOAT_EPS_KWH:
            soc[i + 1] = 0.0
        if abs(soc[i + 1] - config.e_usable_kwh) <= FLOAT_EPS_KWH:
            soc[i + 1] = config.e_usable_kwh

    return charge, discharge, soc[:-1], soc[1:]


def attach_reference_dispatch(frame: pd.DataFrame, config: BatteryConfig) -> pd.DataFrame:
    """Preserve canonical columns and append auditable reference-dispatch columns."""
    out = frame.copy()
    charge, discharge, soc_start, soc_end = reference_actions(out, config)
    import0 = out["grid_import_baseline_kwh"].to_numpy(dtype=float)
    export0 = out["grid_export_baseline_kwh"].to_numpy(dtype=float)
    net_export, net_import = net_availability(import0, export0)
    out["net_export_available_kwh"] = net_export
    out["net_import_need_kwh"] = net_import
    out["charge_pv_kwh"] = charge
    out["discharge_load_kwh"] = discharge
    out["discharge_grid_kwh"] = np.zeros(len(out), dtype=float)
    out["soc_start_kwh"] = soc_start
    out["soc_end_kwh"] = soc_end
    out["charge_loss_kwh"] = charge * (1.0 - config.eta_charge)
    out["discharge_loss_kwh"] = discharge / config.eta_discharge - discharge
    out["grid_import_kwh"] = import0 - discharge
    out["grid_export_kwh"] = export0 - charge
    return out
