"""Shared sparse HiGHS export LP for fixed and dynamic revenue cases."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import allowed_stored_throughput_kwh, selected_period_year_fraction
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, FLOAT_EPS_KWH, INTERVAL_HOURS
from btm_sim.optimizer.constants import LEXICO_TOL_EUR
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.highs_backend import (
    _apply_highs_options,
    _assert_continuous_lp,
    _release_highs_log_file,
    add_highs_keep_row,
    dispose_highs_lp,
    flush_highs_log,
    highs_options,
    highs_solver_metadata,
    import_highspy,
    optimize_highs_stage,
    set_highs_objective,
)


@dataclass
class HighsExportLP:
    highs: Any
    highspy: Any
    config: BatteryConfig
    frame: pd.DataFrame
    dt: np.ndarray
    import0: np.ndarray
    export0: np.ndarray
    discharge_customer: np.ndarray
    injection_value_eur_mwh: np.ndarray
    year_fraction: float
    allowed_stored_throughput_kwh: float
    n: int
    idx_charge: int
    idx_discharge_grid: int
    idx_soc: int
    num_col: int
    num_row_physical: int
    num_nz: int
    allow_grid_export: bool
    matrix_build_s: float
    options: dict[str, Any]
    log_file_path: str | None = None
    log_file_offset: int = 0
    col_value: np.ndarray | None = None
    last_info: dict[str, Any] = field(default_factory=dict)

    @property
    def charge_values(self) -> np.ndarray:
        return self._require_solution()[self.idx_charge : self.idx_charge + self.n]

    @property
    def discharge_grid_values(self) -> np.ndarray:
        return self._require_solution()[self.idx_discharge_grid : self.idx_discharge_grid + self.n]

    @property
    def soc_values(self) -> np.ndarray:
        return self._require_solution()[self.idx_soc : self.idx_soc + self.n + 1]

    def _require_solution(self) -> np.ndarray:
        if self.col_value is None:
            raise OptimizerError("HiGHS export LP has no stored solution")
        return self.col_value


def build_highs_export_lp(
    work: pd.DataFrame,
    config: BatteryConfig,
    injection_value_eur_mwh: np.ndarray,
    *,
    allow_grid_export: bool,
    output_flag: int = 0,
    model_name: str = "btm_export_highs",
) -> HighsExportLP:
    if work.empty:
        raise OptimizerError("Cannot optimize an empty interval frame")
    highspy = import_highspy()
    build_started = time.perf_counter()
    n = len(work)
    prices = np.asarray(injection_value_eur_mwh, dtype=float)
    if len(prices) != n:
        raise OptimizerError(
            "Injection value vector must match the frozen customer-first frame",
            details={"n_values": int(len(prices)), "n_intervals": int(n)},
        )
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

    use_inverter = config.p_charge_kw > FLOAT_EPS_KWH and config.p_discharge_kw > FLOAT_EPS_KWH
    year_fraction = selected_period_year_fraction(work)
    allowed_throughput = allowed_stored_throughput_kwh(config, year_fraction)
    fixed_customer_throughput = float(discharge_customer.sum()) / config.eta_discharge
    remaining_throughput = allowed_throughput - fixed_customer_throughput

    idx_charge = 0
    idx_discharge_grid = n
    idx_soc = 2 * n
    num_col = 2 * n + (n + 1)

    row_soc_init = 0
    row_soc_term = 1
    row_soc_trans = 2
    next_row = 2 + n
    row_inverter = next_row if use_inverter else None
    if use_inverter:
        next_row += n
    row_cycle = next_row
    next_row += 1
    num_row = next_row

    inf = float(highspy.kHighsInf)
    col_lower = np.zeros(num_col, dtype=np.float64)
    col_upper = np.empty(num_col, dtype=np.float64)
    col_upper[idx_charge : idx_charge + n] = ub_charge
    col_upper[idx_discharge_grid : idx_discharge_grid + n] = ub_grid
    col_upper[idx_soc : idx_soc + n + 1] = config.e_usable_kwh
    col_cost = np.zeros(num_col, dtype=np.float64)

    row_lower = np.empty(num_row, dtype=np.float64)
    row_upper = np.empty(num_row, dtype=np.float64)
    row_lower[row_soc_init] = config.soc_initial_kwh
    row_upper[row_soc_init] = config.soc_initial_kwh
    row_lower[row_soc_term] = config.soc_initial_kwh
    row_upper[row_soc_term] = config.soc_initial_kwh
    # soc[t+1] - soc[t] - eta_c * charge + (d_cust + d_grid)/eta_d = 0
    # => soc[t+1] - soc[t] - eta_c * charge + d_grid/eta_d = -d_cust/eta_d
    inv_eta_d = 1.0 / config.eta_discharge
    row_lower[row_soc_trans : row_soc_trans + n] = -discharge_customer * inv_eta_d
    row_upper[row_soc_trans : row_soc_trans + n] = -discharge_customer * inv_eta_d
    if use_inverter:
        # charge/P_c + (d_cust + d_grid)/P_d <= dt
        # charge/P_c + d_grid/P_d <= dt - d_cust/P_d
        row_lower[row_inverter : row_inverter + n] = -inf
        row_upper[row_inverter : row_inverter + n] = dt - discharge_customer / config.p_discharge_kw
    row_lower[row_cycle] = -inf
    row_upper[row_cycle] = remaining_throughput

    entries: list[list[tuple[int, float]]] = [[] for _ in range(num_col)]

    def put(col: int, row: int, value: float) -> None:
        if value == 0.0:
            return
        entries[col].append((row, value))

    inv_charge = (1.0 / config.p_charge_kw) if use_inverter else 0.0
    inv_discharge = (1.0 / config.p_discharge_kw) if use_inverter else 0.0
    for t in range(n):
        put(idx_charge + t, row_soc_trans + t, -config.eta_charge)
        put(idx_discharge_grid + t, row_soc_trans + t, inv_eta_d)
        if use_inverter:
            put(idx_charge + t, row_inverter + t, inv_charge)
            put(idx_discharge_grid + t, row_inverter + t, inv_discharge)
        put(idx_charge + t, row_cycle, config.eta_charge)
        put(idx_discharge_grid + t, row_cycle, inv_eta_d)

    put(idx_soc, row_soc_init, 1.0)
    put(idx_soc + n, row_soc_term, 1.0)
    for t in range(n):
        put(idx_soc + t, row_soc_trans + t, -1.0)
        put(idx_soc + t + 1, row_soc_trans + t, 1.0)

    starts = np.zeros(num_col + 1, dtype=np.int32)
    for col in range(num_col):
        starts[col + 1] = starts[col] + len(entries[col])
    num_nz = int(starts[-1])
    indices = np.empty(num_nz, dtype=np.int32)
    values = np.empty(num_nz, dtype=np.float64)
    for col in range(num_col):
        start = int(starts[col])
        for offset, (row, value) in enumerate(entries[col]):
            indices[start + offset] = row
            values[start + offset] = value

    lp = highspy.HighsLp()
    lp.num_col_ = num_col
    lp.num_row_ = num_row
    lp.col_cost_ = col_cost
    lp.col_lower_ = col_lower
    lp.col_upper_ = col_upper
    lp.row_lower_ = row_lower
    lp.row_upper_ = row_upper
    lp.sense_ = highspy.ObjSense.kMinimize
    lp.offset_ = 0.0
    lp.model_name_ = model_name
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.num_col_ = num_col
    lp.a_matrix_.num_row_ = num_row
    lp.a_matrix_.start_ = starts
    lp.a_matrix_.index_ = indices
    lp.a_matrix_.value_ = values

    highs = highspy.Highs()
    log_file_path = _apply_highs_options(highs, highspy, output_flag=output_flag)
    options = highs_options(output_flag=output_flag)
    passed = highs.passModel(lp)
    if passed != highspy.HighsStatus.kOk:
        raise OptimizerError(
            "HiGHS rejected the export battery model",
            details={"status": str(passed)},
        )
    _assert_continuous_lp(highs, highspy)
    return HighsExportLP(
        highs=highs,
        highspy=highspy,
        config=config,
        frame=work,
        dt=dt,
        import0=import0,
        export0=export0,
        discharge_customer=discharge_customer,
        injection_value_eur_mwh=prices,
        year_fraction=year_fraction,
        allowed_stored_throughput_kwh=allowed_throughput,
        n=n,
        idx_charge=idx_charge,
        idx_discharge_grid=idx_discharge_grid,
        idx_soc=idx_soc,
        num_col=num_col,
        num_row_physical=num_row,
        num_nz=num_nz,
        allow_grid_export=allow_grid_export,
        matrix_build_s=time.perf_counter() - build_started,
        options=options,
        log_file_path=log_file_path,
    )


def solve_highs_export_stages(
    work: pd.DataFrame,
    config: BatteryConfig,
    injection_value_eur_mwh: np.ndarray,
    *,
    allow_grid_export: bool,
    output_flag: int,
    revenue_stage: str,
    revenue_user_label: str,
    model_name: str,
) -> tuple[HighsExportLP, list[dict[str, Any]]]:
    lp = build_highs_export_lp(
        work,
        config,
        injection_value_eur_mwh,
        allow_grid_export=allow_grid_export,
        output_flag=output_flag,
        model_name=model_name,
    )
    stages = _solve_export_priority(lp, revenue_stage=revenue_stage, revenue_user_label=revenue_user_label)
    return lp, stages


def assert_no_export_second_solve_feasible_highs(
    work: pd.DataFrame,
    config: BatteryConfig,
    injection_value_eur_mwh: np.ndarray,
    *,
    output_flag: int,
    model_name: str,
) -> dict[str, Any]:
    lp, stages = solve_highs_export_stages(
        work,
        config,
        injection_value_eur_mwh,
        allow_grid_export=False,
        output_flag=output_flag,
        revenue_stage="maximize_energent_pv_revenue_eur",
        revenue_user_label="No-export feasibility revenue stage",
        model_name=model_name,
    )
    try:
        assert_preserved_customer_dispatch(lp.frame, lp.discharge_customer)
        grid = np.asarray(lp.discharge_grid_values, dtype=float)
        if float(np.max(np.abs(grid))) > DOCUMENTED_TOLERANCE_KWH:
            raise OptimizerError(
                "No-export revenue solve produced battery grid injection",
                details={"max_abs_discharge_grid_kwh": float(np.max(np.abs(grid)))},
            )
        return {
            "matrix_build_s": float(lp.matrix_build_s),
            "num_vars": int(lp.num_col),
            "num_constrs": int(lp.num_row_physical),
            "num_nz": int(lp.num_nz),
            "runtime_s": float(sum(stage["runtime_s"] for stage in stages)),
        }
    finally:
        dispose_highs_lp(lp)


def assert_preserved_customer_dispatch(frame: pd.DataFrame, expected_customer: np.ndarray) -> None:
    actual = frame["discharge_load_kwh"].to_numpy(dtype=float)
    gap = float(np.max(np.abs(actual - expected_customer))) if len(actual) else 0.0
    if gap > DOCUMENTED_TOLERANCE_KWH:
        raise OptimizerError(
            "Revenue maximisation did not preserve the customer-first discharge schedule",
            details={"max_abs_kwh": gap, "tolerance_kwh": DOCUMENTED_TOLERANCE_KWH},
        )


def assert_no_simultaneous_import_and_battery_export(frame: pd.DataFrame) -> None:
    imp = frame["grid_import_kwh"].to_numpy(dtype=float)
    grid = frame["discharge_grid_kwh"].to_numpy(dtype=float)
    bad = (imp > DOCUMENTED_TOLERANCE_KWH) & (grid > DOCUMENTED_TOLERANCE_KWH)
    if bool(np.any(bad)):
        raise OptimizerError(
            "Battery grid export occurred in an interval that still has material grid import",
            details={"n_intervals": int(np.sum(bad))},
        )


def _solve_export_priority(
    lp: HighsExportLP,
    *,
    revenue_stage: str,
    revenue_user_label: str,
) -> list[dict[str, Any]]:
    inf = float(lp.highspy.kHighsInf)
    n = lp.n
    price_eur_kwh = lp.injection_value_eur_mwh / 1000.0
    charge_cols = np.arange(lp.idx_charge, lp.idx_charge + n, dtype=np.int32)
    grid_cols = np.arange(lp.idx_discharge_grid, lp.idx_discharge_grid + n, dtype=np.int32)
    # revenue = sum(p * (dg - c)) = sum(p*dg) + sum((-p)*c)
    revenue_cols = np.concatenate([charge_cols, grid_cols])
    revenue_costs = np.concatenate([-price_eur_kwh, price_eur_kwh]).astype(np.float64)
    set_highs_objective(lp, revenue_cols, revenue_costs, maximize=True)
    stage1 = optimize_highs_stage(lp, stage=revenue_stage)
    revenue_opt = float(stage1["objective_value"])
    stage1["optimum"] = revenue_opt
    stage1["unit"] = "EUR"
    stage1["tolerance"] = LEXICO_TOL_EUR
    stage1["user_label"] = revenue_user_label
    add_highs_keep_row(
        lp,
        columns=revenue_cols,
        values=revenue_costs,
        lower=revenue_opt - LEXICO_TOL_EUR,
        upper=inf,
    )
    stages = [stage1]

    # variable throughput = eta_c * sum(charge) + sum(dg)/eta_d
    throughput_cols = revenue_cols
    throughput_costs = np.concatenate(
        [
            np.full(n, lp.config.eta_charge, dtype=np.float64),
            np.full(n, 1.0 / lp.config.eta_discharge, dtype=np.float64),
        ]
    )
    set_highs_objective(lp, throughput_cols, throughput_costs, maximize=False)
    stage2 = optimize_highs_stage(lp, stage="minimize_stored_throughput_kwh")
    stage2["optimum"] = float(stage2["objective_value"])
    stage2["unit"] = "kWh"
    stage2["tolerance"] = 1e-9
    stage2["user_label"] = "Lowest stored-energy throughput among equally valuable schedules"
    stages.append(stage2)
    return stages


def export_solver_metadata(
    lp: HighsExportLP,
    stages: list[dict[str, Any]],
    *,
    feasibility_ok: bool,
    end_to_end_s: float,
    no_export_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extra = {
        "hard_cycle_budget": True,
        "allow_grid_export": bool(lp.allow_grid_export),
    }
    if no_export_probe is not None:
        extra["no_export_probe"] = no_export_probe
    return highs_solver_metadata(
        lp,
        stages,
        feasibility_ok=feasibility_ok,
        cycle_cut_applied=True,
        end_to_end_s=end_to_end_s,
        extra=extra,
    )
