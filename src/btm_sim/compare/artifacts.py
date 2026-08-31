"""Write the auditable comparison run directory."""

from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.exceptions import ComparisonError
from btm_sim.compare.metrics import (
    DISPATCH_METRIC_COLUMNS,
    MONTHLY_PEAKS_DESCRIPTION,
    SCENARIO_ORDER,
    metrics_from_prefixed_dispatch,
)
from btm_sim.compare.monthly import MONTHLY_SUMMARY_COLUMNS
from btm_sim.config.schema import TariffConfig
from btm_sim.fluvius.constants import CANONICAL_COLUMNS, TZ_NAME
from btm_sim.fluvius.csv_io import sha256_file
from btm_sim.settlement.ledger import PREFIXED_LEDGER_COLUMNS, settle_dispatch, settle_dynamic_dispatch
from btm_sim.settlement.tariffs import classify_frame

ARTIFACT_SCHEMA_VERSION = 2
PARQUET_ROW_GROUP_SIZE = 672
PARQUET_COMPRESSION = "zstd"

SUMMARY_CSV_COLUMNS = (
    "scenario",
    "label",
    "total_pv_production_kwh",
    "useful_pv_direct_kwh",
    "useful_pv_delivered_kwh",
    "additional_useful_pv_kwh",
    "additional_useful_pv_pct_of_total_pv",
    "useful_self_consumption_pct_before",
    "useful_self_consumption_pct_after",
    "useful_self_consumption_change_pp",
    "grid_import_kwh",
    "grid_export_kwh",
    "annual_peak_kw",
    "annual_peak_reduction_kw",
    "annual_peak_reduction_pct",
    "baseline_average_monthly_peak_kw",
    "average_monthly_peak_kw",
    "average_monthly_peak_reduction_kw",
    "average_monthly_peak_reduction_pct",
    "average_monthly_peak_n_complete_months",
    "sum_monthly_peaks_kw",
    "charge_pv_kwh",
    "discharge_load_kwh",
    "battery_discharge_to_grid_kwh",
    "total_loss_kwh",
        "stored_throughput_kwh",
        "equivalent_full_cycles",
        "max_equivalent_full_cycles_per_year",
        "selected_period_year_fraction",
        "allowed_equivalent_full_cycles",
        "remaining_equivalent_full_cycles_allowance",
        "cycle_limit_binding",
        "soc_initial_kwh",
    "soc_final_kwh",
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
    "total_export_eur",
    "total_energent_pv_revenue_eur",
    "revenue_change_eur",
    "revenue_change_pct",
    "period_revenue_uplift_eur",
    "annual_revenue_uplift_eur",
    "simple_payback_years",
    "payback_applicable",
    "estimated_battery_capex_eur",
    "extra_customer_sale_eur",
    "foregone_export_eur",
    "battery_grid_injection_revenue_eur",
    "uplift_eur",
)

REVENUE_RECONCILE_KEYS = (
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
    "total_export_eur",
    "total_energent_pv_revenue_eur",
    "revenue_change_eur",
    "extra_customer_sale_eur",
    "foregone_export_eur",
    "uplift_eur",
    "battery_grid_injection_revenue_eur",
)


