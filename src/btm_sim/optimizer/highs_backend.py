"""HiGHS sparse LP of the shared physical battery model (production backend)."""

from __future__ import annotations

import gc
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import allowed_stored_throughput_kwh, selected_period_year_fraction
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, FLOAT_EPS_KWH, INTERVAL_HOURS
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.physical_prep import local_month_groups

# Documented experimental options. HiGHS does not guarantee bit-identical
# solves; random_seed only reduces uncontrolled variation.
HIGHS_RANDOM_SEED = 0
HIGHS_SOLVER = "choose"
HIGHS_PRESOLVE = "on"
STATUS_NAMES = {
    "kOptimal": "OPTIMAL",
    "kInfeasible": "INFEASIBLE",
    "kUnbounded": "UNBOUNDED",
    "kUnboundedOrInfeasible": "INF_OR_UNBD",
    "kTimeLimit": "TIME_LIMIT",
    "kIterationLimit": "ITERATION_LIMIT",
    "kSolutionLimit": "SOLUTION_LIMIT",
    "kInterrupt": "INTERRUPTED",
    "kHighsInterrupt": "INTERRUPTED",
    "kMemoryLimit": "MEM_LIMIT",
    "kUnknown": "UNKNOWN",
    "kNotset": "NOTSET",
    "kModelEmpty": "MODEL_EMPTY",
    "kModelError": "MODEL_ERROR",
    "kLoadError": "LOAD_ERROR",
    "kPresolveError": "PRESOLVE_ERROR",
    "kSolveError": "SOLVE_ERROR",
    "kPostsolveError": "POSTSOLVE_ERROR",
    "kObjectiveBound": "OBJECTIVE_BOUND",
    "kObjectiveTarget": "OBJECTIVE_TARGET",
}


def import_highspy():
    try:
        import highspy
    except ImportError as exc:
        raise OptimizerError(
            "HiGHS is unavailable: the highspy package is not installed. "
            "Install the project normally; highspy is a required dependency."
        ) from exc
    return highspy


def highs_options(*, output_flag: int = 0) -> dict[str, Any]:
    quiet = int(output_flag) == 0
    return {
        "output_flag": not quiet,
        "log_to_console": bool(output_flag),
        "random_seed": HIGHS_RANDOM_SEED,
        "solver": HIGHS_SOLVER,
        "presolve": HIGHS_PRESOLVE,
    }


def _apply_highs_options(highs: Any, highspy: Any, *, output_flag: int) -> str | None:
    options = highs_options(output_flag=output_flag)
    log_path: str | None = None
    if int(output_flag) != 0:
        handle = tempfile.NamedTemporaryFile(prefix="btm_highs_", suffix=".log", delete=False)
        handle.close()
        log_path = handle.name
        options["log_file"] = log_path
        options["log_to_console"] = False
    for name, value in options.items():
        status = highs.setOptionValue(name, value)
        if status != highspy.HighsStatus.kOk:
            raise OptimizerError(
                f"Failed to set HiGHS option {name!r}",
                details={"option": name, "value": value, "status": str(status)},
            )
    return log_path


def flush_highs_log(lp: Any) -> None:
    path = getattr(lp, "log_file_path", None)
    if not path:
        return
    log_path = Path(path)
    if not log_path.exists():
        return
    text = log_path.read_text(encoding="utf-8", errors="replace")
    offset = int(getattr(lp, "log_file_offset", 0))
    if len(text) > offset:
        sys.stdout.write(text[offset:])
        sys.stdout.flush()
        lp.log_file_offset = len(text)


@dataclass
class HighsPhysicalLP:
    """Sparse HiGHS instance of the shared physical battery LP."""

    highs: Any
    highspy: Any
    config: BatteryConfig
    frame: pd.DataFrame
    dt: np.ndarray
    import0: np.ndarray
    export0: np.ndarray
    month_labels: list[str]
    year_fraction: float
    allowed_stored_throughput_kwh: float
    n: int
    idx_charge: int
    idx_discharge: int
    idx_soc: int
    idx_peak_annual: int
    idx_peak_month: int
    num_col: int
    num_row_physical: int
    num_nz: int
    enforce_cycle_limit: bool
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
    def discharge_values(self) -> np.ndarray:
        return self._require_solution()[self.idx_discharge : self.idx_discharge + self.n]

    @property
    def soc_values(self) -> np.ndarray:
        return self._require_solution()[self.idx_soc : self.idx_soc + self.n + 1]

    @property
    def peak_annual_value(self) -> float:
        return float(self._require_solution()[self.idx_peak_annual])

    @property
    def peak_month_values(self) -> np.ndarray:
        n_months = len(self.month_labels)
        return self._require_solution()[self.idx_peak_month : self.idx_peak_month + n_months]

    def _require_solution(self) -> np.ndarray:
        if self.col_value is None:
            raise OptimizerError("HiGHS physical LP has no stored solution")
        return self.col_value


