"""Tidy monthly energy, peak, and Energent-revenue summary for comparison runs."""

from __future__ import annotations

from typing import Any

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.metrics import (
    SCENARIO_LABELS,
    SCENARIO_ORDER,
    as_percent,
    metrics_from_prefixed_dispatch,
    ratio_or_none,
)
from btm_sim.compare.months import local_month_coverage, month_interval_mask
from btm_sim.config.schema import TariffConfig

MONTHLY_SUMMARY_COLUMNS = (
    "month",
    "month_start_local",
    "month_end_local_exclusive",
    "complete_local_month",
    "n_intervals",
    "scenario",
    "scenario_label",
    "total_pv_production_kwh",
    "site_load_kwh",
    "useful_pv_direct_kwh",
    "additional_useful_pv_kwh",
    "useful_pv_delivered_kwh",
    "useful_self_consumption_pct",
    "self_sufficiency_pct",
    "grid_import_kwh",
    "grid_export_kwh",
    "charge_pv_kwh",
    "total_loss_kwh",
    "stored_throughput_kwh",
    "equivalent_full_cycles",
    "baseline_monthly_peak_kw",
    "monthly_peak_kw",
    "monthly_peak_reduction_kw",
    "monthly_peak_reduction_pct",
    "direct_pv_customer_sales_mwh",
    "direct_pv_customer_sales_eur",
    "battery_customer_sales_mwh",
    "battery_customer_sales_eur",
    "total_customer_sales_mwh",
    "total_customer_sales_eur",
    "export_peak_mwh",
    "export_peak_eur",
    "export_offpeak_mwh",
    "export_offpeak_eur",
    "total_export_mwh",
    "total_export_eur",
    "total_energent_pv_revenue_eur",
    "baseline_total_energent_pv_revenue_eur",
    "revenue_change_eur",
    "revenue_change_pct",
    "extra_customer_sale_eur",
    "foregone_export_eur",
    "battery_grid_injection_revenue_eur",
    "uplift_eur",
)

ENERGY_ADDITIVE_COLUMNS = (
    "total_pv_production_kwh",
    "site_load_kwh",
    "useful_pv_direct_kwh",
    "additional_useful_pv_kwh",
    "useful_pv_delivered_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
    "charge_pv_kwh",
    "total_loss_kwh",
    "stored_throughput_kwh",
    "equivalent_full_cycles",
)

REVENUE_ADDITIVE_COLUMNS = (
    "direct_pv_customer_sales_mwh",
    "direct_pv_customer_sales_eur",
    "battery_customer_sales_mwh",
    "battery_customer_sales_eur",
    "total_customer_sales_mwh",
    "total_customer_sales_eur",
    "export_peak_mwh",
    "export_peak_eur",
    "export_offpeak_mwh",
    "export_offpeak_eur",
    "total_export_mwh",
    "total_export_eur",
    "total_energent_pv_revenue_eur",
    "baseline_total_energent_pv_revenue_eur",
    "revenue_change_eur",
    "extra_customer_sale_eur",
    "foregone_export_eur",
    "battery_grid_injection_revenue_eur",
    "uplift_eur",
)

RECONCILE_TOLERANCE = 1e-9
PEAK_ZERO_EPS_KW = 1e-12