def build_comparison_dispatch(
    canonical: pd.DataFrame,
    cases: dict[str, pd.DataFrame],
    tariffs: TariffConfig,
    *,
    da_prices: pd.Series | np.ndarray | None = None,
) -> pd.DataFrame:
    out = canonical.loc[:, [column for column in CANONICAL_COLUMNS if column in canonical.columns]].copy()
    out["pv_direct_kwh"] = (
        out["pv_production_kwh"].to_numpy(dtype=float) - out["grid_export_baseline_kwh"].to_numpy(dtype=float)
    )
    classified = classify_frame(out, tariffs)
    out["tariff_class"] = classified["tariff_class"].to_numpy()
    out["export_rate_eur_per_mwh"] = classified["export_rate_eur_per_mwh"].to_numpy(dtype=float)
    out["customer_rate_eur_per_mwh"] = classified["customer_rate_eur_per_mwh"].to_numpy(dtype=float)
    extra: dict[str, np.ndarray] = {}
    if da_prices is not None:
        extra["da_price_eur_mwh"] = np.asarray(da_prices, dtype=float)
    elif "da_price_eur_mwh" in canonical.columns:
        extra["da_price_eur_mwh"] = canonical["da_price_eur_mwh"].to_numpy(dtype=float)
    for scenario in SCENARIO_ORDER:
        frame = cases[scenario].copy()
        if "discharge_grid_kwh" not in frame.columns:
            frame["discharge_grid_kwh"] = 0.0
        for column in DISPATCH_METRIC_COLUMNS:
            extra[f"{scenario}_{column}"] = frame[column].to_numpy(dtype=float)
        if scenario == "dynamic_injection":
            if "da_price_eur_mwh" not in frame.columns and "da_price_eur_mwh" in extra:
                frame["da_price_eur_mwh"] = extra["da_price_eur_mwh"]
            settled = settle_dynamic_dispatch(frame, tariffs)
        else:
            settled = settle_dispatch(frame, tariffs)
        for column in PREFIXED_LEDGER_COLUMNS:
            extra[f"{scenario}_{column}"] = settled.ledger[column].to_numpy(dtype=float)
    return pd.concat([out, pd.DataFrame(extra, index=out.index)], axis=1)


def write_run_directory(
    *,
    run_dir: Path,
    source_path: Path | None,
    frame: pd.DataFrame,
    dispatch: pd.DataFrame,
    summary: dict[str, Any],
    metadata: dict[str, Any],
    config: BatteryConfig,
    monthly_rows: list[dict[str, Any]],
    aligned_prices: pd.DataFrame | None = None,
) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = run_dir / "normalized_input.parquet"
    if source_path is not None and Path(source_path).exists():
        source = Path(source_path).resolve()
        if source == parquet_path.resolve():
            pass
        else:
            shutil.copy2(source, parquet_path)
    else:
        frame.to_parquet(parquet_path, index=False)

    dispatch_path = run_dir / "comparison_dispatch.csv"
    dispatch_parquet_path = run_dir / "comparison_dispatch.parquet"
    summary_json_path = run_dir / "comparison_summary.json"
    summary_csv_path = run_dir / "comparison_summary.csv"
    monthly_path = run_dir / "monthly_peaks.csv"
    monthly_summary_path = run_dir / "monthly_summary.csv"
    metadata_path = run_dir / "run_metadata.json"

    dispatch_path.write_text(_dispatch_csv(dispatch), encoding="utf-8")
    write_dispatch_parquet(dispatch, dispatch_parquet_path)
    summary_csv_path.write_text(_summary_csv(summary["scenarios"], config), encoding="utf-8")
    monthly_path.write_text(_monthly_csv(summary["scenarios"]), encoding="utf-8")
    monthly_summary_path.write_text(_monthly_summary_csv(monthly_rows), encoding="utf-8")
    summary_json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    paths = {
        "normalized_input": parquet_path,
        "run_metadata": metadata_path,
        "comparison_summary_json": summary_json_path,
        "comparison_summary_csv": summary_csv_path,
        "monthly_peaks": monthly_path,
        "monthly_summary": monthly_summary_path,
        "comparison_dispatch": dispatch_path,
        "comparison_dispatch_parquet": dispatch_parquet_path,
    }
    if aligned_prices is not None:
        prices_path = run_dir / "dynamic_injection_prices.parquet"
        aligned_prices.to_parquet(prices_path, index=False)
        paths["dynamic_injection_prices"] = prices_path
    return paths


def write_dispatch_parquet(dispatch: pd.DataFrame, path: Path) -> None:
    """Write the comparison dispatch as compressed, filterable Parquet."""
    work = _prepare_dispatch_for_parquet(dispatch)
    table = pa.Table.from_pandas(work, preserve_index=False)
    pq.write_table(
        table,
        path,
        compression=PARQUET_COMPRESSION,
        row_group_size=PARQUET_ROW_GROUP_SIZE,
    )