def build_highs_physical_lp(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    output_flag: int = 0,
    enforce_cycle_limit: bool = True,
) -> HighsPhysicalLP:
    if frame.empty:
        raise OptimizerError("Cannot optimize an empty interval frame")
    highspy = import_highspy()
    work = frame.sort_values("timestamp_utc").reset_index(drop=True)
    build_started = time.perf_counter()
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
        ub_charge = np.zeros(n, dtype=float)
        ub_discharge = np.zeros(n, dtype=float)

    use_inverter = config.p_charge_kw > FLOAT_EPS_KWH and config.p_discharge_kw > FLOAT_EPS_KWH
    month_labels, month_groups = local_month_groups(work)
    n_months = len(month_labels)
    year_fraction = selected_period_year_fraction(work)
    allowed_throughput = allowed_stored_throughput_kwh(config, year_fraction)

    idx_charge = 0
    idx_discharge = n
    idx_soc = 2 * n
    idx_peak_annual = 2 * n + (n + 1)
    idx_peak_month = idx_peak_annual + 1
    num_col = idx_peak_month + n_months

    row_soc_init = 0
    row_soc_term = 1
    row_soc_trans = 2
    next_row = 2 + n
    row_inverter = next_row if use_inverter else None
    if use_inverter:
        next_row += n
    row_annual = next_row
    next_row += n
    row_month = next_row
    next_row += n
    row_cycle = next_row if enforce_cycle_limit else None
    if enforce_cycle_limit:
        next_row += 1
    num_row = next_row

    inf = float(highspy.kHighsInf)
    col_lower = np.zeros(num_col, dtype=np.float64)
    col_upper = np.empty(num_col, dtype=np.float64)
    col_upper[idx_charge : idx_charge + n] = ub_charge
    col_upper[idx_discharge : idx_discharge + n] = ub_discharge
    col_upper[idx_soc : idx_soc + n + 1] = config.e_usable_kwh
    col_upper[idx_peak_annual] = inf
    col_upper[idx_peak_month : idx_peak_month + n_months] = inf
    col_cost = np.zeros(num_col, dtype=np.float64)

    row_lower = np.empty(num_row, dtype=np.float64)
    row_upper = np.empty(num_row, dtype=np.float64)
    row_lower[row_soc_init] = config.soc_initial_kwh
    row_upper[row_soc_init] = config.soc_initial_kwh
    row_lower[row_soc_term] = config.soc_initial_kwh
    row_upper[row_soc_term] = config.soc_initial_kwh
    row_lower[row_soc_trans : row_soc_trans + n] = 0.0
    row_upper[row_soc_trans : row_soc_trans + n] = 0.0
    if use_inverter:
        row_lower[row_inverter : row_inverter + n] = -inf
        row_upper[row_inverter : row_inverter + n] = dt
    import_kw = import0 / dt
    row_lower[row_annual : row_annual + n] = import_kw
    row_upper[row_annual : row_annual + n] = inf
    row_lower[row_month : row_month + n] = import_kw
    row_upper[row_month : row_month + n] = inf
    if enforce_cycle_limit:
        row_lower[row_cycle] = -inf
        row_upper[row_cycle] = allowed_throughput

    entries: list[list[tuple[int, float]]] = [[] for _ in range(num_col)]

    def put(col: int, row: int, value: float) -> None:
        if value == 0.0:
            return
        entries[col].append((row, value))

    inv_charge = (1.0 / config.p_charge_kw) if use_inverter else 0.0
    inv_discharge = (1.0 / config.p_discharge_kw) if use_inverter else 0.0
    inv_eta_d = 1.0 / config.eta_discharge
    for t in range(n):
        put(idx_charge + t, row_soc_trans + t, -config.eta_charge)
        put(idx_discharge + t, row_soc_trans + t, inv_eta_d)
        if use_inverter:
            put(idx_charge + t, row_inverter + t, inv_charge)
            put(idx_discharge + t, row_inverter + t, inv_discharge)
        inv_dt = 1.0 / dt[t]
        put(idx_discharge + t, row_annual + t, inv_dt)
        put(idx_peak_annual, row_annual + t, 1.0)
        put(idx_discharge + t, row_month + t, inv_dt)
        if enforce_cycle_limit:
            put(idx_charge + t, row_cycle, config.eta_charge)
            put(idx_discharge + t, row_cycle, inv_eta_d)

    for month_index, rows in enumerate(month_groups):
        peak_col = idx_peak_month + month_index
        for t in rows:
            put(peak_col, row_month + t, 1.0)

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
    lp.model_name_ = "btm_physical_battery_highs"
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
            "HiGHS rejected the physical battery model",
            details={"status": str(passed)},
        )
    _assert_continuous_lp(highs, highspy)
    matrix_build_s = time.perf_counter() - build_started
    return HighsPhysicalLP(
        highs=highs,
        highspy=highspy,
        config=config,
        frame=work,
        dt=dt,
        import0=import0,
        export0=export0,
        month_labels=month_labels,
        year_fraction=year_fraction,
        allowed_stored_throughput_kwh=allowed_throughput,
        n=n,
        idx_charge=idx_charge,
        idx_discharge=idx_discharge,
        idx_soc=idx_soc,
        idx_peak_annual=idx_peak_annual,
        idx_peak_month=idx_peak_month,
        num_col=num_col,
        num_row_physical=num_row,
        num_nz=num_nz,
        enforce_cycle_limit=enforce_cycle_limit,
        matrix_build_s=matrix_build_s,
        options=options,
        log_file_path=log_file_path,
    )


