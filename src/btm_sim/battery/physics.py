"""Pure battery state transition, losses, and dispatch-feasibility checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, FLOAT_EPS_KWH, INTERVAL_HOURS

DISPATCH_COLUMNS = (
    "net_export_available_kwh",
    "net_import_need_kwh",
    "charge_pv_kwh",
    "discharge_load_kwh",
    "soc_start_kwh",
    "soc_end_kwh",
    "charge_loss_kwh",
    "discharge_loss_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
)


@dataclass(frozen=True)
class StepResult:
    soc_end_kwh: float
    charge_loss_kwh: float
    discharge_loss_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float


@dataclass
class FeasibilityResult:
    ok: bool
    violations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": self.violations}


def charge_loss_kwh(charge_pv_kwh: float, eta_charge: float) -> float:
    return charge_pv_kwh * (1.0 - eta_charge)


def discharge_loss_kwh(discharge_load_kwh: float, eta_discharge: float) -> float:
    return discharge_load_kwh / eta_discharge - discharge_load_kwh


def next_soc_kwh(
    soc_kwh: float,
    charge_pv_kwh: float,
    discharge_load_kwh: float,
    config: BatteryConfig,
) -> float:
    """Stored-energy transition from MODEL_SPEC.md."""
    return (
        soc_kwh
        + config.eta_charge * charge_pv_kwh
        - discharge_load_kwh / config.eta_discharge
    )


def apply_step(
    soc_kwh: float,
    charge_pv_kwh: float,
    discharge_load_kwh: float,
    *,
    config: BatteryConfig,
    grid_import_baseline_kwh: float,
    grid_export_baseline_kwh: float,
) -> StepResult:
    """Pure AC-side step. Callers must pass a feasible action."""
    return StepResult(
        soc_end_kwh=next_soc_kwh(soc_kwh, charge_pv_kwh, discharge_load_kwh, config),
        charge_loss_kwh=charge_loss_kwh(charge_pv_kwh, config.eta_charge),
        discharge_loss_kwh=discharge_loss_kwh(discharge_load_kwh, config.eta_discharge),
        grid_import_kwh=grid_import_baseline_kwh - discharge_load_kwh,
        grid_export_kwh=grid_export_baseline_kwh - charge_pv_kwh,
    )


def net_availability(
    grid_import_baseline_kwh: pd.Series | np.ndarray,
    grid_export_baseline_kwh: pd.Series | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Conservative net export/import used only by the diagnostic controller."""
    import0 = np.asarray(grid_import_baseline_kwh, dtype=float)
    export0 = np.asarray(grid_export_baseline_kwh, dtype=float)
    net_export = np.maximum(export0 - import0, 0.0)
    net_import = np.maximum(import0 - export0, 0.0)
    return net_export, net_import


def stored_throughput_kwh(
    charge_pv_kwh: np.ndarray | pd.Series,
    discharge_load_kwh: np.ndarray | pd.Series,
    config: BatteryConfig,
) -> float:
    charge = np.asarray(charge_pv_kwh, dtype=float)
    discharge = np.asarray(discharge_load_kwh, dtype=float)
    return float(np.sum(config.eta_charge * charge + discharge / config.eta_discharge))


def equivalent_full_cycles(throughput_kwh: float, e_usable_kwh: float) -> float:
    if e_usable_kwh <= FLOAT_EPS_KWH:
        return 0.0
    return throughput_kwh / (2.0 * e_usable_kwh)


