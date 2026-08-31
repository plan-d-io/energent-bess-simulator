"""Gurobi fixed-tariff Revenue maximisation for explicit differential tests only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import allowed_stored_throughput_kwh, selected_period_year_fraction
from btm_sim.config.schema import TariffConfig
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, FLOAT_EPS_KWH, INTERVAL_HOURS
from btm_sim.optimizer.constants import LEXICO_TOL_EUR
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.model import STATUS_NAMES, dispose_lp, start_gurobi_env
from btm_sim.optimizer.reporting import (
    build_revenue_summary,
    dispatch_from_solution,
    postcheck_dispatch,
    solver_metadata,
    write_revenue_outputs,
)
from btm_sim.optimizer.gurobi_self_consumption import (
    optimize_self_consumption_gurobi,
)
from btm_sim.optimizer.self_consumption import SelfConsumptionRun
from btm_sim.settlement.ledger import attach_ledger_columns, settle_dispatch
from btm_sim.settlement.tariffs import classify_frame


@dataclass
class RevenueRun:
    frame: pd.DataFrame
    summary: dict[str, Any]
    config: BatteryConfig
    tariffs: TariffConfig
    stages: list[dict[str, Any]]
    self_consumption: SelfConsumptionRun | None = None

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))


def optimize_revenue_gurobi(
    frame: pd.DataFrame,
    config: BatteryConfig,
    tariffs: TariffConfig | None = None,
    *,
    output_dir: str | None = None,
    source_path=None,
    output_flag: int = 0,
    customer_first: SelfConsumptionRun | None = None,
) -> RevenueRun:
    """Preserve customer-first dispatch, then value remaining flexibility at the fixed tariff."""
    tariffs = tariffs if tariffs is not None else TariffConfig()
    if customer_first is None:
        customer_first = optimize_self_consumption_gurobi(frame, config, output_flag=output_flag)
    work = customer_first.frame.sort_values("timestamp_utc").reset_index(drop=True)
    classified = classify_frame(work, tariffs)
    r_export = classified["export_rate_eur_per_mwh"].to_numpy(dtype=float)

    _assert_no_export_second_solve_feasible(work, config, r_export, output_flag=output_flag)
    lp, stages = _solve_fixed_export_lp(
        work, config, r_export, allow_grid_export=True, output_flag=output_flag
    )
    charge = np.asarray(lp.charge.X, dtype=float)
    discharge_grid = np.asarray(lp.discharge_grid.X, dtype=float)
    soc = np.asarray(lp.soc.X, dtype=float)
    discharge_customer = lp.discharge_customer
    dispatched = dispatch_from_solution(
        lp.frame,
        config=config,
        charge=charge,
        discharge=discharge_customer,
        soc=soc,
        dt=lp.dt,
        import0=lp.import0,
        export0=lp.export0,
        discharge_grid=discharge_grid,
    )
    _assert_preserved_customer_dispatch(dispatched, discharge_customer)
    _assert_no_simultaneous_import_and_battery_export(dispatched)
    dispatched = attach_ledger_columns(dispatched, tariffs)
    feasibility = postcheck_dispatch(dispatched, config)
    solver = solver_metadata(lp, stages, feasibility_ok=bool(feasibility.get("ok")))
    if solver["num_int_vars"] != 0 or solver["num_bin_vars"] != 0:
        dispose_lp(lp)
        raise OptimizerError(
            "Revenue maximisation model must remain a continuous LP (no integer/binary variables)",
            details={"NumIntVars": solver["num_int_vars"], "NumBinVars": solver["num_bin_vars"]},
        )
    settled = settle_dispatch(dispatched, tariffs)
    summary = build_revenue_summary(
        dispatched,
        config,
        tariffs=tariffs,
        stages=stages,
        solver=solver,
        feasibility=feasibility,
        settlement=settled.totals,
    )
    summary["preserved_customer_discharge_kwh"] = float(dispatched["discharge_load_kwh"].sum())
    summary["battery_discharge_to_grid_kwh"] = float(dispatched["discharge_grid_kwh"].sum())
    summary["self_consumption_solver"] = customer_first.summary.get("solver")
    if not feasibility["ok"]:
        summary["ok"] = False
        summary["battery_limits_and_balances"] = "failed"
        summary["solver"]["status"] = "POSTCHECK_FAILED"
        dispose_lp(lp)
        raise OptimizerError(
            "Solved schedule failed dispatch-feasibility, energy-balance, or revenue checks",
            status="POSTCHECK_FAILED",
            details={"feasibility": feasibility},
        )
    dispose_lp(lp)
    result = RevenueRun(
        frame=dispatched,
        summary=summary,
        config=config,
        tariffs=tariffs,
        stages=stages,
        self_consumption=customer_first,
    )
    if output_dir is not None:
        write_revenue_outputs(dispatched, summary, output_dir, source_path=source_path)
    return result


class _FixedExportLP:
    def __init__(self, **payload: Any) -> None:
        self.__dict__.update(payload)


def _solve_fixed_export_lp(
    work: pd.DataFrame,
    config: BatteryConfig,
    r_export: np.ndarray,
    *,
    allow_grid_export: bool,
    output_flag: int,
) -> tuple[Any, list[dict[str, Any]]]:
    gp, env = start_gurobi_env(output_flag=output_flag)
    model = gp.Model("btm_fixed_tariff_revenue", env=env)
    n = len(work)
    dt = (
        work["interval_hours"].to_numpy(dtype=float)
        if "interval_hours" in work.columns
        else np.full(n, INTERVAL_HOURS, dtype=float)
    )
    import0 = work["grid_import_baseline_kwh"].to_numpy(dtype=float)
    export0 = work["grid_export_baseline_kwh"].to_numpy(dtype=float)
    discharge_customer = work["discharge_load_kwh"].to_numpy(dtype=float)
    sc_import = work["grid_import_kwh"].to_numpy(dtype=float)
    ub_charge = np.minimum(np.maximum(export0, 0.0), config.p_charge_kw * dt)
    remaining_discharge = np.maximum(config.p_discharge_kw * dt - discharge_customer, 0.0)
    ub_grid = remaining_discharge.copy()
    ub_grid[sc_import > DOCUMENTED_TOLERANCE_KWH] = 0.0
    if not allow_grid_export or config.e_usable_kwh <= FLOAT_EPS_KWH:
        ub_grid[:] = 0.0
        if config.e_usable_kwh <= FLOAT_EPS_KWH:
            ub_charge[:] = 0.0

    charge = model.addMVar(n, lb=0.0, ub=ub_charge, name="charge_pv")
    discharge_grid = model.addMVar(n, lb=0.0, ub=ub_grid, name="discharge_grid")
    soc = model.addMVar(n + 1, lb=0.0, ub=config.e_usable_kwh, name="soc")
    model.addConstr(soc[0] == config.soc_initial_kwh, name="soc_initial")
    model.addConstr(soc[n] == config.soc_initial_kwh, name="soc_terminal")
    model.addConstr(
        soc[1:]
        == soc[:n]
        + config.eta_charge * charge
        - (discharge_customer + discharge_grid) / config.eta_discharge,
        name="soc_transition",
    )
    if config.p_charge_kw > FLOAT_EPS_KWH and config.p_discharge_kw > FLOAT_EPS_KWH:
        model.addConstr(
            charge / config.p_charge_kw
            + (discharge_customer + discharge_grid) / config.p_discharge_kw
            <= dt,
            name="inverter_time",
        )
    year_fraction = selected_period_year_fraction(work)
    allowed_throughput = allowed_stored_throughput_kwh(config, year_fraction)
    model.addConstr(
        config.eta_charge * charge.sum()
        + (float(discharge_customer.sum()) + discharge_grid.sum()) / config.eta_discharge
        <= allowed_throughput,
        name="cycle_throughput_limit",
    )
    model.update()
    if model.NumIntVars != 0 or model.NumBinVars != 0:
        env.dispose()
        raise OptimizerError(
            "Revenue maximisation model must remain a continuous LP (no integer/binary variables)",
            details={"NumIntVars": int(model.NumIntVars), "NumBinVars": int(model.NumBinVars)},
        )

    revenue_expr = gp.quicksum(
        (float(r_export[t]) / 1000.0) * (discharge_grid[t] - charge[t]) for t in range(n)
    )
    stages: list[dict[str, Any]] = []
    model.setObjective(revenue_expr, gp.GRB.MAXIMIZE)
    stage1 = _optimize(model, gp, stage="maximize_energent_pv_revenue_eur")
    revenue_opt = float(stage1["objective_value"])
    stage1["optimum"] = revenue_opt
    stage1["unit"] = "EUR"
    stage1["tolerance"] = LEXICO_TOL_EUR
    stage1["user_label"] = (
        "Highest remaining fixed-tariff injection revenue after preserving customer PV supply"
    )
    model.addConstr(revenue_expr >= revenue_opt - LEXICO_TOL_EUR, name="keep_revenue")
    stages.append(stage1)

    throughput = config.eta_charge * charge.sum() + discharge_grid.sum() / config.eta_discharge
    model.setObjective(throughput, gp.GRB.MINIMIZE)
    stage2 = _optimize(model, gp, stage="minimize_stored_throughput_kwh")
    stage2["optimum"] = float(stage2["objective_value"])
    stage2["unit"] = "kWh"
    stage2["tolerance"] = 1e-9
    stage2["user_label"] = "Lowest stored-energy throughput among equally valuable schedules"
    stages.append(stage2)

    lp = _FixedExportLP(
        model=model,
        env=env,
        gp=gp,
        config=config,
        frame=work,
        charge=charge,
        discharge_grid=discharge_grid,
        discharge_customer=discharge_customer,
        soc=soc,
        dt=dt,
        import0=import0,
        export0=export0,
    )
    return lp, stages


def _assert_no_export_second_solve_feasible(
    work: pd.DataFrame,
    config: BatteryConfig,
    r_export: np.ndarray,
    *,
    output_flag: int,
) -> None:
    lp, _stages = _solve_fixed_export_lp(
        work, config, r_export, allow_grid_export=False, output_flag=output_flag
    )
    recovered = np.asarray(lp.frame["discharge_load_kwh"], dtype=float)
    _assert_preserved_customer_dispatch(lp.frame.assign(discharge_load_kwh=recovered), recovered)
    grid = np.asarray(lp.discharge_grid.X, dtype=float)
    if float(np.max(np.abs(grid))) > DOCUMENTED_TOLERANCE_KWH:
        dispose_lp(lp)
        raise OptimizerError(
            "No-export revenue solve produced battery grid injection",
            details={"max_abs_discharge_grid_kwh": float(np.max(np.abs(grid)))},
        )
    dispose_lp(lp)


def _assert_preserved_customer_dispatch(frame: pd.DataFrame, expected_customer: np.ndarray) -> None:
    actual = frame["discharge_load_kwh"].to_numpy(dtype=float)
    gap = float(np.max(np.abs(actual - expected_customer))) if len(actual) else 0.0
    if gap > DOCUMENTED_TOLERANCE_KWH:
        raise OptimizerError(
            "Revenue maximisation did not preserve the customer-first discharge schedule",
            details={"max_abs_kwh": gap, "tolerance_kwh": DOCUMENTED_TOLERANCE_KWH},
        )


def _assert_no_simultaneous_import_and_battery_export(frame: pd.DataFrame) -> None:
    imp = frame["grid_import_kwh"].to_numpy(dtype=float)
    grid = frame["discharge_grid_kwh"].to_numpy(dtype=float)
    bad = (imp > DOCUMENTED_TOLERANCE_KWH) & (grid > DOCUMENTED_TOLERANCE_KWH)
    if bool(np.any(bad)):
        raise OptimizerError(
            "Battery grid export occurred in an interval that still has material grid import",
            details={"n_intervals": int(np.sum(bad))},
        )


def _optimize(model: Any, gp: Any, *, stage: str) -> dict[str, Any]:
    model.optimize()
    status_code = int(model.Status)
    status = STATUS_NAMES.get(status_code, str(status_code))
    runtime = float(model.Runtime)
    if status_code != gp.GRB.OPTIMAL:
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