def _prepare_dispatch_for_parquet(dispatch: pd.DataFrame) -> pd.DataFrame:
    work = dispatch.copy()
    utc = pd.to_datetime(work["timestamp_utc"], utc=True)
    if utc.duplicated().any():
        raise ComparisonError("comparison_dispatch.parquet requires unique timestamp_utc values")
    if len(utc) > 1:
        ordered = utc.sort_values()
        if (ordered.diff().iloc[1:] <= pd.Timedelta(0)).any():
            raise ComparisonError(
                "comparison_dispatch.parquet requires strictly increasing timestamp_utc values"
            )
        work = work.loc[ordered.index].reset_index(drop=True)
        utc = pd.to_datetime(work["timestamp_utc"], utc=True)
    local = pd.to_datetime(work["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    work["timestamp_utc"] = utc
    work["timestamp_local"] = local
    return work


def dispatch_csv_reconciles(
    dispatch: pd.DataFrame,
    summary: dict[str, Any],
    config: BatteryConfig,
    tariffs: TariffConfig | None = None,
) -> None:
    for scenario, expected in summary["scenarios"].items():
        rebuilt = metrics_from_prefixed_dispatch(dispatch, config, scenario=scenario, tariffs=tariffs)
        for key in (
            "total_pv_production_kwh",
            "useful_pv_direct_kwh",
            "useful_pv_delivered_kwh",
            "additional_useful_pv_kwh",
            "grid_import_kwh",
            "grid_export_kwh",
            "annual_peak_kw",
            "annual_peak_reduction_kw",
            "charge_pv_kwh",
            "discharge_load_kwh",
            "total_loss_kwh",
            "stored_throughput_kwh",
            "soc_initial_kwh",
            "soc_final_kwh",
        ):
            left = rebuilt[key]
            right = expected[key]
            if left is None or right is None:
                if left != right:
                    raise ValueError(f"{scenario}.{key} mismatch: {left!r} vs {right!r}")
                continue
            if abs(float(left) - float(right)) > 1e-9:
                raise ValueError(f"{scenario}.{key} mismatch: {left} vs {right}")
        for key in (
            "annual_peak_reduction_pct",
            "baseline_average_monthly_peak_kw",
            "average_monthly_peak_kw",
            "average_monthly_peak_reduction_kw",
            "average_monthly_peak_reduction_pct",
        ):
            _assert_optional_close(rebuilt.get(key), expected.get(key), f"{scenario}.{key}")
        if int(rebuilt["average_monthly_peak_n_complete_months"]) != int(
            expected["average_monthly_peak_n_complete_months"]
        ):
            raise ValueError(
                f"{scenario}.average_monthly_peak_n_complete_months mismatch: "
                f"{rebuilt['average_monthly_peak_n_complete_months']} vs "
                f"{expected['average_monthly_peak_n_complete_months']}"
            )
        for month, value in rebuilt["monthly_peaks_kw"].items():
            expected_peak = expected["monthly_peaks_kw"].get(month)
            if expected_peak is None or abs(float(value) - float(expected_peak)) > 1e-9:
                raise ValueError(f"{scenario} monthly peak {month} mismatch")
        if tariffs is not None:
            left_rev = rebuilt["revenue"]
            right_rev = expected["revenue"]
            for key in REVENUE_RECONCILE_KEYS:
                if abs(float(left_rev[key]) - float(right_rev[key])) > 1e-9:
                    raise ValueError(f"{scenario}.revenue.{key} mismatch: {left_rev[key]} vs {right_rev[key]}")
            _assert_optional_close(
                left_rev.get("revenue_change_pct"),
                right_rev.get("revenue_change_pct"),
                f"{scenario}.revenue.revenue_change_pct",
            )
            prefix = f"{scenario}_"
            csv_revenue = float(dispatch[f"{prefix}total_energent_pv_revenue_eur"].sum())
            if abs(csv_revenue - float(right_rev["total_energent_pv_revenue_eur"])) > 1e-9:
                raise ValueError(f"{scenario} prefixed ledger revenue mismatch")
            csv_uplift = float(
                dispatch[f"{prefix}extra_customer_sale_eur"].sum() - dispatch[f"{prefix}foregone_export_eur"].sum()
            )
            injection_col = f"{prefix}battery_grid_injection_eur"
            if injection_col in dispatch.columns:
                csv_uplift += float(dispatch[injection_col].sum())
            if abs(csv_uplift - float(right_rev["uplift_eur"])) > 1e-9:
                raise ValueError(f"{scenario} prefixed uplift mismatch")


def _assert_optional_close(left: Any, right: Any, label: str) -> None:
    if left is None or right is None:
        if left != right:
            raise ValueError(f"{label} mismatch: {left!r} vs {right!r}")
        return
    if abs(float(left) - float(right)) > 1e-9:
        raise ValueError(f"{label} mismatch: {left} vs {right}")


def _dispatch_csv(frame: pd.DataFrame) -> str:
    export = frame.copy()
    export["timestamp_utc"] = [pd.Timestamp(value).isoformat() for value in export["timestamp_utc"]]
    export["timestamp_local"] = [pd.Timestamp(value).isoformat() for value in export["timestamp_local"]]
    return export.to_csv(index=False)


def _csv_cell(value: Any) -> str | float | int:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _summary_csv(scenarios: dict[str, dict[str, Any]], config: BatteryConfig) -> str:
    del config
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(SUMMARY_CSV_COLUMNS)
    for scenario in SCENARIO_ORDER:
        row = scenarios[scenario]
        writer.writerow([_csv_cell(_summary_value(row, column)) for column in SUMMARY_CSV_COLUMNS])
    return buffer.getvalue()


def _summary_value(row: dict[str, Any], column: str) -> Any:
    if column in row:
        return row[column]
    revenue = row.get("revenue") or {}
    return revenue.get(column)


def _monthly_csv(scenarios: dict[str, dict[str, Any]]) -> str:
    months: list[str] = []
    seen: set[str] = set()
    for scenario in SCENARIO_ORDER:
        for month in scenarios[scenario]["monthly_peaks_kw"]:
            if month not in seen:
                seen.add(month)
                months.append(month)
    months = sorted(months)
    header = ["month", *[f"{scenario}_kw" for scenario in SCENARIO_ORDER]]
    buffer = io.StringIO()
    buffer.write("# " + MONTHLY_PEAKS_DESCRIPTION + "\n")
    writer = csv.writer(buffer)
    writer.writerow(header)
    for month in months:
        writer.writerow(
            [month, *[_csv_cell(scenarios[scenario]["monthly_peaks_kw"].get(month)) for scenario in SCENARIO_ORDER]]
        )
    return buffer.getvalue()


def _monthly_summary_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(MONTHLY_SUMMARY_COLUMNS)
    for row in rows:
        writer.writerow([_csv_cell(row[column]) for column in MONTHLY_SUMMARY_COLUMNS])
    return buffer.getvalue()


def input_quality(frame: pd.DataFrame) -> dict[str, Any]:
    flag = frame["quality_flag"] if "quality_flag" in frame.columns else pd.Series(["validated"] * len(frame))
    return {
        "n_intervals": int(len(frame)),
        "n_validated": int((flag == "validated").sum()),
        "n_unvalidated": int((flag == "unvalidated").sum()),
        "n_unavailable": int((flag == "unavailable").sum()),
        "pv_source": None if "pv_source" not in frame.columns else str(frame["pv_source"].iloc[0]),
    }


def sibling_validation(
    source_path: Path | None,
    report_path: Path | None = None,
) -> dict[str, Any] | None:
    if report_path is None:
        if source_path is None:
            return None
        report_path = source_path.parent / "validation_report.json"
    if not report_path.exists():
        return None
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "path": str(report_path),
        "sha256": sha256_file(report_path),
        "ok": payload.get("ok"),
        "warnings": payload.get("warnings", []),
        "unvalidated_policy": payload.get("unvalidated_policy"),
        "site_boundary_policy": payload.get("site_boundary_policy"),
        "selected_period": payload.get("selected_period"),
    }
