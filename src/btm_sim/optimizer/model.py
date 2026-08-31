"""Gurobi LP of the version-1 battery physics. Continuous variables only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import allowed_stored_throughput_kwh, selected_period_year_fraction
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, FLOAT_EPS_KWH, INTERVAL_HOURS
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.physical_prep import local_month_groups


def import_gurobipy():
    try:
        import gurobipy as gp
    except ImportError as exc:
        raise OptimizerError(
            "Gurobi is unavailable: the gurobipy package is not installed"
        ) from exc
    return gp


def start_gurobi_env(*, output_flag: int = 0):
    gp = import_gurobipy()
    try:
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", output_flag)
        env.start()
    except gp.GurobiError as exc:
        raise OptimizerError(f"Gurobi licence error: {exc}") from exc
    return gp, env


STATUS_NAMES = {
    1: "LOADED",
    2: "OPTIMAL",
    3: "INFEASIBLE",
    4: "INF_OR_UNBD",
    5: "UNBOUNDED",
    6: "CUTOFF",
    7: "ITERATION_LIMIT",
    8: "NODE_LIMIT",
    9: "TIME_LIMIT",
    10: "SOLUTION_LIMIT",
    11: "INTERRUPTED",
    12: "NUMERIC",
    13: "SUBOPTIMAL",
    14: "INPROGRESS",
    15: "USER_OBJ_LIMIT",
    16: "WORK_LIMIT",
    17: "MEM_LIMIT",
}


@dataclass
class PhysicalBatteryLP:
    """Shared physical variables and constraints for the best-case dispatch cases."""

    model: Any
    env: Any
    gp: Any
    config: BatteryConfig
    frame: pd.DataFrame
    charge: Any
    discharge: Any
    soc: Any
    peak_annual: Any
    peak_month: Any
    month_labels: list[str]
    dt: np.ndarray
    import0: np.ndarray
    export0: np.ndarray
    year_fraction: float
    allowed_stored_throughput_kwh: float

    @property
    def n(self) -> int:
        return int(len(self.frame))


def build_physical_lp(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    output_flag: int = 0,
    enforce_cycle_limit: bool = True,
) -> PhysicalBatteryLP:
    if frame.empty:
        raise OptimizerError("Cannot optimize an empty interval frame")
    work = frame.sort_values("timestamp_utc").reset_index(drop=True)
    gp, env = start_gurobi_env(output_flag=output_flag)
    model = gp.Model("btm_physical_battery", env=env)
    model.ModelSense = gp.GRB.MINIMIZE

    n = len(work)
    dt = (
        work["interval_hours"].to_numpy(dtype=float)
        if "interval_hours" in work.columns
        else np.full(n, INTERVAL_HOURS, dtype=float)
    )
    import0 = work["grid_import_baseline_kwh"].to_numpy(dtype=float)
    export0 = work["grid_export_baseline_kwh"].to_numpy(dtype=float)
    ub_charge = np.minimum(np.maximum(export0, 0.0), config.p_charge_kw * dt)
    ub_discharge = np.minimum(np.maximum(import0, 0.0), config.p_discharge_kw * dt)
    if config.e_usable_kwh <= FLOAT_EPS_KWH:
        # Zero usable capacity cannot time-share a fictitious store in-interval.
        ub_charge = np.zeros(n, dtype=float)
        ub_discharge = np.zeros(n, dtype=float)

    charge = model.addMVar(n, lb=0.0, ub=ub_charge, name="charge_pv")
    discharge = model.addMVar(n, lb=0.0, ub=ub_discharge, name="discharge_load")
    soc = model.addMVar(n + 1, lb=0.0, ub=config.e_usable_kwh, name="soc")
    model.addConstr(soc[0] == config.soc_initial_kwh, name="soc_initial")
    model.addConstr(soc[n] == config.soc_initial_kwh, name="soc_terminal")
    model.addConstr(
        soc[1:] == soc[:n] + config.eta_charge * charge - discharge / config.eta_discharge,
        name="soc_transition",
    )
    if config.p_charge_kw > FLOAT_EPS_KWH and config.p_discharge_kw > FLOAT_EPS_KWH:
        model.addConstr(
            charge / config.p_charge_kw + discharge / config.p_discharge_kw <= dt,
            name="inverter_time",
        )

    year_fraction = selected_period_year_fraction(work)
    allowed_throughput = allowed_stored_throughput_kwh(config, year_fraction)
    if enforce_cycle_limit:
        model.addConstr(
            config.eta_charge * charge.sum() + discharge.sum() / config.eta_discharge
            <= allowed_throughput,
            name="cycle_throughput_limit",
        )

    import_kw = (import0 - discharge) / dt
    peak_annual = model.addVar(lb=0.0, name="peak_annual_kw")
    model.addConstr(peak_annual >= import_kw, name="annual_peak")

    month_labels, month_groups = local_month_groups(work)
    peak_month = model.addMVar(len(month_labels), lb=0.0, name="peak_month_kw")
    for month_index, rows in enumerate(month_groups):
        for row in rows:
            model.addConstr(
                peak_month[month_index] >= import_kw[row],
                name=f"month_peak_{month_labels[month_index]}_{row}",
            )

    if model.NumIntVars != 0 or model.NumBinVars != 0:
        raise OptimizerError(
            "Physical battery model must remain a continuous LP (no integer/binary variables)",
            details={"NumIntVars": int(model.NumIntVars), "NumBinVars": int(model.NumBinVars)},
        )
    model.update()
    return PhysicalBatteryLP(
        model=model,
        env=env,
        gp=gp,
        config=config,
        frame=work,
        charge=charge,
        discharge=discharge,
        soc=soc,
        peak_annual=peak_annual,
        peak_month=peak_month,
        month_labels=month_labels,
        dt=dt,
        import0=import0,
        export0=export0,
        year_fraction=year_fraction,
        allowed_stored_throughput_kwh=allowed_throughput,
    )


def optimize_stage(lp: PhysicalBatteryLP, *, stage: str) -> dict[str, Any]:
    model = lp.model
    model.optimize()
    status_code = int(model.Status)
    status = STATUS_NAMES.get(status_code, str(status_code))
    runtime = float(model.Runtime)
    if status_code != lp.gp.GRB.OPTIMAL:
        raise OptimizerError(
            f"Gurobi did not return an optimal solution at stage {stage!r}: {status}",
            status=status,
            stage=stage,
            details={"status_code": status_code, "runtime_s": runtime},
        )
    return {
        "stage": stage,
        "status": status,
        "status_code": status_code,
        "objective_value": float(model.ObjVal),
        "runtime_s": runtime,
        "iter_count": float(model.IterCount),
    }


def dispose_lp(lp: PhysicalBatteryLP) -> None:
    try:
        lp.model.dispose()
    finally:
        lp.env.dispose()


def solve_stages_respecting_cycle_limit(
    frame: pd.DataFrame,
    config: BatteryConfig,
    solve_stages,
    *,
    output_flag: int = 0,
) -> tuple[PhysicalBatteryLP, list[dict[str, Any]]]:
    """Solve the existing lexicographic stages, adding the cycle cut only if needed.

    A redundant annual-cycle row can move Gurobi to another equally optimal
    vertex and change reported peak or settlement totals. If the established
    schedule already respects the prorated budget, keep it.
    """
    from btm_sim.battery.physics import stored_throughput_kwh

    lp = build_physical_lp(frame, config, output_flag=output_flag, enforce_cycle_limit=False)
    stages = solve_stages(lp)
    actual = stored_throughput_kwh(
        np.asarray(lp.charge.X, dtype=float),
        np.asarray(lp.discharge.X, dtype=float),
        config,
    )
    if actual <= lp.allowed_stored_throughput_kwh + DOCUMENTED_TOLERANCE_KWH:
        return lp, stages
    dispose_lp(lp)
    lp = build_physical_lp(frame, config, output_flag=output_flag, enforce_cycle_limit=True)
    stages = solve_stages(lp)
    return lp, stages
