"""Write and reconcile the sweep audit folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from btm_sim.sweep.exceptions import SweepExecutionError
from btm_sim.sweep.runner import RevenueSweepRun
from btm_sim.sweep.site import SiteAnalysis

SWEEP_ARTIFACT_SCHEMA_VERSION = 1
SWEEP_REQUEST_FILENAME = "sweep_request.json"

SUMMARY_COLUMNS = (
    "candidate_id",
    "duration_hours",
    "power_kw",
    "usable_energy_kwh",
    "estimated_capex_eur",
    "period_revenue_uplift_eur",
    "annual_revenue_uplift_eur",
    "total_energent_pv_revenue_eur",
    "simple_payback_years",
    "estimated_value_eur",
    "payback_within_evaluation_period",
    "useful_pv_delivered_kwh",
    "additional_useful_pv_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
    "baseline_annual_peak_kw",
    "annual_peak_kw",
    "annual_peak_reduction_kw",
    "annual_peak_reduction_pct",
    "baseline_average_monthly_peak_kw",
    "average_monthly_peak_kw",
    "average_monthly_peak_reduction_kw",
    "average_monthly_peak_reduction_pct",
    "average_monthly_peak_n_complete_months",
    "charge_pv_kwh",
    "discharge_load_kwh",
    "total_loss_kwh",
    "stored_throughput_kwh",
    "equivalent_full_cycles",
    "allowed_equivalent_full_cycles",
    "remaining_equivalent_full_cycles_allowance",
    "cycle_limit_binding",
    "soc_initial_kwh",
    "soc_final_kwh",
    "solver_status",
    "solver_name",
    "solver_highspy_version",
    "solver_highs_version",
    "solver_runtime_s",
    "solver_num_vars",
    "solver_num_constrs",
    "solver_num_int_vars",
    "solver_num_bin_vars",
    "continuous_lp",
    "feasibility_ok",
    "exceeds_p95_daily_pv_surplus",
    "exceeds_p95_daily_import",
)

NUMERIC_RECONCILE_COLUMNS = (
    "duration_hours",
    "power_kw",
    "usable_energy_kwh",
    "estimated_capex_eur",
    "period_revenue_uplift_eur",
    "annual_revenue_uplift_eur",
    "total_energent_pv_revenue_eur",
    "simple_payback_years",
    "estimated_value_eur",
    "useful_pv_delivered_kwh",
    "additional_useful_pv_kwh",
    "grid_import_kwh",
    "grid_export_kwh",
    "baseline_annual_peak_kw",
    "annual_peak_kw",
    "annual_peak_reduction_kw",
    "annual_peak_reduction_pct",
    "baseline_average_monthly_peak_kw",
    "average_monthly_peak_kw",
    "average_monthly_peak_reduction_kw",
    "average_monthly_peak_reduction_pct",
    "average_monthly_peak_n_complete_months",
    "charge_pv_kwh",
    "discharge_load_kwh",
    "total_loss_kwh",
    "stored_throughput_kwh",
    "equivalent_full_cycles",
    "allowed_equivalent_full_cycles",
    "remaining_equivalent_full_cycles_allowance",
    "soc_initial_kwh",
    "soc_final_kwh",
    "solver_runtime_s",
    "solver_num_vars",
    "solver_num_constrs",
    "solver_num_int_vars",
    "solver_num_bin_vars",
)

FORBIDDEN_OUTPUT_NAMES = {
    "dynamic_injection_prices.parquet",
    "comparison_summary.json",
    "comparison_summary.csv",
    "comparison_dispatch.csv",
    "comparison_dispatch.parquet",
    "monthly_summary.csv",
    "monthly_peaks.csv",
    "revenue_dispatch.csv",
}


def _sweep_solver_info(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"name": None}
    name = rows[0].get("solver_name")
    payload: dict[str, Any] = {"name": name}
    if rows[0].get("solver_highspy_version") is not None:
        payload["highspy_version"] = rows[0]["solver_highspy_version"]
    if rows[0].get("solver_highs_version") is not None:
        payload["highs_version"] = rows[0]["solver_highs_version"]
    if name == "HiGHS":
        payload["production_backend"] = True
    elif name == "Gurobi":
        payload["production_backend"] = False
    return payload


def build_sweep_summary(run: RevenueSweepRun, site: SiteAnalysis) -> dict[str, Any]:
    solver_info = _sweep_solver_info(run.rows)
    return {
        "sweep_artifact_schema_version": SWEEP_ARTIFACT_SCHEMA_VERSION,
        "ok": run.ok,
        "optimizer": "revenue",
        "solver": solver_info,
        "n_candidates": len(run.rows),
        "candidates": list(run.rows),
        "baseline": run.baseline,
        "recommendation": run.recommendation["recommendation"],
        "best_per_duration": run.recommendation["best_per_duration"],
        "screening_summary": run.recommendation["screening_summary"],
        "peak_summary": run.recommendation["peak_summary"],
        "no_battery_estimated_value_eur": run.recommendation["no_battery_estimated_value_eur"],
        "period": run.period,
        "selected_period_year_fraction": run.year_fraction,
        "annualized_from_partial_period": run.annualized_from_partial_period,
        "site_analysis": {
            "quantile_method": site.quantile_method,
            "reference_power_kw": site.reference_power_kw,
            "no_revenue_shifting_opportunity": site.no_revenue_shifting_opportunity,
            "diagnostic": site.diagnostic,
            "candidate_generation_method": site.candidate_generation_method,
        },
        "sweep": run.sweep_config.to_dict(),
        "battery_template": {
            "eta_charge": run.battery_template.eta_charge,
            "eta_discharge": run.battery_template.eta_discharge,
            "soc_initial_kwh": 0.0,
            "max_equivalent_full_cycles_per_year": (
                run.battery_template.max_equivalent_full_cycles_per_year
            ),
        },
        "tariffs": run.tariffs.to_dict(),
        **run.explanations,
    }


def write_sweep_directory(
    *,
    run_dir: Path,
    run: RevenueSweepRun,
    site: SiteAnalysis,
    request_payload: dict[str, Any] | None,
    config_audit: dict[str, Any] | None,
) -> dict[str, Path]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = build_sweep_summary(run, site)
    table = pd.DataFrame(run.rows, columns=list(SUMMARY_COLUMNS))
    json_path = run_dir / "sweep_summary.json"
    csv_path = run_dir / "sweep_summary.csv"
    parquet_path = run_dir / "sweep_summary.parquet"
    site_path = run_dir / "site_analysis.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_summary_csv(table, csv_path)
    table.to_parquet(parquet_path, index=False)
    site_path.write_text(json.dumps(site.to_dict(), indent=2) + "\n", encoding="utf-8")
    reconcile_sweep_tables(summary, csv_path, parquet_path)
    resolved = _write_resolved_config(run_dir, run=run, audit=config_audit)
    metadata = {
        "sweep_artifact_schema_version": SWEEP_ARTIFACT_SCHEMA_VERSION,
        "software_version": None if request_payload is None else request_payload.get("software_version"),
        "job_id": None if request_payload is None else request_payload.get("job_id"),
        "period_id": None if request_payload is None else request_payload.get("period_id"),
        "n_candidates": len(run.rows),
        "annualized_from_partial_period": run.annualized_from_partial_period,
        "screening_summary": summary["screening_summary"],
        "peak_summary": summary["peak_summary"],
        "candidate_generation_method": site.candidate_generation_method,
        "solver": _sweep_solver_info(run.rows),
        "filenames": sorted(
            {
                "sweep_summary.json",
                "sweep_summary.csv",
                "sweep_summary.parquet",
                "site_analysis.json",
                "sweep_metadata.json",
                *resolved,
            }
        ),
        "warnings": list(run.explanations.get("warnings") or []),
    }
    metadata_path = run_dir / "sweep_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    _reject_forbidden_outputs(run_dir)
    return {
        "sweep_summary_json": json_path,
        "sweep_summary_csv": csv_path,
        "sweep_summary_parquet": parquet_path,
        "site_analysis": site_path,
        "sweep_metadata": metadata_path,
    }


def reconcile_sweep_tables(summary: dict[str, Any], csv_path: Path, parquet_path: Path) -> None:
    csv_table = pd.read_csv(csv_path)
    parquet_table = pd.read_parquet(parquet_path)
    expected = summary["candidates"]
    if len(csv_table) != len(expected) or len(parquet_table) != len(expected):
        raise SweepExecutionError(
            "sweep_summary CSV/Parquet row counts do not match the JSON summary",
            category="artifact_write",
        )
    for index, row in enumerate(expected):
        _reconcile_row(row, csv_table.iloc[index], "CSV")
        _reconcile_row(row, parquet_table.iloc[index], "Parquet")


def _reconcile_row(expected: dict[str, Any], actual: pd.Series, label: str) -> None:
    if str(actual["candidate_id"]) != str(expected["candidate_id"]):
        raise SweepExecutionError(
            f"{label} candidate_id {actual['candidate_id']!r} does not match JSON",
            category="artifact_write",
        )
    for key in NUMERIC_RECONCILE_COLUMNS:
        left = expected.get(key)
        right = actual[key]
        if _is_null(left) and _is_null(right):
            continue
        if _is_null(left) or _is_null(right):
            raise SweepExecutionError(
                f"{label} {key} {right!r} does not match JSON {left!r}",
                category="artifact_write",
            )
        if abs(float(left) - float(right)) > 0.0:
            if abs(float(left) - float(right)) > 1e-15 * max(1.0, abs(float(left))):
                raise SweepExecutionError(
                    f"{label} {key} {right!r} does not match JSON {left!r} at full precision",
                    category="artifact_write",
                )
    expected_flag = expected.get("payback_within_evaluation_period")
    actual_flag = actual["payback_within_evaluation_period"]
    if bool(expected_flag) != _as_bool(actual_flag):
        raise SweepExecutionError(
            f"{label} payback_within_evaluation_period {actual_flag!r} does not match JSON {expected_flag!r}",
            category="artifact_write",
        )


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _write_resolved_config(
    run_dir: Path,
    *,
    run: RevenueSweepRun,
    audit: dict[str, Any] | None,
) -> list[str]:
    payload = {
        "source": {
            "defaults_path": None if audit is None else audit.get("defaults_path"),
            "defaults_sha256": None if audit is None else audit.get("defaults_sha256"),
            "toml_path": None if audit is None else audit.get("toml_path"),
            "toml_sha256": None if audit is None else audit.get("toml_sha256"),
            "run_toml_path": None if audit is None else audit.get("run_toml_path"),
            "run_toml_sha256": None if audit is None else audit.get("run_toml_sha256"),
            "cli_overrides": [] if audit is None else audit.get("cli_overrides", []),
        },
        "value_sources": None if audit is None else audit.get("value_sources"),
        "resolved": {
            "output": {"directory": str(run_dir)},
            "battery_template": run.battery_template.to_dict(),
            "tariffs": run.tariffs.to_dict(),
            "sweep": run.sweep_config.to_dict(),
        },
    }
    if audit is not None and "resolved" in audit:
        payload["resolved"] = {**audit["resolved"], **payload["resolved"]}
    (run_dir / "resolved_config.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    written = ["resolved_config.json"]
    defaults_toml = None if audit is None else audit.get("defaults_path")
    if defaults_toml:
        defaults_file = Path(str(defaults_toml))
        if defaults_file.exists():
            shutil.copy2(defaults_file, run_dir / "source_defaults.toml")
            written.append("source_defaults.toml")
    source_toml = None if audit is None else audit.get("run_toml_path") or audit.get("toml_path")
    if source_toml:
        source_file = Path(str(source_toml))
        if source_file.exists():
            shutil.copy2(source_file, run_dir / "source_config.toml")
            written.append("source_config.toml")
    return written


def _write_summary_csv(table: pd.DataFrame, path: Path) -> None:
    work = table.copy()
    for column in work.columns:
        if pd.api.types.is_float_dtype(work[column]):
            work[column] = work[column].map(_format_float)
    work.to_csv(path, index=False)


def _format_float(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return format(float(value), ".16g")



def _reject_forbidden_outputs(run_dir: Path) -> None:
    present = {path.name for path in run_dir.iterdir() if path.is_file()}
    forbidden = sorted(present & FORBIDDEN_OUTPUT_NAMES)
    if forbidden:
        raise SweepExecutionError(
            "Sweep folder must not contain " + ", ".join(forbidden),
            category="artifact_write",
        )
    dispatch_like = sorted(
        name
        for name in present
        if name.endswith("_dispatch.csv") or name.endswith("_dispatch.parquet")
    )
    if dispatch_like:
        raise SweepExecutionError(
            "Sweep folder must not contain full dispatch traces: " + ", ".join(dispatch_like),
            category="artifact_write",
        )
