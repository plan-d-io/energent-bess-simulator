"""Run the diagnostic reference controller and write audit artefacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.controller import attach_reference_dispatch
from btm_sim.battery.cycles import cycle_limit_report
from btm_sim.battery.physics import (
    DISPATCH_COLUMNS,
    check_dispatch_feasibility,
    equivalent_full_cycles,
    interval_energy_balance_residual,
    stored_throughput_kwh,
)
from btm_sim.fluvius.constants import CANONICAL_COLUMNS, DOCUMENTED_TOLERANCE_KWH, INTERVAL_HOURS, TZ_NAME
from btm_sim.fluvius.csv_io import sha256_file


@dataclass
class ReferenceRun:
    frame: pd.DataFrame
    summary: dict[str, Any]
    config: BatteryConfig
    feasibility_ok: bool


def run_reference_controller(frame: pd.DataFrame, config: BatteryConfig) -> ReferenceRun:
    """Apply the simple reference controller and build a labelled summary."""
    missing = [column for column in CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"normalized input is missing columns: {missing}")

    work = frame.sort_values("timestamp_utc").reset_index(drop=True)
    dispatched = attach_reference_dispatch(work, config)
    feasibility = check_dispatch_feasibility(dispatched, config)
    summary = build_reference_summary(dispatched, config, feasibility.to_dict())
    return ReferenceRun(
        frame=dispatched,
        summary=summary,
        config=config,
        feasibility_ok=feasibility.ok,
    )


def build_reference_summary(
    frame: pd.DataFrame,
    config: BatteryConfig,
    feasibility: dict[str, Any],
    *,
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
    charge_loss = float(frame["charge_loss_kwh"].sum())
    discharge_loss = float(frame["discharge_loss_kwh"].sum())
    total_loss = charge_loss + discharge_loss
    pv_direct = float((frame["pv_production_kwh"] - frame["grid_export_baseline_kwh"]).sum())
    useful_pv = pv_direct + discharge
    gross_retained = float((frame["pv_production_kwh"] - frame["grid_export_kwh"]).sum())
    soc_initial = float(frame["soc_start_kwh"].iloc[0]) if len(frame) else config.soc_initial_kwh
    soc_final = float(frame["soc_end_kwh"].iloc[-1]) if len(frame) else config.soc_initial_kwh
    throughput = stored_throughput_kwh(frame["charge_pv_kwh"], frame["discharge_load_kwh"], config)
    residual = interval_energy_balance_residual(frame)
    loss_identity = charge - discharge - total_loss - (soc_final - soc_initial)
    dt = frame["interval_hours"] if "interval_hours" in frame.columns else INTERVAL_HOURS
    baseline_peak = float((frame["grid_import_baseline_kwh"] / dt).max()) if len(frame) else 0.0
    annual_peak = float((frame["grid_import_kwh"] / dt).max()) if len(frame) else 0.0

    return {
        "ok": bool(feasibility.get("ok", False)),
        "label": "diagnostic_reference",
        "diagnostic_reference": True,
        "not_upper_bound": True,
        "result_description": (
            "Simple reference controller. It looks only at the current quarter-hour "
            "and is a check on battery physics, not a best-case result."
        ),
        "interpretation": (
            "Simple reference controller. It looks only at the current quarter-hour. "
            "It did not try to reduce peaks. This is a check on battery physics, "
            "not a best-case optimized result."
        ),
        "software_version": _software_version(),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dt_hours": INTERVAL_HOURS,
        "n_intervals": int(len(frame)),
        "source_sha256": source_hash,
        "battery": config.to_dict(),
        "soc_initial_kwh": soc_initial,
        "soc_final_kwh": soc_final,
        "energy_kwh": {
            "pv_production": pv,
            "site_load": load,
            "grid_import_baseline": import0,
            "grid_export_baseline": export0,
            "grid_import": grid_import,
            "grid_export": grid_export,
            "charge_pv": charge,
            "discharge_load": discharge,
            "charge_loss": charge_loss,
            "discharge_loss": discharge_loss,
            "total_loss": total_loss,
            "pv_direct": pv_direct,
            "useful_pv_delivered": useful_pv,
            "useful_additional_pv": discharge,
            "gross_pv_retained_onsite": gross_retained,
        },
        "peaks_kw": {
            "baseline_annual_max": baseline_peak,
            "annual_max": annual_peak,
            "monthly_max": _monthly_import_peaks_kw(frame),
        },
        "throughput": {
            "stored_throughput_kwh": throughput,
            "equivalent_full_cycles": equivalent_full_cycles(throughput, config.e_usable_kwh),
            **cycle_limit_report(frame, config),
        },
        "reconciliation": {
            "max_abs_interval_balance_kwh": float(abs(residual).max()) if len(residual) else 0.0,
            "loss_identity_residual_kwh": float(loss_identity),
            "tolerance_kwh": DOCUMENTED_TOLERANCE_KWH,
        },
        "feasibility": feasibility,
    }


def write_reference_outputs(result: ReferenceRun, output_dir: str | Path, *, source_path: Path | None = None) -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    dispatch_path = directory / "reference_dispatch.csv"
    summary_path = directory / "reference_summary.json"
    source_hash = sha256_file(source_path) if source_path is not None and source_path.exists() else None
    if source_hash is not None:
        result.summary["source_sha256"] = source_hash
    result.summary["outputs"] = {
        "reference_dispatch": str(dispatch_path),
        "reference_summary": str(summary_path),
    }
    export = result.frame.copy()
    dispatch_path.write_text(_dispatch_csv(export), encoding="utf-8")
    summary_path.write_text(json.dumps(result.summary, indent=2) + "\n", encoding="utf-8")
    return {"reference_dispatch": dispatch_path, "reference_summary": summary_path}


def _dispatch_csv(frame: pd.DataFrame) -> str:
    ordered = [column for column in (*CANONICAL_COLUMNS, *DISPATCH_COLUMNS) if column in frame.columns]
    extra = [column for column in frame.columns if column not in ordered]
    return frame.loc[:, ordered + extra].to_csv(index=False)


def _monthly_import_peaks_kw(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {}
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    keys = [f"{year:04d}-{month:02d}" for year, month in zip(local.dt.year, local.dt.month, strict=True)]
    power = frame["grid_import_kwh"].to_numpy(dtype=float) / frame["interval_hours"].to_numpy(dtype=float)
    grouped = pd.Series(power, index=keys).groupby(level=0).max()
    return {str(index): float(value) for index, value in grouped.items()}


def _software_version() -> str:
    from btm_sim import __version__

    return __version__
