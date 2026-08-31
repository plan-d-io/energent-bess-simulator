"""Solver-independent Energent PV settlement ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.config.schema import TariffConfig
from btm_sim.fluvius.constants import FLOAT_EPS_KWH
from btm_sim.settlement.tariffs import TARIFF_CLASS_OFFPEAK, TARIFF_CLASS_PEAK, classify_frame

KWH_PER_MWH = 1000.0

LEDGER_COLUMNS = (
    "tariff_class",
    "export_rate_eur_per_mwh",
    "customer_rate_eur_per_mwh",
    "pv_direct_kwh",
    "direct_customer_sale_kwh",
    "direct_customer_sale_eur",
    "battery_customer_sale_kwh",
    "battery_customer_sale_eur",
    "export_kwh",
    "export_eur",
    "total_energent_pv_revenue_eur",
    "extra_customer_sale_eur",
    "foregone_export_eur",
    "battery_grid_injection_eur",
)

PREFIXED_LEDGER_COLUMNS = (
    "direct_customer_sale_kwh",
    "direct_customer_sale_eur",
    "battery_customer_sale_kwh",
    "battery_customer_sale_eur",
    "export_eur",
    "total_energent_pv_revenue_eur",
    "extra_customer_sale_eur",
    "foregone_export_eur",
    "battery_grid_injection_eur",
)


@dataclass(frozen=True)
class SettlementResult:
    ledger: pd.DataFrame
    totals: dict[str, Any]


def settle_dispatch(frame: pd.DataFrame, tariffs: TariffConfig) -> SettlementResult:
    """Build an interval ledger and reconciled Energent PV revenue totals.

    Rates are EUR/MWh. Energy is converted from kWh to MWh exactly once.
    Charging energy and conversion losses are never customer sales.
    Foregone export is valued at the charging interval's export rate.
    """
    work = frame.reset_index(drop=True)
    classified = classify_frame(work, tariffs)
    pv = _col(work, "pv_production_kwh")
    export0 = _col(work, "grid_export_baseline_kwh")
    pv_direct = pv - export0
    charge = _col(work, "charge_pv_kwh") if "charge_pv_kwh" in work.columns else np.zeros(len(work))
    discharge = (
        _col(work, "discharge_load_kwh") if "discharge_load_kwh" in work.columns else np.zeros(len(work))
    )
    discharge_grid = (
        _col(work, "discharge_grid_kwh") if "discharge_grid_kwh" in work.columns else np.zeros(len(work))
    )
    export = _col(work, "grid_export_kwh") if "grid_export_kwh" in work.columns else export0
    r_c = classified["customer_rate_eur_per_mwh"].to_numpy(dtype=float)
    r_e = classified["export_rate_eur_per_mwh"].to_numpy(dtype=float)

    direct_eur = r_c * pv_direct / KWH_PER_MWH
    battery_eur = r_c * discharge / KWH_PER_MWH
    export_eur = r_e * export / KWH_PER_MWH
    baseline_export_eur = r_e * export0 / KWH_PER_MWH
    total_eur = direct_eur + battery_eur + export_eur
    extra_customer = battery_eur
    foregone = r_e * charge / KWH_PER_MWH
    grid_injection = r_e * discharge_grid / KWH_PER_MWH

    ledger = pd.DataFrame(
        {
            "tariff_class": classified["tariff_class"].to_numpy(),
            "export_rate_eur_per_mwh": r_e,
            "customer_rate_eur_per_mwh": r_c,
            "pv_direct_kwh": pv_direct,
            "direct_customer_sale_kwh": pv_direct,
            "direct_customer_sale_eur": direct_eur,
            "battery_customer_sale_kwh": discharge,
            "battery_customer_sale_eur": battery_eur,
            "export_kwh": export,
            "export_eur": export_eur,
            "total_energent_pv_revenue_eur": total_eur,
            "extra_customer_sale_eur": extra_customer,
            "foregone_export_eur": foregone,
            "battery_grid_injection_eur": grid_injection,
        }
    )
    classes = ledger["tariff_class"].to_numpy()
    peak = classes == TARIFF_CLASS_PEAK
    offpeak = classes == TARIFF_CLASS_OFFPEAK
    baseline_revenue = float(direct_eur.sum() + baseline_export_eur.sum())
    battery_revenue = float(total_eur.sum())
    change = battery_revenue - baseline_revenue
    change_pct = None if abs(baseline_revenue) <= FLOAT_EPS_KWH else 100.0 * change / baseline_revenue
    totals = {
        "direct_pv_customer_sales_mwh": float(pv_direct.sum() / KWH_PER_MWH),
        "direct_pv_customer_sales_eur": float(direct_eur.sum()),
        "battery_customer_sales_mwh": float(discharge.sum() / KWH_PER_MWH),
        "battery_customer_sales_eur": float(battery_eur.sum()),
        "total_customer_sales_mwh": float((pv_direct + discharge).sum() / KWH_PER_MWH),
        "total_customer_sales_eur": float((direct_eur + battery_eur).sum()),
        "export_peak_mwh": float(export[peak].sum() / KWH_PER_MWH),
        "export_peak_eur": float(export_eur[peak].sum()),
        "export_offpeak_mwh": float(export[offpeak].sum() / KWH_PER_MWH),
        "export_offpeak_eur": float(export_eur[offpeak].sum()),
        "total_export_mwh": float(export.sum() / KWH_PER_MWH),
        "total_export_eur": float(export_eur.sum()),
        "total_energent_pv_revenue_eur": battery_revenue,
        "baseline_total_energent_pv_revenue_eur": baseline_revenue,
        "revenue_change_eur": change,
        "revenue_change_pct": change_pct,
        "extra_customer_sale_eur": float(extra_customer.sum()),
        "foregone_export_eur": float(foregone.sum()),
        "battery_grid_injection_revenue_eur": float(grid_injection.sum()),
        "uplift_eur": float((extra_customer - foregone + grid_injection).sum()),
    }
    _check_identities(totals, r_c, r_e, pv_direct, discharge, charge, export, export0, discharge_grid)
    return SettlementResult(ledger=ledger, totals=totals)


def settle_dynamic_dispatch(frame: pd.DataFrame, tariffs: TariffConfig) -> SettlementResult:
    """Value customer sales at the fixed customer tariff and all injection at DA prices.

    The financial baseline remains the fixed-tariff no-battery case on the same
    intervals, so the displayed revenue difference includes the tariff change.
    """
    if "da_price_eur_mwh" not in frame.columns:
        raise ValueError("Dynamic settlement requires da_price_eur_mwh on the dispatch frame")
    work = frame.reset_index(drop=True)
    classified = classify_frame(work, tariffs)
    pv = _col(work, "pv_production_kwh")
    export0 = _col(work, "grid_export_baseline_kwh")
    pv_direct = pv - export0
    charge = _col(work, "charge_pv_kwh") if "charge_pv_kwh" in work.columns else np.zeros(len(work))
    discharge = (
        _col(work, "discharge_load_kwh") if "discharge_load_kwh" in work.columns else np.zeros(len(work))
    )
    export = _col(work, "grid_export_kwh") if "grid_export_kwh" in work.columns else export0
    r_c = classified["customer_rate_eur_per_mwh"].to_numpy(dtype=float)
    r_da = _col(work, "da_price_eur_mwh")

    direct_eur = r_c * pv_direct / KWH_PER_MWH
    battery_eur = r_c * discharge / KWH_PER_MWH
    export_eur = r_da * export / KWH_PER_MWH
    total_eur = direct_eur + battery_eur + export_eur
    extra_customer = battery_eur
    foregone = r_da * charge / KWH_PER_MWH
    baseline = settle_dispatch(
        work.assign(
            charge_pv_kwh=0.0,
            discharge_load_kwh=0.0,
            discharge_grid_kwh=0.0,
            grid_export_kwh=export0,
        ),
        tariffs,
    )
    baseline_revenue = float(baseline.totals["total_energent_pv_revenue_eur"])
    battery_revenue = float(total_eur.sum())
    change = battery_revenue - baseline_revenue
    change_pct = None if abs(baseline_revenue) <= FLOAT_EPS_KWH else 100.0 * change / baseline_revenue
    classes = classified["tariff_class"].to_numpy()
    peak = classes == TARIFF_CLASS_PEAK
    offpeak = classes == TARIFF_CLASS_OFFPEAK
    ledger = pd.DataFrame(
        {
            "tariff_class": classes,
            "export_rate_eur_per_mwh": r_da,
            "customer_rate_eur_per_mwh": r_c,
            "pv_direct_kwh": pv_direct,
            "direct_customer_sale_kwh": pv_direct,
            "direct_customer_sale_eur": direct_eur,
            "battery_customer_sale_kwh": discharge,
            "battery_customer_sale_eur": battery_eur,
            "export_kwh": export,
            "export_eur": export_eur,
            "total_energent_pv_revenue_eur": total_eur,
            "extra_customer_sale_eur": extra_customer,
            "foregone_export_eur": foregone,
            "battery_grid_injection_eur": np.zeros(len(work), dtype=float),
        }
    )
    totals = {
        "direct_pv_customer_sales_mwh": float(pv_direct.sum() / KWH_PER_MWH),
        "direct_pv_customer_sales_eur": float(direct_eur.sum()),
        "battery_customer_sales_mwh": float(discharge.sum() / KWH_PER_MWH),
        "battery_customer_sales_eur": float(battery_eur.sum()),
        "total_customer_sales_mwh": float((pv_direct + discharge).sum() / KWH_PER_MWH),
        "total_customer_sales_eur": float((direct_eur + battery_eur).sum()),
        "export_peak_mwh": float(export[peak].sum() / KWH_PER_MWH),
        "export_peak_eur": float(export_eur[peak].sum()),
        "export_offpeak_mwh": float(export[offpeak].sum() / KWH_PER_MWH),
        "export_offpeak_eur": float(export_eur[offpeak].sum()),
        "total_export_mwh": float(export.sum() / KWH_PER_MWH),
        "total_export_eur": float(export_eur.sum()),
        "dynamic_grid_injection_revenue_eur": float(export_eur.sum()),
        "total_energent_pv_revenue_eur": battery_revenue,
        "baseline_total_energent_pv_revenue_eur": baseline_revenue,
        "revenue_change_eur": change,
        "revenue_change_pct": change_pct,
        "extra_customer_sale_eur": float(extra_customer.sum()),
        "foregone_export_eur": float(foregone.sum()),
        "battery_grid_injection_revenue_eur": 0.0,
        "uplift_eur": float((extra_customer - foregone).sum()),
        "settlement_mode": "dynamic_injection",
        "financial_baseline": "fixed_tariff_no_battery",
    }
    if abs((direct_eur + battery_eur + export_eur).sum() - battery_revenue) > 1e-9:
        raise ValueError("Dynamic battery revenue identity failed")
    return SettlementResult(ledger=ledger, totals=totals)


def attach_ledger_columns(frame: pd.DataFrame, tariffs: TariffConfig) -> pd.DataFrame:
    settled = settle_dispatch(frame, tariffs)
    out = frame.copy()
    for column in LEDGER_COLUMNS:
        out[column] = settled.ledger[column].to_numpy()
    return out


def _col(frame: pd.DataFrame, name: str) -> np.ndarray:
    return frame[name].to_numpy(dtype=float)


def _check_identities(
    totals: dict[str, Any],
    r_c: np.ndarray,
    r_e: np.ndarray,
    pv_direct: np.ndarray,
    discharge: np.ndarray,
    charge: np.ndarray,
    export: np.ndarray,
    export0: np.ndarray,
    discharge_grid: np.ndarray,
) -> None:
    r0 = float((r_c * pv_direct + r_e * export0).sum() / KWH_PER_MWH)
    rb = float((r_c * (pv_direct + discharge) + r_e * export).sum() / KWH_PER_MWH)
    uplift = float((r_c * discharge - r_e * charge + r_e * discharge_grid).sum() / KWH_PER_MWH)
    if abs(r0 - totals["baseline_total_energent_pv_revenue_eur"]) > 1e-9:
        raise ValueError("Baseline revenue identity failed")
    if abs(rb - totals["total_energent_pv_revenue_eur"]) > 1e-9:
        raise ValueError("Battery revenue identity failed")
    if abs(uplift - totals["uplift_eur"]) > 1e-9:
        raise ValueError("Revenue uplift identity failed")
    expected_change = (
        totals["extra_customer_sale_eur"]
        - totals["foregone_export_eur"]
        + totals["battery_grid_injection_revenue_eur"]
    )
    if abs(totals["uplift_eur"] - expected_change) > 1e-9:
        raise ValueError(
            "Uplift does not equal extra customer sales minus foregone export "
            "plus battery grid-injection revenue"
        )
    if abs(totals["revenue_change_eur"] - totals["uplift_eur"]) > 1e-9:
        raise ValueError("revenue_change_eur does not match uplift_eur")
