"""HiGHS fixed-tariff Revenue maximisation (production backend)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.config.schema import TariffConfig
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.highs_backend import dispose_highs_lp
from btm_sim.optimizer.highs_export import (
    assert_no_export_second_solve_feasible_highs,
    assert_no_simultaneous_import_and_battery_export,
    assert_preserved_customer_dispatch,
    export_solver_metadata,
    solve_highs_export_stages,
)
from btm_sim.optimizer.highs_self_consumption import optimize_self_consumption_highs
from btm_sim.optimizer.reporting import (
    build_revenue_summary,
    dispatch_from_solution,
    postcheck_dispatch,
)
from btm_sim.optimizer.revenue import RevenueRun
from btm_sim.optimizer.self_consumption import SelfConsumptionRun
from btm_sim.settlement.ledger import attach_ledger_columns, settle_dispatch
from btm_sim.settlement.tariffs import classify_frame


def optimize_revenue_highs(
    frame: pd.DataFrame,
    config: BatteryConfig,
    tariffs: TariffConfig | None = None,
    *,
    output_flag: int = 0,
    customer_first: SelfConsumptionRun | None = None,
) -> RevenueRun:
    """Preserve a customer-first schedule, then value remaining flexibility at the fixed tariff."""
    started = time.perf_counter()
    tariffs = tariffs if tariffs is not None else TariffConfig()
    if customer_first is None:
        customer_first = optimize_self_consumption_highs(frame, config, output_flag=output_flag)
    work = customer_first.frame.sort_values("timestamp_utc").reset_index(drop=True)
    classified = classify_frame(work, tariffs)
    r_export = classified["export_rate_eur_per_mwh"].to_numpy(dtype=float)

    no_export_probe = assert_no_export_second_solve_feasible_highs(
        work,
        config,
        r_export,
        output_flag=output_flag,
        model_name="btm_fixed_tariff_revenue_highs_no_export",
    )
    lp, stages = solve_highs_export_stages(
        work,
        config,
        r_export,
        allow_grid_export=True,
        output_flag=output_flag,
        revenue_stage="maximize_energent_pv_revenue_eur",
        revenue_user_label=(
            "Highest remaining fixed-tariff injection revenue after preserving customer PV supply"
        ),
        model_name="btm_fixed_tariff_revenue_highs",
    )
    try:
        charge = np.asarray(lp.charge_values, dtype=float)
        discharge_grid = np.asarray(lp.discharge_grid_values, dtype=float)
        soc = np.asarray(lp.soc_values, dtype=float)
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
        assert_preserved_customer_dispatch(dispatched, discharge_customer)
        assert_no_simultaneous_import_and_battery_export(dispatched)
        dispatched = attach_ledger_columns(dispatched, tariffs)
        feasibility = postcheck_dispatch(dispatched, config)
        end_to_end_s = time.perf_counter() - started
        solver = export_solver_metadata(
            lp,
            stages,
            feasibility_ok=bool(feasibility.get("ok")),
            end_to_end_s=end_to_end_s,
            no_export_probe=no_export_probe,
        )
    finally:
        dispose_highs_lp(lp)

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
        raise OptimizerError(
            "Solved schedule failed dispatch-feasibility, energy-balance, or revenue checks",
            status="POSTCHECK_FAILED",
            details={"feasibility": feasibility},
        )
    return RevenueRun(
        frame=dispatched,
        summary=summary,
        config=config,
        tariffs=tariffs,
        stages=stages,
        self_consumption=customer_first,
    )
