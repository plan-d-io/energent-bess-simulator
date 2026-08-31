"""Gurobi Peak-reduction hierarchy for explicit differential tests only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.constants import LEXICO_TOL_KWH, LEXICO_TOL_KW
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.model import PhysicalBatteryLP, optimize_stage, solve_stages_respecting_cycle_limit
from btm_sim.optimizer.reporting import (
    build_peak_reduction_summary,
    dispatch_from_lp,
    postcheck_dispatch,
    solver_metadata,
    write_peak_reduction_outputs,
)


@dataclass
class PeakReductionRun:
    frame: pd.DataFrame
    summary: dict[str, Any]
    config: BatteryConfig
    stages: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))


def optimize_peak_reduction_gurobi(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    output_dir: str | None = None,
    source_path=None,
    output_flag: int = 0,
) -> PeakReductionRun:
    """Solve the peak-reduction-first case and optionally write audit files."""
    lp, stages = solve_stages_respecting_cycle_limit(
        frame, config, _solve_priority_order, output_flag=output_flag
    )
    dispatched = dispatch_from_lp(lp)
    feasibility = postcheck_dispatch(dispatched, config)
    solver = solver_metadata(lp, stages, feasibility_ok=bool(feasibility.get("ok")))
    summary = build_peak_reduction_summary(
        dispatched,
        config,
        stages=stages,
        solver=solver,
        feasibility=feasibility,
    )
    if not feasibility["ok"]:
        summary["ok"] = False
        summary["battery_limits_and_balances"] = "failed"
        summary["solver"]["status"] = "POSTCHECK_FAILED"
    result = PeakReductionRun(frame=dispatched, summary=summary, config=config, stages=stages)
    if output_dir is not None:
        write_peak_reduction_outputs(dispatched, summary, output_dir, source_path=source_path)
    if not feasibility["ok"]:
        raise OptimizerError(
            "Solved schedule failed dispatch-feasibility or energy-balance checks",
            status="POSTCHECK_FAILED",
            details={"feasibility": feasibility},
        )
    return result


def _solve_priority_order(lp: PhysicalBatteryLP) -> list[dict[str, Any]]:
    gp = lp.gp
    model = lp.model
    stages: list[dict[str, Any]] = []

    model.setObjective(lp.peak_annual, gp.GRB.MINIMIZE)
    stage1 = optimize_stage(lp, stage="minimize_annual_peak_import_kw")
    peak_opt = float(stage1["objective_value"])
    stage1["optimum"] = peak_opt
    stage1["unit"] = "kW"
    stage1["tolerance"] = LEXICO_TOL_KW
    stage1["user_label"] = "Lowest annual quarter-hour grid-import peak"
    model.addConstr(
        lp.peak_annual <= peak_opt + LEXICO_TOL_KW,
        name="keep_annual_peak",
    )
    stages.append(stage1)

    n_months = len(lp.month_labels)
    month_tol = LEXICO_TOL_KW * max(n_months, 1)
    model.setObjective(lp.peak_month.sum(), gp.GRB.MINIMIZE)
    stage2 = optimize_stage(lp, stage="minimize_sum_monthly_peak_import_kw")
    monthly_opt = float(stage2["objective_value"])
    stage2["optimum"] = monthly_opt
    stage2["unit"] = "kW"
    stage2["tolerance"] = month_tol
    stage2["n_months"] = n_months
    stage2["month_labels"] = lp.month_labels
    stage2["user_label"] = "Sum of monthly quarter-hour grid-import peaks"
    model.addConstr(
        lp.peak_month.sum() <= monthly_opt + month_tol,
        name="keep_monthly_peaks",
    )
    stages.append(stage2)

    model.setObjective(lp.discharge.sum(), gp.GRB.MAXIMIZE)
    stage3 = optimize_stage(lp, stage="maximize_discharge_load_kwh")
    discharge_opt = float(stage3["objective_value"])
    stage3["optimum"] = discharge_opt
    stage3["unit"] = "kWh"
    stage3["tolerance"] = LEXICO_TOL_KWH
    stage3["user_label"] = "Additional PV energy delivered from the battery to the customer"
    stages.append(stage3)
    return stages
