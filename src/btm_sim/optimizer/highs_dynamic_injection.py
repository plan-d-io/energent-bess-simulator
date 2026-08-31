"""HiGHS dynamic-injection revenue case (production backend)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.dynamic_injection import DynamicInjectionRun
from btm_sim.optimizer.highs_backend import dispose_highs_lp
from btm_sim.optimizer.highs_export import (
    assert_no_export_second_solve_feasible_highs,
    assert_no_simultaneous_import_and_battery_export,
    assert_preserved_customer_dispatch,
    export_solver_metadata,
    solve_highs_export_stages,
)
from btm_sim.optimizer.highs_self_consumption import optimize_self_consumption_highs
from btm_sim.optimizer.reporting import build_optimization_summary, dispatch_from_solution, postcheck_dispatch
from btm_sim.optimizer.self_consumption import SelfConsumptionRun
from btm_sim.settlement.ledger import settle_dynamic_dispatch


def optimize_dynamic_injection_highs(
    frame: pd.DataFrame,
    config: BatteryConfig,
    prices_eur_mwh: np.ndarray | pd.Series,
    *,
    tariffs=None,
    output_flag: int = 0,
    customer_first: SelfConsumptionRun | None = None,
) -> DynamicInjectionRun:
    """Preserve a customer-first schedule, then value remaining flexibility at DA prices."""
    from btm_sim.config.schema import TariffConfig

    started = time.perf_counter()
    tariffs = tariffs if tariffs is not None else TariffConfig()
    if customer_first is None:
        customer_first = optimize_self_consumption_highs(frame, config, output_flag=output_flag)
    work = customer_first.frame.sort_values("timestamp_utc").reset_index(drop=True)
    prices = np.asarray(prices_eur_mwh, dtype=float)
    if len(prices) != len(work):
        raise OptimizerError(
            "Dynamic injection prices must already be aligned to the selected intervals",
            details={"n_prices": int(len(prices)), "n_intervals": int(len(work))},
        )
    if not np.isfinite(prices).all():
        raise OptimizerError("Dynamic injection prices must be finite, including zero and negative values")

    no_export_probe = assert_no_export_second_solve_feasible_highs(
        work,
        config,
        prices,
        output_flag=output_flag,
        model_name="btm_dynamic_injection_highs_no_export",
    )
    lp, stages = solve_highs_export_stages(
        work,
        config,
        prices,
        allow_grid_export=True,
        output_flag=output_flag,
        revenue_stage="maximize_dynamic_injection_revenue_eur",
        revenue_user_label="Highest Energent PV revenue at the supplied dynamic injection prices",
        model_name="btm_dynamic_injection_highs",
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
        dispatched["da_price_eur_mwh"] = prices
        assert_preserved_customer_dispatch(dispatched, discharge_customer)
        assert_no_simultaneous_import_and_battery_export(dispatched)
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

    settled = settle_dynamic_dispatch(dispatched, tariffs)
    summary = _build_summary(
        dispatched,
        config,
        stages=stages,
        solver=solver,
        feasibility=feasibility,
        settlement=settled.totals,
        customer_first=customer_first,
    )
    if not feasibility["ok"]:
        summary["ok"] = False
        summary["battery_limits_and_balances"] = "failed"
        summary["solver"]["status"] = "POSTCHECK_FAILED"
        raise OptimizerError(
            "Solved dynamic-injection schedule failed dispatch-feasibility or energy-balance checks",
            status="POSTCHECK_FAILED",
            details={"feasibility": feasibility},
        )
    return DynamicInjectionRun(
        frame=dispatched,
        summary=summary,
        config=config,
        stages=stages,
        self_consumption=customer_first,
    )


def _build_summary(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    stages: list,
    solver: dict,
    feasibility: dict,
    settlement: dict,
    customer_first: SelfConsumptionRun,
) -> dict:
    summary = build_optimization_summary(
        frame,
        config,
        case="dynamic_injection",
        result_description=(
            "The battery first preserves the best achievable PV supply to the customer. "
            "Remaining battery flexibility may inject stored PV at the supplied dynamic "
            "price. The battery never charges from the grid. The revenue difference is "
            "measured against the current fixed-tariff no-battery situation and therefore "
            "also includes the change of injection tariff."
        ),
        interpretation=(
            "Best-case dynamic injection result using the complete selected period in "
            "advance. Not a forecast, profit, or NPV."
        ),
        stages=stages,
        solver=solver,
        feasibility=feasibility,
    )
    summary["revenue"] = settlement
    summary["preserved_customer_discharge_kwh"] = float(frame["discharge_load_kwh"].sum())
    summary["battery_discharge_to_grid_kwh"] = float(frame["discharge_grid_kwh"].sum())
    summary["self_consumption_solver"] = customer_first.summary.get("solver")
    return summary