def optimize_highs_stage(lp: Any, *, stage: str) -> dict[str, Any]:
    highspy = lp.highspy
    started = time.perf_counter()
    run_status = lp.highs.run()
    flush_highs_log(lp)
    runtime_s = time.perf_counter() - started
    model_status = lp.highs.getModelStatus()
    status_name = highspy.HighsModelStatus(model_status).name
    status = STATUS_NAMES.get(status_name, status_name)
    info = lp.highs.getInfo()
    lp.last_info = _info_payload(lp.highs, info)
    lp.col_value = np.asarray(lp.highs.getSolution().col_value, dtype=float)
    if model_status != highspy.HighsModelStatus.kOptimal:
        raise OptimizerError(
            f"HiGHS did not return an optimal solution at stage {stage!r}: {status}",
            status=status,
            stage=stage,
            details={
                "status_name": status_name,
                "run_status": str(run_status),
                "runtime_s": runtime_s,
                **lp.last_info,
            },
        )
    _assert_continuous_lp(lp.highs, highspy)
    return {
        "stage": stage,
        "status": status,
        "status_name": status_name,
        "objective_value": float(info.objective_function_value),
        "runtime_s": runtime_s,
        "highs_run_time_s": float(lp.highs.getRunTime()),
        "iter_count": _iteration_count(info),
        "algorithm": _algorithm_used(info),
        "simplex_iteration_count": int(info.simplex_iteration_count),
        "ipm_iteration_count": int(info.ipm_iteration_count),
        "crossover_iteration_count": int(info.crossover_iteration_count),
        "pdlp_iteration_count": int(info.pdlp_iteration_count),
    }


def set_highs_objective(lp: Any, columns: np.ndarray, costs: np.ndarray, *, maximize: bool) -> None:
    highspy = lp.highspy
    zeros = np.zeros(lp.num_col, dtype=np.float64)
    all_idx = np.arange(lp.num_col, dtype=np.int32)
    lp.highs.changeColsCost(lp.num_col, all_idx, zeros)
    idx = np.asarray(columns, dtype=np.int32)
    costs64 = np.asarray(costs, dtype=np.float64)
    lp.highs.changeColsCost(int(idx.size), idx, costs64)
    sense = highspy.ObjSense.kMaximize if maximize else highspy.ObjSense.kMinimize
    lp.highs.changeObjectiveSense(sense)


def add_highs_keep_row(
    lp: Any,
    *,
    columns: np.ndarray,
    values: np.ndarray,
    lower: float,
    upper: float,
) -> None:
    idx = np.asarray(columns, dtype=np.int32)
    coef = np.asarray(values, dtype=np.float64)
    lp.highs.addRow(float(lower), float(upper), int(idx.size), idx, coef)


