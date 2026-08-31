"""Assemble dispatch frames and optimization audit summaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import cycle_limit_report
from btm_sim.battery.physics import (
    check_dispatch_feasibility,
    equivalent_full_cycles,
    interval_energy_balance_residual,
    stored_throughput_kwh,
)
from btm_sim.fluvius.constants import CANONICAL_COLUMNS, DOCUMENTED_TOLERANCE_KWH, INTERVAL_HOURS, TZ_NAME
from btm_sim.fluvius.csv_io import sha256_file
from btm_sim.optimizer.constants import LEXICO_TOL_EUR, LEXICO_TOL_KWH, LEXICO_TOL_KW

LP_DISPATCH_COLUMNS = (
    "charge_pv_kwh",
    "discharge_load_kwh",
    "discharge_grid_kwh",
    "soc_start_kwh",
    "soc_end_kwh",
    "charge_loss_kwh",
    "discharge_loss_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
    "grid_import_kw",
)


def dispatch_from_solution(
    frame: pd.DataFrame,
    *,
    config: BatteryConfig,
    charge: np.ndarray,
    discharge: np.ndarray,
    soc: np.ndarray,
    dt: np.ndarray,
    import0: np.ndarray,
    export0: np.ndarray,
    discharge_grid: np.ndarray | None = None,
) -> pd.DataFrame:
    grid = np.zeros(len(charge), dtype=float) if discharge_grid is None else np.asarray(discharge_grid, dtype=float)
    total_discharge = np.asarray(discharge, dtype=float) + grid
    out = frame.copy()
    out["charge_pv_kwh"] = charge
    out["discharge_load_kwh"] = discharge
    out["discharge_grid_kwh"] = grid
    out["soc_start_kwh"] = soc[:-1]
    out["soc_end_kwh"] = soc[1:]
    out["charge_loss_kwh"] = charge * (1.0 - config.eta_charge)
    out["discharge_loss_kwh"] = total_discharge / config.eta_discharge - total_discharge
    out["grid_import_kwh"] = import0 - discharge
    out["grid_export_kwh"] = export0 - charge + grid
    out["grid_import_kw"] = out["grid_import_kwh"].to_numpy(dtype=float) / dt
    return out


def monthly_import_peaks_kw(frame: pd.DataFrame) -> dict[str, float]:
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    keys = [f"{year:04d}-{month:02d}" for year, month in zip(local.dt.year, local.dt.month, strict=True)]
    grouped = frame.assign(_month=keys).groupby("_month", sort=True)["grid_import_kw"].max()
    return {str(index): float(value) for index, value in grouped.items()}


def dispatch_from_lp(lp: Any) -> pd.DataFrame:
    charge = np.asarray(lp.charge.X, dtype=float)
    discharge = np.asarray(lp.discharge.X, dtype=float)
    soc = np.asarray(lp.soc.X, dtype=float)
    discharge_grid = (
        np.asarray(lp.discharge_grid.X, dtype=float)
        if getattr(lp, "discharge_grid", None) is not None
        else None
    )
    return dispatch_from_solution(
        lp.frame,
        config=lp.config,
        charge=charge,
        discharge=discharge,
        soc=soc,
        dt=lp.dt,
        import0=lp.import0,
        export0=lp.export0,
        discharge_grid=discharge_grid,
    )


def solver_metadata(lp: Any, stages: list[dict[str, Any]], *, feasibility_ok: bool) -> dict[str, Any]:
    return {
        "name": "Gurobi",
        "gurobipy_version": ".".join(str(part) for part in lp.gp.gurobi.version()),
        "status": "OPTIMAL" if feasibility_ok else "POSTCHECK_FAILED",
        "runtime_s": float(sum(stage["runtime_s"] for stage in stages)),
        "num_vars": int(lp.model.NumVars),
        "num_constrs": int(lp.model.NumConstrs),
        "num_int_vars": int(lp.model.NumIntVars),
        "num_bin_vars": int(lp.model.NumBinVars),
        "continuous_lp": lp.model.NumIntVars == 0 and lp.model.NumBinVars == 0,
        "production_backend": False,
    }


def build_self_consumption_summary(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    stages: list[dict[str, Any]],
    solver: dict[str, Any],
    feasibility: dict[str, Any],
    source_hash: str | None = None,
) -> dict[str, Any]:
    return build_optimization_summary(
        frame,
        config,
        case="self_consumption_first",
        result_description=(
            "Best-case self-consumption result using the complete year in advance. "
            "This is not a forecast or expected operational saving."
        ),
        interpretation=(
            "Best-case self-consumption result using the complete year in advance. "
            "Objectives were applied in priority order. This is not a forecast or "
            "expected operational saving."
        ),
        stages=stages,
        solver=solver,
        feasibility=feasibility,
        source_hash=source_hash,
    )


def build_peak_reduction_summary(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    stages: list[dict[str, Any]],
    solver: dict[str, Any],
    feasibility: dict[str, Any],
    source_hash: str | None = None,
) -> dict[str, Any]:
    return build_optimization_summary(
        frame,
        config,
        case="peak_reduction_first",
        result_description=(
            "Best-case peak-reduction result using the complete year in advance. "
            "This is not a forecast or expected operational saving."
        ),
        interpretation=(
            "Best-case peak-reduction result using the complete year in advance. "
            "Objectives were applied in priority order. Charging is from PV that "
            "would otherwise be exported. This is not a forecast or expected "
            "operational saving."
        ),
        stages=stages,
        solver=solver,
        feasibility=feasibility,
        source_hash=source_hash,
    )


def build_optimization_summary(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    case: str,
    result_description: str,
    interpretation: str,
    stages: list[dict[str, Any]],
    solver: dict[str, Any],
    feasibility: dict[str, Any],
    source_hash: str | None = None,
) -> dict[str, Any]:
    pv = float(frame["pv_production_kwh"].sum())
    load = float(frame["site_load_kwh"].sum())
    import0 = float(frame["grid_import_baseline_kwh"].sum())
    export0 = float(frame["grid_export_baseline_kwh"].sum())
    grid_import = float(frame["grid_import_kwh"].sum())
    grid_export = float(frame["grid_export_kwh"].sum())
    charge = float(frame["charge_pv_kwh"].sum())
    discharge = float(frame["discharge_load_kwh"].sum())
    discharge_grid = (
        float(frame["discharge_grid_kwh"].sum()) if "discharge_grid_kwh" in frame.columns else 0.0
    )
    total_ac_discharge = discharge + discharge_grid
    charge_loss = float(frame["charge_loss_kwh"].sum())
    discharge_loss = float(frame["discharge_loss_kwh"].sum())
    total_loss = charge_loss + discharge_loss
    pv_direct = float((frame["pv_production_kwh"] - frame["grid_export_baseline_kwh"]).sum())
    soc_initial = float(frame["soc_start_kwh"].iloc[0])
    soc_final = float(frame["soc_end_kwh"].iloc[-1])
    if "discharge_grid_kwh" in frame.columns:
        discharge_for_throughput = frame["discharge_load_kwh"].to_numpy(dtype=float) + frame[
            "discharge_grid_kwh"
        ].to_numpy(dtype=float)
    else:
        discharge_for_throughput = frame["discharge_load_kwh"]
    throughput = stored_throughput_kwh(frame["charge_pv_kwh"], discharge_for_throughput, config)
    residual = interval_energy_balance_residual(frame)
    monthly = monthly_import_peaks_kw(frame)
    baseline_for_months = frame.copy()
    baseline_for_months["grid_import_kw"] = (
        frame["grid_import_baseline_kwh"].to_numpy(dtype=float)
        / frame["interval_hours"].to_numpy(dtype=float)
    )
    baseline_monthly = monthly_import_peaks_kw(baseline_for_months)
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is not None:
        local = local.dt.tz_convert(TZ_NAME)
    feasibility_ok = bool(feasibility.get("ok", False))
    return {
        "ok": feasibility_ok,
        "label": "perfect_foresight_upper_bound",
        "perfect_foresight_upper_bound": True,
        "diagnostic_reference": False,
        "not_upper_bound": False,
        "case": case,
        "result_description": result_description,
        "interpretation": interpretation,
        "battery_limits_and_balances": "passed" if feasibility_ok else "failed",
        "software_version": _software_version(),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dt_hours": INTERVAL_HOURS,
        "n_intervals": int(len(frame)),
        "selected_period": {
            "start_utc": _iso(frame["timestamp_utc"].iloc[0]),
            "end_utc_exclusive": _iso(
                pd.Timestamp(frame["timestamp_utc"].iloc[-1]) + pd.Timedelta(hours=float(frame["interval_hours"].iloc[-1]))
            ),
            "start_local": _iso(local.iloc[0]),
            "end_local": _iso(local.iloc[-1]),
        },
        "source_sha256": source_hash,
        "battery": config.to_dict(),
        "soc_initial_kwh": soc_initial,
        "soc_final_kwh": soc_final,
        "solver": solver,
        "lexicographic_tolerance": {
            "energy_kwh": LEXICO_TOL_KWH,
            "power_kw": LEXICO_TOL_KW,
            "revenue_eur": LEXICO_TOL_EUR,
            "note": (
                "Allowed numerical change in an earlier result when applying the "
                "next objective. Value is solver-scale slack (1e-9 kWh; that "
                "value / 0.25 h in kW; 1e-9 EUR), not the 0.001 kWh Fluvius "
                "measurement tolerance."
            ),
        },
        "objective_stages": stages,
        "objective_steps": [
            {
                "priority": index,
                "label": stage.get("user_label", stage["stage"]),
                "result": stage["optimum"],
                "unit": stage["unit"],
                "solver_status": stage["status"],
                "gurobi_status": stage["status"] if solver.get("name") == "Gurobi" else None,
                "runtime_s": stage["runtime_s"],
            }
            for index, stage in enumerate(stages, start=1)
        ],
        "energy_kwh": {
            "pv_production": pv,
            "site_load": load,
            "grid_import_baseline": import0,
            "grid_export_baseline": export0,
            "grid_import": grid_import,
            "grid_export": grid_export,
            "charge_pv": charge,
            "discharge_load": discharge,
            "discharge_grid": discharge_grid,
            "charge_loss": charge_loss,
            "discharge_loss": discharge_loss,
            "total_loss": total_loss,
            "pv_direct": pv_direct,
            "useful_pv_delivered": pv_direct + discharge,
            "useful_additional_pv": discharge,
            "gross_pv_retained_onsite": float((frame["pv_production_kwh"] - frame["grid_export_kwh"]).sum()),
        },
        "peaks_kw": {
            "baseline_annual_max": float((frame["grid_import_baseline_kwh"] / frame["interval_hours"]).max()),
            "annual_max": float(frame["grid_import_kw"].max()),
            "baseline_monthly_max": baseline_monthly,
            "monthly_max": monthly,
            "baseline_sum_monthly_max": float(sum(baseline_monthly.values())),
            "sum_monthly_max": float(sum(monthly.values())),
        },
        "throughput": {
            "stored_throughput_kwh": throughput,
            "equivalent_full_cycles": equivalent_full_cycles(throughput, config.e_usable_kwh),
            **cycle_limit_report(frame, config),
        },
        "reconciliation": {
            "max_abs_interval_balance_kwh": float(np.max(np.abs(residual))) if len(residual) else 0.0,
            "loss_identity_residual_kwh": float(
                charge - total_ac_discharge - total_loss - (soc_final - soc_initial)
            ),
            "terminal_soc_gap_kwh": float(soc_final - soc_initial),
            "tolerance_kwh": DOCUMENTED_TOLERANCE_KWH,
        },
        "feasibility": feasibility,
    }


def postcheck_dispatch(frame: pd.DataFrame, config: BatteryConfig) -> dict[str, Any]:
    feasibility = check_dispatch_feasibility(frame, config)
    payload = feasibility.to_dict()
    soc_gap = abs(float(frame["soc_end_kwh"].iloc[-1] - frame["soc_start_kwh"].iloc[0]))
    if soc_gap > DOCUMENTED_TOLERANCE_KWH:
        payload["ok"] = False
        payload["violations"] = list(payload["violations"]) + [
            {"code": "TERMINAL_SOC", "gap_kwh": soc_gap}
        ]
    residual = interval_energy_balance_residual(frame)
    max_residual = float(np.max(np.abs(residual))) if len(residual) else 0.0
    if max_residual > DOCUMENTED_TOLERANCE_KWH:
        payload["ok"] = False
        payload["violations"] = list(payload["violations"]) + [
            {"code": "ENERGY_BALANCE", "max_abs_kwh": max_residual}
        ]
    return payload


def build_revenue_summary(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    tariffs,
    stages: list[dict[str, Any]],
    solver: dict[str, Any],
    feasibility: dict[str, Any],
    settlement: dict[str, Any],
    source_hash: str | None = None,
) -> dict[str, Any]:
    summary = build_optimization_summary(
        frame,
        config,
        case="revenue_first",
        result_description=(
            "Best-case Energent PV revenue result using the complete year in "
            "advance. The battery first preserves customer PV supply, then may "
            "inject remaining stored PV at the configured fixed tariff. Totals "
            "exclude battery CAPEX, OPEX, financing, taxes, and customer import "
            "costs. This is not a forecast, profit, or NPV."
        ),
        interpretation=(
            "Best-case Energent PV revenue result using the complete year in "
            "advance. The battery first preserves the best achievable PV supply "
            "to the customer. Remaining flexibility may inject stored PV at the "
            "configured fixed injection tariff. The battery never charges from "
            "the grid. This is not a forecast, profit, or NPV."
        ),
        stages=stages,
        solver=solver,
        feasibility=feasibility,
        source_hash=source_hash,
    )
    summary["tariffs"] = tariffs.to_dict()
    summary["revenue"] = settlement
    return summary


def write_revenue_outputs(
    frame: pd.DataFrame,
    summary: dict[str, Any],
    output_dir: str | Path,
    *,
    source_path: Path | None = None,
) -> dict[str, Path]:
    return write_optimization_outputs(
        frame,
        summary,
        output_dir,
        dispatch_name="revenue_dispatch.csv",
        summary_name="revenue_summary.json",
        source_path=source_path,
    )


def write_self_consumption_outputs(
    frame: pd.DataFrame,
    summary: dict[str, Any],
    output_dir: str | Path,
    *,
    source_path: Path | None = None,
) -> dict[str, Path]:
    return write_optimization_outputs(
        frame,
        summary,
        output_dir,
        dispatch_name="self_consumption_dispatch.csv",
        summary_name="self_consumption_summary.json",
        source_path=source_path,
    )


def write_peak_reduction_outputs(
    frame: pd.DataFrame,
    summary: dict[str, Any],
    output_dir: str | Path,
    *,
    source_path: Path | None = None,
) -> dict[str, Path]:
    return write_optimization_outputs(
        frame,
        summary,
        output_dir,
        dispatch_name="peak_reduction_dispatch.csv",
        summary_name="peak_reduction_summary.json",
        source_path=source_path,
    )


def write_optimization_outputs(
    frame: pd.DataFrame,
    summary: dict[str, Any],
    output_dir: str | Path,
    *,
    dispatch_name: str,
    summary_name: str,
    source_path: Path | None = None,
) -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    dispatch_path = directory / dispatch_name
    summary_path = directory / summary_name
    if source_path is not None and source_path.exists():
        summary["source_sha256"] = sha256_file(source_path)
    summary["outputs"] = {
        dispatch_name.replace(".csv", ""): str(dispatch_path),
        summary_name.replace(".json", ""): str(summary_path),
    }
    ordered = [column for column in (*CANONICAL_COLUMNS, *LP_DISPATCH_COLUMNS) if column in frame.columns]
    extra = [column for column in frame.columns if column not in ordered]
    dispatch_path.write_text(frame.loc[:, ordered + extra].to_csv(index=False), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {
        dispatch_name.replace(".csv", ""): dispatch_path,
        summary_name.replace(".json", ""): summary_path,
    }


def _iso(value: Any) -> str:
    ts = pd.Timestamp(value)
    return ts.isoformat()


def _software_version() -> str:
    from btm_sim import __version__

    return __version__
