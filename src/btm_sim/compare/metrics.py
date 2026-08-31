"""Documented useful-PV and peak metrics for a comparison scenario."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import cycle_limit_report
from btm_sim.compare.months import LocalMonthWindow, complete_month_keys, local_month_coverage
from btm_sim.config.schema import TariffConfig
from btm_sim.fluvius.constants import FLOAT_EPS_KWH, INTERVAL_HOURS
from btm_sim.optimizer.reporting import monthly_import_peaks_kw
from btm_sim.settlement.ledger import settle_dispatch, settle_dynamic_dispatch

SCENARIO_ORDER = (
    "no_battery",
    "reference",
    "self_consumption",
    "peak_reduction",
    "revenue",
    "dynamic_injection",
)

SCENARIO_LABELS = {
    "no_battery": "No battery",
    "reference": (
        "Simple reference controller. Chronological; uses only the current quarter-hour."
    ),
    "self_consumption": (
        "Best-case self-consumption using the complete selected period in advance. "
        "Not a forecast or expected operational saving."
    ),
    "peak_reduction": (
        "Best-case peak reduction using the complete selected period in advance. "
        "Not a forecast or expected operational saving."
    ),
    "revenue": (
        "Best-case Energent PV revenue using the complete selected period in advance. "
        "The battery first preserves the best achievable PV supply to the customer. "
        "Remaining flexibility may inject stored PV at the configured fixed tariff. "
        "The battery never charges from the grid. Not a forecast, profit, or NPV."
    ),
    "dynamic_injection": (
        "Dynamic injection tariff. The battery first preserves the best achievable PV "
        "supply to the customer. Remaining battery flexibility may inject stored PV at "
        "the supplied dynamic price. The battery never charges from the grid. "
        "Not a forecast, profit, or NPV."
    ),
}

MONTHLY_PEAKS_DESCRIPTION = (
    "A monthly peak is the highest average grid-import power recorded during a "
    "15-minute interval in that local calendar month."
)

AVERAGE_MONTHLY_PEAK_DESCRIPTION = (
    "Average monthly peak is calculated from complete local calendar months only. "
    "Partial months remain in the monthly results but are excluded from this average."
)

HIGHEST_INTERVAL_VS_MONTHLY_NOTE = (
    "The reduction in the selected period's single highest 15-minute interval does "
    "not imply the same reduction in every month. A physical peak reduction is not "
    "a customer euro saving; customer demand tariffs are not modelled."
)

ENERGENT_PV_REVENUE_NOTE = (
    "Energent PV revenue includes PV energy sold to the customer and PV injected "
    "into the grid. It is not profit, customer bill savings, NPV, or a complete "
    "business case. For the dynamic-injection case, the revenue difference is "
    "measured against the current fixed-tariff no-battery situation and therefore "
    "also includes the change of injection tariff."
)

PEAK_ZERO_EPS_KW = 1e-12

DISPATCH_METRIC_COLUMNS = (
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

NOT_APPLICABLE = "not applicable"


def ratio_or_none(numerator: float, denominator: float) -> float | None:
    if abs(denominator) <= FLOAT_EPS_KWH:
        return None
    return float(numerator) / float(denominator)


def as_percent(ratio: float | None) -> float | None:
    if ratio is None:
        return None
    return 100.0 * float(ratio)


def display_ratio(ratio: float | None) -> str:
    if ratio is None:
        return NOT_APPLICABLE
    return f"{100.0 * ratio:.2f}%"


def attach_baseline_dispatch(frame: pd.DataFrame, config: BatteryConfig) -> pd.DataFrame:
    """No-battery identity dispatch using the comparison initial charge."""
    out = frame.copy()
    n = len(out)
    zeros = np.zeros(n, dtype=float)
    soc = np.full(n, float(config.soc_initial_kwh), dtype=float)
    dt = (
        out["interval_hours"].to_numpy(dtype=float)
        if "interval_hours" in out.columns
        else np.full(n, INTERVAL_HOURS, dtype=float)
    )
    out["charge_pv_kwh"] = zeros
    out["discharge_load_kwh"] = zeros
    out["discharge_grid_kwh"] = zeros
    out["soc_start_kwh"] = soc
    out["soc_end_kwh"] = soc
    out["charge_loss_kwh"] = zeros
    out["discharge_loss_kwh"] = zeros
    out["grid_import_kwh"] = out["grid_import_baseline_kwh"].to_numpy(dtype=float)
    out["grid_export_kwh"] = out["grid_export_baseline_kwh"].to_numpy(dtype=float)
    out["grid_import_kw"] = out["grid_import_kwh"].to_numpy(dtype=float) / dt
    return out


def ensure_grid_import_kw(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "grid_import_kw" not in out.columns:
        dt = (
            out["interval_hours"].to_numpy(dtype=float)
            if "interval_hours" in out.columns
            else np.full(len(out), INTERVAL_HOURS, dtype=float)
        )
        out["grid_import_kw"] = out["grid_import_kwh"].to_numpy(dtype=float) / dt
    return out


def scenario_metrics(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    scenario: str,
    tariffs: TariffConfig | None = None,
) -> dict[str, Any]:
    work = ensure_grid_import_kw(frame)
    pv = float(work["pv_production_kwh"].sum())
    load = float(work["site_load_kwh"].sum())
    import0 = float(work["grid_import_baseline_kwh"].sum())
    export0 = float(work["grid_export_baseline_kwh"].sum())
    grid_import = float(work["grid_import_kwh"].sum())
    grid_export = float(work["grid_export_kwh"].sum())
    charge = float(work["charge_pv_kwh"].sum())
    discharge = float(work["discharge_load_kwh"].sum())
    charge_loss = float(work["charge_loss_kwh"].sum())
    discharge_loss = float(work["discharge_loss_kwh"].sum())
    total_loss = charge_loss + discharge_loss
    pv_direct = float((work["pv_production_kwh"] - work["grid_export_baseline_kwh"]).sum())
    useful_after = pv_direct + discharge
    soc_initial = float(work["soc_start_kwh"].iloc[0]) if len(work) else float(config.soc_initial_kwh)
    soc_final = float(work["soc_end_kwh"].iloc[-1]) if len(work) else float(config.soc_initial_kwh)
    cycle = cycle_limit_report(work, config)
    throughput = float(cycle["stored_throughput_kwh"])
    monthly = monthly_import_peaks_kw(work)
    dt = work["interval_hours"] if "interval_hours" in work.columns else INTERVAL_HOURS
    annual_peak = float((work["grid_import_kwh"] / dt).max()) if len(work) else 0.0
    baseline_annual = float((work["grid_import_baseline_kwh"] / dt).max()) if len(work) else 0.0
    peak_reduction_kw = baseline_annual - annual_peak
    peak_reduction_pct = None if abs(baseline_annual) <= PEAK_ZERO_EPS_KW else 100.0 * peak_reduction_kw / baseline_annual
    coverage = local_month_coverage(work)
    baseline_monthly = _baseline_monthly_peaks_kw(work, dt)

    useful_before_ratio = ratio_or_none(pv_direct, pv)
    useful_after_ratio = ratio_or_none(useful_after, pv)
    additional_share = ratio_or_none(discharge, pv)
    self_sufficiency = ratio_or_none(useful_after, load)
    change_pp = None
    if useful_before_ratio is not None and useful_after_ratio is not None:
        change_pp = 100.0 * (useful_after_ratio - useful_before_ratio)

    payload = {
        "scenario": scenario,
        "label": SCENARIO_LABELS[scenario],
        "total_pv_production_kwh": pv,
        "site_load_kwh": load,
        "useful_pv_direct_kwh": pv_direct,
        "useful_pv_delivered_kwh": useful_after,
        "additional_useful_pv_kwh": discharge,
        "additional_useful_pv_pct_of_total_pv": as_percent(additional_share),
        "useful_self_consumption_ratio_before": useful_before_ratio,
        "useful_self_consumption_ratio_after": useful_after_ratio,
        "useful_self_consumption_pct_before": as_percent(useful_before_ratio),
        "useful_self_consumption_pct_after": as_percent(useful_after_ratio),
        "useful_self_consumption_change_pp": change_pp,
        "self_sufficiency_ratio": self_sufficiency,
        "self_sufficiency_pct": as_percent(self_sufficiency),
        "grid_import_kwh": grid_import,
        "grid_export_kwh": grid_export,
        "grid_import_baseline_kwh": import0,
        "grid_export_baseline_kwh": export0,
        "annual_peak_kw": annual_peak,
        "baseline_annual_peak_kw": baseline_annual,
        "annual_peak_reduction_kw": peak_reduction_kw,
        "annual_peak_reduction_pct": peak_reduction_pct,
        "monthly_peaks_kw": monthly,
        "sum_monthly_peaks_kw": float(sum(monthly.values())),
        **average_monthly_peak_payload(monthly, baseline_monthly, coverage),
        "charge_pv_kwh": charge,
        "discharge_load_kwh": discharge,
        "battery_discharge_to_grid_kwh": float(
            work["discharge_grid_kwh"].sum() if "discharge_grid_kwh" in work.columns else 0.0
        ),
        "charge_loss_kwh": charge_loss,
        "discharge_loss_kwh": discharge_loss,
        "total_loss_kwh": total_loss,
        **cycle,
        "soc_initial_kwh": soc_initial,
        "soc_final_kwh": soc_final,
    }
    if tariffs is not None:
        if scenario == "dynamic_injection":
            payload["revenue"] = settle_dynamic_dispatch(work, tariffs).totals
        else:
            payload["revenue"] = settle_dispatch(work, tariffs).totals
    return payload


def prefixed_scenario_view(dispatch: pd.DataFrame, scenario: str) -> pd.DataFrame:
    prefix = f"{scenario}_"
    view = dispatch.copy()
    for column in DISPATCH_METRIC_COLUMNS:
        view[column] = dispatch[f"{prefix}{column}"]
    if "da_price_eur_mwh" in dispatch.columns:
        view["da_price_eur_mwh"] = dispatch["da_price_eur_mwh"]
    return view


def metrics_from_prefixed_dispatch(
    dispatch: pd.DataFrame,
    config: BatteryConfig,
    *,
    scenario: str,
    tariffs: TariffConfig | None = None,
) -> dict[str, Any]:
    return scenario_metrics(
        prefixed_scenario_view(dispatch, scenario),
        config,
        scenario=scenario,
        tariffs=tariffs,
    )


def average_monthly_peak_payload(
    monthly_peaks: dict[str, float],
    baseline_monthly_peaks: dict[str, float],
    coverage: list[LocalMonthWindow],
) -> dict[str, Any]:
    complete = complete_month_keys(coverage)
    n_complete = len(complete)
    if n_complete == 0:
        return {
            "baseline_average_monthly_peak_kw": None,
            "average_monthly_peak_kw": None,
            "average_monthly_peak_reduction_kw": None,
            "average_monthly_peak_reduction_pct": None,
            "average_monthly_peak_n_complete_months": 0,
        }
    missing = [month for month in complete if month not in monthly_peaks or month not in baseline_monthly_peaks]
    if missing:
        raise ValueError(f"missing monthly peaks for complete months: {missing}")
    average = float(sum(monthly_peaks[month] for month in complete) / n_complete)
    baseline_average = float(sum(baseline_monthly_peaks[month] for month in complete) / n_complete)
    reduction = baseline_average - average
    reduction_pct = None if abs(baseline_average) <= PEAK_ZERO_EPS_KW else 100.0 * reduction / baseline_average
    return {
        "baseline_average_monthly_peak_kw": baseline_average,
        "average_monthly_peak_kw": average,
        "average_monthly_peak_reduction_kw": reduction,
        "average_monthly_peak_reduction_pct": reduction_pct,
        "average_monthly_peak_n_complete_months": n_complete,
    }


def _baseline_monthly_peaks_kw(work: pd.DataFrame, dt: pd.Series | float) -> dict[str, float]:
    baseline_view = work.copy()
    baseline_view["grid_import_kw"] = work["grid_import_baseline_kwh"].to_numpy(dtype=float) / dt
    return monthly_import_peaks_kw(baseline_view)