def _release_highs_log_file(lp: Any) -> None:
    path = getattr(lp, "log_file_path", None)
    lp.log_file_path = None
    lp.log_file_offset = 0
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def dispose_highs_lp(lp: Any) -> None:
    path = getattr(lp, "log_file_path", None)
    highs = getattr(lp, "highs", None)
    try:
        if highs is not None:
            try:
                highs.setOptionValue("log_file", "")
            except Exception:
                pass
            highs.clear()
    finally:
        lp.highs = None
        lp.col_value = None
        lp.log_file_path = None
        lp.log_file_offset = 0
        if highs is not None:
            del highs
        gc.collect()
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def solve_highs_stages_respecting_cycle_limit(
    frame: pd.DataFrame,
    config: BatteryConfig,
    solve_stages,
    *,
    output_flag: int = 0,
) -> tuple[HighsPhysicalLP, list[dict[str, Any]], bool]:
    from btm_sim.battery.physics import stored_throughput_kwh

    lp = build_highs_physical_lp(frame, config, output_flag=output_flag, enforce_cycle_limit=False)
    try:
        stages = solve_stages(lp)
        actual = stored_throughput_kwh(lp.charge_values, lp.discharge_values, config)
        if actual <= lp.allowed_stored_throughput_kwh + DOCUMENTED_TOLERANCE_KWH:
            return lp, stages, False
    except Exception:
        dispose_highs_lp(lp)
        raise
    dispose_highs_lp(lp)
    lp = build_highs_physical_lp(frame, config, output_flag=output_flag, enforce_cycle_limit=True)
    try:
        stages = solve_stages(lp)
    except Exception:
        dispose_highs_lp(lp)
        raise
    return lp, stages, True


def highs_package_version(highspy: Any, highs: Any) -> tuple[str, str]:
    version = str(highs.version())
    try:
        from importlib.metadata import version as pkg_version_of

        pkg_version = pkg_version_of("highspy")
    except Exception:
        pkg_version = getattr(highspy, "__version__", version)
    return str(pkg_version), version


def highs_solver_metadata(
    lp: Any,
    stages: list[dict[str, Any]],
    *,
    feasibility_ok: bool,
    cycle_cut_applied: bool | None,
    end_to_end_s: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pkg_version, version = highs_package_version(lp.highspy, lp.highs)
    payload = {
        "name": "HiGHS",
        "highspy_version": pkg_version,
        "highs_version": version,
        "status": "OPTIMAL" if feasibility_ok else "POSTCHECK_FAILED",
        "runtime_s": float(sum(stage["runtime_s"] for stage in stages)),
        "matrix_build_s": float(getattr(lp, "matrix_build_s", 0.0)),
        "end_to_end_s": float(end_to_end_s),
        "num_vars": int(lp.highs.getNumCol()),
        "num_constrs": int(lp.highs.getNumRow()),
        "num_nz": int(lp.highs.getNumNz()),
        "num_physical_constrs": int(getattr(lp, "num_row_physical", lp.highs.getNumRow())),
        "num_physical_nz": int(getattr(lp, "num_nz", lp.highs.getNumNz())),
        "num_int_vars": 0,
        "num_bin_vars": 0,
        "continuous_lp": True,
        "cycle_cut_applied": cycle_cut_applied,
        "options": dict(getattr(lp, "options", {})),
        "algorithm": stages[-1]["algorithm"] if stages else None,
        "production_backend": True,
    }
    if extra:
        payload.update(extra)
    return payload


def _assert_continuous_lp(highs: Any, highspy: Any) -> None:
    lp = highs.getLp()
    integrality = list(getattr(lp, "integrality_", []))
    if not integrality:
        return
    n_int = 0
    n_bin = 0
    for item in integrality:
        name = highspy.HighsVarType(item).name
        if name == "kContinuous":
            continue
        if name == "kBinary":
            n_bin += 1
        else:
            n_int += 1
    if n_int or n_bin:
        raise OptimizerError(
            "Physical battery model must remain a continuous LP (no integer/binary variables)",
            details={"NumIntVars": n_int, "NumBinVars": n_bin},
        )


def _info_payload(highs: Any, info: Any) -> dict[str, Any]:
    return {
        "objective_function_value": float(info.objective_function_value),
        "simplex_iteration_count": int(info.simplex_iteration_count),
        "ipm_iteration_count": int(info.ipm_iteration_count),
        "crossover_iteration_count": int(info.crossover_iteration_count),
        "pdlp_iteration_count": int(info.pdlp_iteration_count),
        "max_primal_infeasibility": float(info.max_primal_infeasibility),
        "max_dual_infeasibility": float(info.max_dual_infeasibility),
        "primal_solution_status": str(highs.solutionStatusToString(info.primal_solution_status)),
        "dual_solution_status": str(highs.solutionStatusToString(info.dual_solution_status)),
    }


def _iteration_count(info: Any) -> float:
    return float(
        int(info.simplex_iteration_count)
        + int(info.ipm_iteration_count)
        + int(info.crossover_iteration_count)
        + int(info.pdlp_iteration_count)
    )


def _algorithm_used(info: Any) -> str:
    parts: list[str] = []
    if int(info.ipm_iteration_count):
        parts.append("ipm")
    if int(info.crossover_iteration_count):
        parts.append("crossover")
    if int(info.simplex_iteration_count):
        parts.append("simplex")
    if int(info.pdlp_iteration_count):
        parts.append("pdlp")
    return "+".join(parts) if parts else "unknown"