def build_monthly_summary(
    dispatch: Any,
    config: BatteryConfig,
    tariffs: TariffConfig,
) -> list[dict[str, Any]]:
    """One self-contained row per local calendar month and scenario."""
    coverage = local_month_coverage(dispatch)
    rows: list[dict[str, Any]] = []
    for window in coverage:
        slice_ = dispatch.loc[month_interval_mask(dispatch, window)].reset_index(drop=True)
        if slice_.empty:
            continue
        by_scenario = {
            name: metrics_from_prefixed_dispatch(slice_, config, scenario=name, tariffs=tariffs)
            for name in SCENARIO_ORDER
        }
        baseline_peak = float(by_scenario["no_battery"]["annual_peak_kw"])
        baseline_revenue = float(by_scenario["no_battery"]["revenue"]["total_energent_pv_revenue_eur"])
        identity = window.to_identity()
        for name in SCENARIO_ORDER:
            metrics = by_scenario[name]
            revenue = metrics["revenue"]
            peak = float(metrics["annual_peak_kw"])
            peak_reduction = baseline_peak - peak
            peak_reduction_pct = (
                None if abs(baseline_peak) <= PEAK_ZERO_EPS_KW else 100.0 * peak_reduction / baseline_peak
            )
            row: dict[str, Any] = {
                **identity,
                "n_intervals": int(len(slice_)),
                "scenario": name,
                "scenario_label": SCENARIO_LABELS[name],
                "total_pv_production_kwh": metrics["total_pv_production_kwh"],
                "site_load_kwh": metrics["site_load_kwh"],
                "useful_pv_direct_kwh": metrics["useful_pv_direct_kwh"],
                "additional_useful_pv_kwh": metrics["additional_useful_pv_kwh"],
                "useful_pv_delivered_kwh": metrics["useful_pv_delivered_kwh"],
                "useful_self_consumption_pct": metrics["useful_self_consumption_pct_after"],
                "self_sufficiency_pct": metrics["self_sufficiency_pct"],
                "grid_import_kwh": metrics["grid_import_kwh"],
                "grid_export_kwh": metrics["grid_export_kwh"],
                "charge_pv_kwh": metrics["charge_pv_kwh"],
                "total_loss_kwh": metrics["total_loss_kwh"],
                "stored_throughput_kwh": metrics["stored_throughput_kwh"],
                "equivalent_full_cycles": metrics["equivalent_full_cycles"],
                "baseline_monthly_peak_kw": baseline_peak,
                "monthly_peak_kw": peak,
                "monthly_peak_reduction_kw": peak_reduction,
                "monthly_peak_reduction_pct": peak_reduction_pct,
                "direct_pv_customer_sales_mwh": revenue["direct_pv_customer_sales_mwh"],
                "direct_pv_customer_sales_eur": revenue["direct_pv_customer_sales_eur"],
                "battery_customer_sales_mwh": revenue["battery_customer_sales_mwh"],
                "battery_customer_sales_eur": revenue["battery_customer_sales_eur"],
                "total_customer_sales_mwh": revenue["total_customer_sales_mwh"],
                "total_customer_sales_eur": revenue["total_customer_sales_eur"],
                "export_peak_mwh": revenue["export_peak_mwh"],
                "export_peak_eur": revenue["export_peak_eur"],
                "export_offpeak_mwh": revenue["export_offpeak_mwh"],
                "export_offpeak_eur": revenue["export_offpeak_eur"],
                "total_export_mwh": revenue["total_export_mwh"],
                "total_export_eur": revenue["total_export_eur"],
                "total_energent_pv_revenue_eur": revenue["total_energent_pv_revenue_eur"],
                "baseline_total_energent_pv_revenue_eur": baseline_revenue,
                "revenue_change_eur": revenue["revenue_change_eur"],
                "revenue_change_pct": revenue["revenue_change_pct"],
                "extra_customer_sale_eur": revenue["extra_customer_sale_eur"],
                "foregone_export_eur": revenue["foregone_export_eur"],
                "battery_grid_injection_revenue_eur": float(
                    revenue.get("battery_grid_injection_revenue_eur") or 0.0
                ),
                "uplift_eur": revenue["uplift_eur"],
            }
            _assert_monthly_identities(row)
            rows.append(row)
    return rows