def check_dispatch_feasibility(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    tolerance_kwh: float = DOCUMENTED_TOLERANCE_KWH,
) -> FeasibilityResult:
    """Check MODEL_SPEC physical constraints, including inverter time-sharing."""
    violations: list[dict[str, Any]] = []
    dt = _interval_hours(frame)
    charge = frame["charge_pv_kwh"].to_numpy(dtype=float)
    discharge = frame["discharge_load_kwh"].to_numpy(dtype=float)
    discharge_grid = (
        frame["discharge_grid_kwh"].to_numpy(dtype=float)
        if "discharge_grid_kwh" in frame.columns
        else np.zeros(len(frame), dtype=float)
    )
    total_discharge = discharge + discharge_grid
    import0 = frame["grid_import_baseline_kwh"].to_numpy(dtype=float)
    export0 = frame["grid_export_baseline_kwh"].to_numpy(dtype=float)
    soc_start = frame["soc_start_kwh"].to_numpy(dtype=float)
    soc_end = frame["soc_end_kwh"].to_numpy(dtype=float)

    def add(code: str, mask: np.ndarray, **extra: Any) -> None:
        count = int(np.count_nonzero(mask))
        if count:
            payload: dict[str, Any] = {"code": code, "count": count}
            payload.update(extra)
            violations.append(payload)

    add("NEGATIVE_CHARGE", charge < -tolerance_kwh)
    add("NEGATIVE_DISCHARGE", discharge < -tolerance_kwh)
    add("NEGATIVE_DISCHARGE_GRID", discharge_grid < -tolerance_kwh)
    add("CHARGE_EXCEEDS_EXPORT", charge - export0 > tolerance_kwh)
    add("DISCHARGE_EXCEEDS_IMPORT", discharge - import0 > tolerance_kwh)
    add(
        "CHARGE_EXCEEDS_POWER",
        charge - config.p_charge_kw * dt > tolerance_kwh,
    )
    add(
        "DISCHARGE_EXCEEDS_POWER",
        total_discharge - config.p_discharge_kw * dt > tolerance_kwh,
    )
    if config.p_charge_kw <= FLOAT_EPS_KWH:
        add("ZERO_CHARGE_POWER", np.abs(charge) > tolerance_kwh)
    if config.p_discharge_kw <= FLOAT_EPS_KWH:
        add("ZERO_DISCHARGE_POWER", np.abs(total_discharge) > tolerance_kwh)

    if config.p_charge_kw > FLOAT_EPS_KWH and config.p_discharge_kw > FLOAT_EPS_KWH:
        inverter_time = charge / config.p_charge_kw + total_discharge / config.p_discharge_kw
        add(
            "INVERTER_TIME",
            inverter_time - dt > tolerance_kwh / max(config.p_charge_kw, config.p_discharge_kw),
            max_excess_h=float(np.max(inverter_time - dt)) if len(inverter_time) else 0.0,
        )

    add("SOC_START_BELOW_ZERO", soc_start < -tolerance_kwh)
    add("SOC_START_ABOVE_CAPACITY", soc_start - config.e_usable_kwh > tolerance_kwh)
    add("SOC_END_BELOW_ZERO", soc_end < -tolerance_kwh)
    add("SOC_END_ABOVE_CAPACITY", soc_end - config.e_usable_kwh > tolerance_kwh)

    expected_end = (
        soc_start + config.eta_charge * charge - total_discharge / config.eta_discharge
    )
    add("SOC_TRANSITION", np.abs(soc_end - expected_end) > tolerance_kwh)

    grid_import = import0 - discharge
    grid_export = export0 - charge + discharge_grid
    add("GRID_CHARGING", grid_import - import0 > tolerance_kwh)
    add("NEGATIVE_GRID_IMPORT", grid_import < -tolerance_kwh)
    add("NEGATIVE_GRID_EXPORT", grid_export < -tolerance_kwh)

    if "grid_import_kwh" in frame.columns:
        add(
            "GRID_IMPORT_MISMATCH",
            np.abs(frame["grid_import_kwh"].to_numpy(dtype=float) - grid_import) > tolerance_kwh,
        )
    if "grid_export_kwh" in frame.columns:
        add(
            "GRID_EXPORT_MISMATCH",
            np.abs(frame["grid_export_kwh"].to_numpy(dtype=float) - grid_export) > tolerance_kwh,
        )

    from btm_sim.battery.cycles import allowed_stored_throughput_kwh, selected_period_year_fraction

    actual_throughput = stored_throughput_kwh(charge, total_discharge, config)
    allowed_throughput = allowed_stored_throughput_kwh(config, selected_period_year_fraction(frame))
    if actual_throughput - allowed_throughput > tolerance_kwh:
        violations.append(
            {
                "code": "CYCLE_LIMIT_EXCEEDED",
                "count": 1,
                "actual_stored_throughput_kwh": actual_throughput,
                "allowed_stored_throughput_kwh": allowed_throughput,
            }
        )

    return FeasibilityResult(ok=not violations, violations=violations)


def interval_energy_balance_residual(frame: pd.DataFrame) -> np.ndarray:
    """AC-bus residual: load + export + charge - (pv + import + customer discharge + grid discharge)."""
    residual = (
        frame["site_load_kwh"].to_numpy(dtype=float)
        + frame["grid_export_kwh"].to_numpy(dtype=float)
        + frame["charge_pv_kwh"].to_numpy(dtype=float)
        - frame["pv_production_kwh"].to_numpy(dtype=float)
        - frame["grid_import_kwh"].to_numpy(dtype=float)
        - frame["discharge_load_kwh"].to_numpy(dtype=float)
    )
    if "discharge_grid_kwh" in frame.columns:
        residual = residual - frame["discharge_grid_kwh"].to_numpy(dtype=float)
    return residual


def _interval_hours(frame: pd.DataFrame) -> np.ndarray:
    if "interval_hours" in frame.columns:
        return frame["interval_hours"].to_numpy(dtype=float)
    return np.full(len(frame), INTERVAL_HOURS, dtype=float)
