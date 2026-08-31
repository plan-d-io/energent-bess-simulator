"""Sequential revenue-maximisation sweep over a frozen candidate list."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.cycles import selected_period_year_fraction
from btm_sim.compare.metrics import attach_baseline_dispatch, scenario_metrics
from btm_sim.compare.period import describe_selected_period
from btm_sim.config.schema import SweepConfig, TariffConfig
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.revenue import optimize_revenue
from btm_sim.progress import ProgressReporter
from btm_sim.sweep.candidates import SweepCandidate
from btm_sim.sweep.economics import (
    annualized_from_partial_period,
    attach_economics,
    explanations,
    recommend,
)
from btm_sim.sweep.peaks import baseline_peak_fields_from_metrics, candidate_peak_fields_from_metrics
from btm_sim.sweep.exceptions import SweepExecutionError, SweepRequestError
from btm_sim.sweep.progress import (
    STAGE_TEST_CANDIDATE,
    candidate_message,
    candidate_stage_number,
    emit_sweep,
    sweep_stage_total,
)

OptimizeRevenue = Callable[..., Any]


@dataclass
class RevenueSweepRun:
    frame: pd.DataFrame
    candidates: tuple[SweepCandidate, ...]
    rows: list[dict[str, Any]]
    baseline: dict[str, Any]
    recommendation: dict[str, Any]
    period: dict[str, Any]
    year_fraction: float
    annualized_from_partial_period: bool
    explanations: dict[str, Any]
    sweep_config: SweepConfig
    battery_template: BatteryConfig
    tariffs: TariffConfig
    failed_candidate_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failed_candidate_id is None


def candidate_battery(template: BatteryConfig, candidate: SweepCandidate) -> BatteryConfig:
    return BatteryConfig.with_symmetric_power(
        e_usable_kwh=candidate.usable_energy_kwh,
        power_kw=candidate.power_kw,
        eta_charge=template.eta_charge,
        eta_discharge=template.eta_discharge,
        soc_initial_kwh=0.0,
        max_equivalent_full_cycles_per_year=template.max_equivalent_full_cycles_per_year,
    )


def run_revenue_sweep(
    frame: pd.DataFrame,
    candidates: list[SweepCandidate] | tuple[SweepCandidate, ...],
    battery_template: BatteryConfig,
    tariffs: TariffConfig,
    sweep_config: SweepConfig,
    *,
    output_flag: int = 0,
    progress: ProgressReporter | None = None,
    stage_total: int | None = None,
    optimize: OptimizeRevenue | None = None,
) -> RevenueSweepRun:
    """Solve the no-battery baseline once, then one revenue LP per candidate."""
    if not candidates:
        raise SweepRequestError("run_revenue_sweep requires at least one candidate")
    work = frame.sort_values("timestamp_utc").reset_index(drop=True)
    year_fraction = selected_period_year_fraction(work)
    if year_fraction <= 0:
        raise SweepRequestError(
            "selected_period_year_fraction must be positive to annualise sweep revenue",
            category="invalid_period",
        )
    period = describe_selected_period(work)
    partial = annualized_from_partial_period(year_fraction)
    notes = explanations(partial_period=partial)
    baseline_frame = attach_baseline_dispatch(work, battery_template)
    baseline_metrics = scenario_metrics(baseline_frame, battery_template, scenario="no_battery", tariffs=tariffs)
    baseline_revenue = float(baseline_metrics["revenue"]["total_energent_pv_revenue_eur"])
    baseline = {
        "total_energent_pv_revenue_eur": baseline_revenue,
        "useful_pv_delivered_kwh": baseline_metrics["useful_pv_delivered_kwh"],
        "grid_import_kwh": baseline_metrics["grid_import_kwh"],
        "grid_export_kwh": baseline_metrics["grid_export_kwh"],
        "selected_period_year_fraction": year_fraction,
        **baseline_peak_fields_from_metrics(baseline_metrics),
    }
    solve = optimize_revenue if optimize is None else optimize
    rows: list[dict[str, Any]] = []
    n_candidates = len(candidates)
    total = sweep_stage_total(n_candidates) if stage_total is None else int(stage_total)
    for index, candidate in enumerate(candidates, start=1):
        details = {
            "candidate_index": index,
            "candidate_count": n_candidates,
            "candidate_id": candidate.candidate_id,
            "power_kw": candidate.power_kw,
            "usable_energy_kwh": candidate.usable_energy_kwh,
            "duration_hours": candidate.duration_hours,
        }
        emit_sweep(
            progress,
            STAGE_TEST_CANDIDATE,
            "started",
            stage_number=candidate_stage_number(index),
            stage_total=total,
            message=candidate_message(index, n_candidates, candidate.size_label()),
            details=details,
        )
        config = candidate_battery(battery_template, candidate)
        try:
            result = solve(work, config, tariffs, output_flag=output_flag)
            row = _candidate_row(
                candidate=candidate,
                result=result,
                config=config,
                tariffs=tariffs,
                baseline_revenue=baseline_revenue,
                year_fraction=year_fraction,
                sweep_config=sweep_config,
            )
            del result
        except Exception as exc:
            emit_sweep(
                progress,
                STAGE_TEST_CANDIDATE,
                "failed",
                stage_number=candidate_stage_number(index),
                stage_total=total,
                message=_candidate_failure_message(candidate, exc),
                level="error",
                details=details,
            )
            if isinstance(exc, OptimizerError):
                raise SweepExecutionError(
                    _candidate_failure_message(candidate, exc),
                    category="optimizer",
                ) from exc
            raise SweepExecutionError(
                _candidate_failure_message(candidate, exc),
                category="optimizer",
            ) from exc
        rows.append(row)
        emit_sweep(
            progress,
            STAGE_TEST_CANDIDATE,
            "completed",
            stage_number=candidate_stage_number(index),
            stage_total=total,
            message=candidate_message(index, n_candidates, candidate.size_label()),
            details=details,
        )
    choice = recommend(
        rows,
        revenue_capture_threshold_pct=sweep_config.revenue_capture_threshold_pct,
        evaluation_period_years=sweep_config.evaluation_period_years,
    )
    return RevenueSweepRun(
        frame=work,
        candidates=tuple(candidates),
        rows=rows,
        baseline=baseline,
        recommendation=choice,
        period=period,
        year_fraction=year_fraction,
        annualized_from_partial_period=partial,
        explanations=notes,
        sweep_config=sweep_config,
        battery_template=battery_template,
        tariffs=tariffs,
    )


def _candidate_row(
    *,
    candidate: SweepCandidate,
    result: Any,
    config: BatteryConfig,
    tariffs: TariffConfig,
    baseline_revenue: float,
    year_fraction: float,
    sweep_config: SweepConfig,
) -> dict[str, Any]:
    metrics = scenario_metrics(result.frame, config, scenario="revenue", tariffs=tariffs)
    revenue = float(metrics["revenue"]["total_energent_pv_revenue_eur"])
    economics = attach_economics(
        usable_energy_kwh=candidate.usable_energy_kwh,
        candidate_revenue_eur=revenue,
        baseline_revenue_eur=baseline_revenue,
        year_fraction=year_fraction,
        cost_eur_per_kwh=sweep_config.estimated_battery_cost_eur_per_kwh,
        evaluation_period_years=sweep_config.evaluation_period_years,
    )
    solver = dict(result.summary.get("solver") or {})
    feasibility = dict(result.summary.get("feasibility") or {})
    if int(solver.get("num_int_vars") or 0) != 0 or int(solver.get("num_bin_vars") or 0) != 0:
        raise SweepExecutionError(
            f"Candidate {candidate.candidate_id} is not a continuous LP",
            category="optimizer",
        )
    if not feasibility.get("ok", True):
        raise SweepExecutionError(
            f"Candidate {candidate.candidate_id} failed dispatch-feasibility checks",
            category="optimizer",
        )
    return {
        "candidate_id": candidate.candidate_id,
        "duration_hours": candidate.duration_hours,
        "power_kw": candidate.power_kw,
        "usable_energy_kwh": candidate.usable_energy_kwh,
        "estimated_capex_eur": economics["estimated_capex_eur"],
        "period_revenue_uplift_eur": economics["period_revenue_uplift_eur"],
        "annual_revenue_uplift_eur": economics["annual_revenue_uplift_eur"],
        "total_energent_pv_revenue_eur": revenue,
        "simple_payback_years": economics["simple_payback_years"],
        "estimated_value_eur": economics["estimated_value_eur"],
        "payback_within_evaluation_period": economics["payback_within_evaluation_period"],
        "useful_pv_delivered_kwh": metrics["useful_pv_delivered_kwh"],
        "additional_useful_pv_kwh": metrics["additional_useful_pv_kwh"],
        "grid_import_kwh": metrics["grid_import_kwh"],
        "grid_export_kwh": metrics["grid_export_kwh"],
        **candidate_peak_fields_from_metrics(metrics),
        "charge_pv_kwh": metrics["charge_pv_kwh"],
        "discharge_load_kwh": metrics["discharge_load_kwh"],
        "total_loss_kwh": metrics["total_loss_kwh"],
        "stored_throughput_kwh": metrics["stored_throughput_kwh"],
        "equivalent_full_cycles": metrics["equivalent_full_cycles"],
        "allowed_equivalent_full_cycles": metrics["allowed_equivalent_full_cycles"],
        "remaining_equivalent_full_cycles_allowance": metrics["remaining_equivalent_full_cycles_allowance"],
        "cycle_limit_binding": bool(metrics["cycle_limit_binding"]),
        "soc_initial_kwh": metrics["soc_initial_kwh"],
        "soc_final_kwh": metrics["soc_final_kwh"],
        "solver_status": solver.get("status"),
        "solver_name": solver.get("name"),
        "solver_highspy_version": solver.get("highspy_version"),
        "solver_highs_version": solver.get("highs_version"),
        "solver_runtime_s": solver.get("runtime_s"),
        "solver_num_vars": solver.get("num_vars"),
        "solver_num_constrs": solver.get("num_constrs"),
        "solver_num_int_vars": solver.get("num_int_vars"),
        "solver_num_bin_vars": solver.get("num_bin_vars"),
        "continuous_lp": bool(solver.get("continuous_lp", True)),
        "feasibility_ok": bool(feasibility.get("ok", True)),
        "exceeds_p95_daily_pv_surplus": candidate.exceeds_p95_daily_pv_surplus,
        "exceeds_p95_daily_import": candidate.exceeds_p95_daily_import,
    }


def _candidate_failure_message(candidate: SweepCandidate, exc: BaseException) -> str:
    return f"Candidate {candidate.candidate_id} ({candidate.size_label()}) failed: {exc}"