def reconcile_monthly_summary(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    """Fail if monthly rows do not reproduce selected-period totals and peak indicators."""
    if not rows:
        raise ValueError("monthly_summary.csv would be empty")
    by_scenario: dict[str, list[dict[str, Any]]] = {name: [] for name in SCENARIO_ORDER}
    for row in rows:
        by_scenario[row["scenario"]].append(row)

    for name in SCENARIO_ORDER:
        month_rows = by_scenario[name]
        expected = summary["scenarios"][name]
        if not month_rows:
            raise ValueError(f"{name} has no monthly rows")
        for column in ENERGY_ADDITIVE_COLUMNS:
            _assert_close(_sum_column(month_rows, column), expected[column], f"{name} monthly sum of {column}")
        revenue = expected["revenue"]
        for column in REVENUE_ADDITIVE_COLUMNS:
            _assert_close(_sum_column(month_rows, column), revenue[column], f"{name} monthly sum of {column}")

        monthly_peaks = [float(row["monthly_peak_kw"]) for row in month_rows]
        _assert_close(max(monthly_peaks), expected["annual_peak_kw"], f"{name} period max vs monthly peaks")

        complete_rows = [row for row in month_rows if row["complete_local_month"]]
        n_complete = int(expected["average_monthly_peak_n_complete_months"])
        if len(complete_rows) != n_complete:
            raise ValueError(
                f"{name} complete-month count {len(complete_rows)} vs {n_complete}"
            )
        if n_complete == 0:
            _assert_optional_close(
                expected["average_monthly_peak_kw"],
                None,
                f"{name}.average_monthly_peak_kw",
            )
            _assert_optional_close(
                expected["average_monthly_peak_reduction_kw"],
                None,
                f"{name}.average_monthly_peak_reduction_kw",
            )
            _assert_optional_close(
                expected["average_monthly_peak_reduction_pct"],
                None,
                f"{name}.average_monthly_peak_reduction_pct",
            )
        else:
            mean_peak = float(sum(float(row["monthly_peak_kw"]) for row in complete_rows) / n_complete)
            mean_baseline = float(
                sum(float(row["baseline_monthly_peak_kw"]) for row in complete_rows) / n_complete
            )
            _assert_close(mean_peak, expected["average_monthly_peak_kw"], f"{name} average monthly peak")
            _assert_close(
                mean_baseline,
                expected["baseline_average_monthly_peak_kw"],
                f"{name} baseline average monthly peak",
            )
            reduction = mean_baseline - mean_peak
            _assert_close(
                reduction,
                expected["average_monthly_peak_reduction_kw"],
                f"{name} average monthly peak reduction",
            )
            expected_pct = None if abs(mean_baseline) <= PEAK_ZERO_EPS_KW else 100.0 * reduction / mean_baseline
            _assert_optional_close(
                expected_pct,
                expected["average_monthly_peak_reduction_pct"],
                f"{name}.average_monthly_peak_reduction_pct",
            )

        no_battery_by_month = {row["month"]: row for row in by_scenario["no_battery"]}
        for row in month_rows:
            _assert_monthly_identities(row)
            _assert_percentages_from_totals(row)
            baseline_month = no_battery_by_month[row["month"]]
            _assert_close(
                row["baseline_total_energent_pv_revenue_eur"],
                baseline_month["total_energent_pv_revenue_eur"],
                f"{name} {row['month']} baseline revenue vs no-battery",
            )
            _assert_close(
                row["revenue_change_eur"],
                row["total_energent_pv_revenue_eur"] - baseline_month["total_energent_pv_revenue_eur"],
                f"{name} {row['month']} revenue change vs no-battery",
            )
            _assert_close(
                row["uplift_eur"],
                row["extra_customer_sale_eur"]
                - row["foregone_export_eur"]
                + float(row.get("battery_grid_injection_revenue_eur") or 0.0),
                f"{name} {row['month']} uplift identity",
            )
            _assert_close(
                row["monthly_peak_reduction_kw"],
                row["baseline_monthly_peak_kw"] - row["monthly_peak_kw"],
                f"{name} {row['month']} peak reduction",
            )
            _assert_close(
                row["baseline_monthly_peak_kw"],
                baseline_month["monthly_peak_kw"],
                f"{name} {row['month']} baseline peak vs no-battery",
            )


def _assert_monthly_identities(row: dict[str, Any]) -> None:
    _assert_close(
        row["total_customer_sales_eur"] + row["total_export_eur"],
        row["total_energent_pv_revenue_eur"],
        f"{row['scenario']} {row['month']} sales plus export vs total revenue",
    )
    _assert_close(
        row["revenue_change_eur"],
        row["total_energent_pv_revenue_eur"] - row["baseline_total_energent_pv_revenue_eur"],
        f"{row['scenario']} {row['month']} revenue change identity",
    )


def _assert_percentages_from_totals(row: dict[str, Any]) -> None:
    useful_pct = as_percent(ratio_or_none(row["useful_pv_delivered_kwh"], row["total_pv_production_kwh"]))
    _assert_optional_close(
        useful_pct,
        row["useful_self_consumption_pct"],
        f"{row['scenario']} {row['month']} useful self-consumption %",
    )
    sufficiency = as_percent(ratio_or_none(row["useful_pv_delivered_kwh"], row["site_load_kwh"]))
    _assert_optional_close(
        sufficiency,
        row["self_sufficiency_pct"],
        f"{row['scenario']} {row['month']} self-sufficiency %",
    )
    peak_pct = (
        None
        if abs(float(row["baseline_monthly_peak_kw"])) <= PEAK_ZERO_EPS_KW
        else 100.0 * float(row["monthly_peak_reduction_kw"]) / float(row["baseline_monthly_peak_kw"])
    )
    _assert_optional_close(
        peak_pct,
        row["monthly_peak_reduction_pct"],
        f"{row['scenario']} {row['month']} monthly peak reduction %",
    )
    revenue_pct = as_percent(
        ratio_or_none(row["revenue_change_eur"], row["baseline_total_energent_pv_revenue_eur"])
    )
    _assert_optional_close(
        revenue_pct,
        row["revenue_change_pct"],
        f"{row['scenario']} {row['month']} revenue change %",
    )


def _sum_column(rows: list[dict[str, Any]], column: str) -> float:
    return float(sum(float(row[column]) for row in rows))


def _assert_close(left: Any, right: Any, label: str) -> None:
    if abs(float(left) - float(right)) > RECONCILE_TOLERANCE:
        raise ValueError(f"{label} mismatch: {left} vs {right}")


def _assert_optional_close(left: Any, right: Any, label: str) -> None:
    if left is None or right is None:
        if left != right:
            raise ValueError(f"{label} mismatch: {left!r} vs {right!r}")
        return
    _assert_close(left, right, label)
