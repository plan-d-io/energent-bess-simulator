"""HiGHS Peak-reduction hierarchy (production backend)."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.constants import LEXICO_TOL_KWH, LEXICO_TOL_KW
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.highs_backend import (
    HighsPhysicalLP,
    add_highs_keep_row,
    dispose_highs_lp,
    highs_solver_metadata,
    optimize_highs_stage,
    set_highs_objective,
    solve_highs_stages_respecting_cycle_limit,
)
from btm_sim.optimizer.peak_reduction import PeakReductionRun
from btm_sim.optimizer.reporting import (
    build_peak_reduction_summary,
    dispatch_from_solution,
    postcheck_dispatch,
)


def optimize_peak_reduction_highs(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    output_flag: int = 0,
) -> PeakReductionRun:
    """Solve Peak reduction with HiGHS."""
    started = time.perf_counter()
    lp, stages, cycle_cut_applied = solve_highs_stages_respecting_cycle_limit(
        frame, config, _solve_priority_order, output_flag=output_flag
    )
    try:
        dispatched = dispatch_from_solution(
            lp.frame,
            config=config,
            charge=np.asarray(lp.charge_values, dtype=float),
            discharge=np.asarray(lp.discharge_values, dtype=float),
            soc=np.asarray(lp.soc_values, dtype=float),
            dt=lp.dt,
            import0=lp.import0,
            export0=lp.export0,
        )
        feasibility = postcheck_dispatch(dispatched, config)
        end_to_end_s = time.perf_counter() - started
        solver = highs_solver_metadata(
            lp,
            stages,
            feasibility_ok=bool(feasibility.get("ok")),
            cycle_cut_applied=cycle_cut_applied,
            end_to_end_s=end_to_end_s,
        )
    finally:
        dispose_highs_lp(lp)
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
        raise OptimizerError(
            "Solved schedule failed dispatch-feasibility or energy-balance checks",
            status="POSTCHECK_FAILED",
            details={"feasibility": feasibility},
        )
    return PeakReductionRun(frame=dispatched, summary=summary, config=config, stages=stages)


def _solve_priority_order(lp: HighsPhysicalLP) -> list[dict[str, Any]]:
    inf = float(lp.highspy.kHighsInf)
    stages: list[dict[str, Any]] = []
    n = lp.n

    set_highs_objective(lp, np.array([lp.idx_peak_annual], dtype=np.int32), np.array([1.0]), maximize=False)
    stage1 = optimize_highs_stage(lp, stage="minimize_annual_peak_import_kw")
    peak_opt = float(stage1["objective_value"])
    stage1["optimum"] = peak_opt
    stage1["unit"] = "kW"
    stage1["tolerance"] = LEXICO_TOL_KW
    stage1["user_label"] = "Lowest annual quarter-hour grid-import peak"
    add_highs_keep_row(
        lp,
        columns=np.array([lp.idx_peak_annual], dtype=np.int32),
        values=np.array([1.0], dtype=np.float64),
        lower=-inf,
        upper=peak_opt + LEXICO_TOL_KW,
    )
    stages.append(stage1)

    n_months = len(lp.month_labels)
    month_tol = LEXICO_TOL_KW * max(n_months, 1)
    month_cols = np.arange(lp.idx_peak_month, lp.idx_peak_month + n_months, dtype=np.int32)
    set_highs_objective(lp, month_cols, np.ones(n_months, dtype=np.float64), maximize=False)
    stage2 = optimize_highs_stage(lp, stage="minimize_sum_monthly_peak_import_kw")
    monthly_opt = float(stage2["objective_value"])
    stage2["optimum"] = monthly_opt
    stage2["unit"] = "kW"
    stage2["tolerance"] = month_tol
    stage2["n_months"] = n_months
    stage2["month_labels"] = lp.month_labels
    stage2["user_label"] = "Sum of monthly quarter-hour grid-import peaks"
    add_highs_keep_row(
        lp,
        columns=month_cols,
        values=np.ones(n_months, dtype=np.float64),
        lower=-inf,
        upper=monthly_opt + month_tol,
    )
    stages.append(stage2)

    discharge_cols = np.arange(lp.idx_discharge, lp.idx_discharge + n, dtype=np.int32)
    set_highs_objective(lp, discharge_cols, np.ones(n, dtype=np.float64), maximize=True)
    stage3 = optimize_highs_stage(lp, stage="maximize_discharge_load_kwh")
    discharge_opt = float(stage3["objective_value"])
    stage3["optimum"] = discharge_opt
    stage3["unit"] = "kWh"
    stage3["tolerance"] = LEXICO_TOL_KWH
    stage3["user_label"] = "Additional PV energy delivered from the battery to the customer"
    stages.append(stage3)
    return stages
