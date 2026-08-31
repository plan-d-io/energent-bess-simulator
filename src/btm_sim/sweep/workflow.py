"""Synchronous Fluvius-to-sweep workflow."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from btm_sim.config.exceptions import ConfigError
from btm_sim.fluvius.pipeline import ingest_fluvius, materialize_period, write_run_outputs
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.progress import CompositeProgress, ProgressReporter, prepare_period_message
from btm_sim.run.exceptions import RunError, RunExecutionError, RunRequestError
from btm_sim.run.status import JobFileProgress, JobSession
from btm_sim.sweep.artifacts import (
    SWEEP_ARTIFACT_SCHEMA_VERSION,
    SWEEP_REQUEST_FILENAME,
    write_sweep_directory,
)
from btm_sim.sweep.exceptions import SweepError, SweepExecutionError
from btm_sim.sweep.progress import (
    ANALYSE_SITE_MESSAGE,
    READ_FLUVIUS_MESSAGE,
    RECOMMEND_MESSAGE,
    STAGE_ANALYSE_SITE,
    STAGE_NORMALIZE_PERIOD,
    STAGE_READ_FLUVIUS,
    STAGE_RECOMMEND,
    STAGE_WRITE_ARTIFACTS,
    SWEEP_COMPLETED_MESSAGE,
    WRITE_ARTIFACTS_MESSAGE,
    SweepConsoleProgress,
    emit_sweep,
    recommend_stage_number,
    sweep_stage_scope,
    sweep_stage_total,
    write_artifacts_stage_number,
)
from btm_sim.sweep.request import SweepRequest, validate_frozen_sweep_inputs, write_sweep_request
from btm_sim.sweep.runner import RevenueSweepRun, run_revenue_sweep
from btm_sim.sweep.site import analyse_site

REQUIRED_FINAL_FILES = (
    "normalized_input.parquet",
    "validation_report.json",
    "sweep_summary.json",
    "sweep_summary.csv",
    "sweep_summary.parquet",
    "site_analysis.json",
    "sweep_metadata.json",
    "sweep_request.json",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "resolved_config.json",
)


@dataclass
class SweepEndToEndRun:
    directory: Path
    request: SweepRequest
    sweep: RevenueSweepRun | None
    status: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status.get("state") == "completed"


class _StdoutLogTee:
    def __init__(self, original: TextIO, session: JobSession) -> None:
        self.original = original
        self.session = session
        self._buf = ""

    def write(self, data: str) -> int:
        written = self.original.write(data)
        self.original.flush()
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self.session.write_log(line)
        return written

    def flush(self) -> None:
        self.original.flush()
        if self._buf:
            self.session.write_log(self._buf)
            self._buf = ""

    def isatty(self) -> bool:
        return bool(getattr(self.original, "isatty", lambda: False)())


def run_sweep_end_to_end(
    request: SweepRequest,
    *,
    progress: ProgressReporter | None = None,
    console: bool = False,
    session: JobSession | None = None,
) -> SweepEndToEndRun:
    """Run Fluvius ingestion through the revenue sweep into ``request.output_dir``."""
    owns_session = session is None
    job = session or JobSession.create(request.output_dir, request.job_id)
    reporters: list[ProgressReporter] = [JobFileProgress(job)]
    if console:
        reporters.append(SweepConsoleProgress())
    if progress is not None:
        reporters.append(progress)
    reporter: ProgressReporter = CompositeProgress(reporters)
    sweep: RevenueSweepRun | None = None
    original_stdout = sys.stdout
    stage_total = sweep_stage_total(len(request.candidates))
    try:
        write_sweep_request(request, job.output_dir / SWEEP_REQUEST_FILENAME)
        validate_frozen_sweep_inputs(request)
        frame = _run_pipeline(request, reporter, job, stage_total)
        sweep = _sweep_from_frame(request, frame, reporter, job, stage_total)
        _write_and_verify(request, sweep, reporter, stage_total)
        job.complete(artifact_schema_version=SWEEP_ARTIFACT_SCHEMA_VERSION, message=SWEEP_COMPLETED_MESSAGE)
        return SweepEndToEndRun(
            directory=job.output_dir,
            request=request,
            sweep=sweep,
            status=job.snapshot(),
        )
    except Exception as exc:
        mapped = _map_exception(exc)
        job.write_exception(exc)
        job.fail(mapped.category, str(mapped))
        if owns_session:
            raise mapped from exc
        raise mapped from exc
    finally:
        if sys.stdout is not original_stdout:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            sys.stdout = original_stdout
        if owns_session:
            job.close()


def _run_pipeline(
    request: SweepRequest,
    reporter: ProgressReporter,
    job: JobSession,
    stage_total: int,
):
    from btm_sim.fluvius.periods import resolve_period_id

    with sweep_stage_scope(
        reporter,
        STAGE_READ_FLUVIUS,
        stage_number=1,
        stage_total=stage_total,
        message=READ_FLUVIUS_MESSAGE,
    ):
        ingest = ingest_fluvius(
            request.fluvius_paths(),
            allow_unvalidated=request.allow_unvalidated,
            acknowledge_site_boundary=request.acknowledge_site_boundary,
        )
        for warning in ingest.issues.warnings:
            emit_sweep(
                reporter,
                STAGE_READ_FLUVIUS,
                "started",
                stage_number=1,
                stage_total=stage_total,
                message=warning.message,
                level="warning",
                details={"code": warning.code, **warning.details},
            )
        if not ingest.ok:
            first = ingest.issues.fatals[0]
            raise RunRequestError(first.message, category="invalid_input")

    selected = resolve_period_id(ingest.periods, request.period_id)
    if selected is None:
        available = [offer.id for offer in ingest.periods]
        raise RunRequestError(
            f"Period {request.period_id!r} is not among the discovered periods: {available}",
            category="invalid_period",
        )
    period_message = prepare_period_message(selected.label, selected.n_intervals)
    with sweep_stage_scope(
        reporter,
        STAGE_NORMALIZE_PERIOD,
        stage_number=2,
        stage_total=stage_total,
        message=period_message,
    ):
        result = materialize_period(
            ingest,
            request.period_id,
            allow_unvalidated=request.allow_unvalidated,
            acknowledge_site_boundary=request.acknowledge_site_boundary,
        )
        if not result.ok or result.frame is None:
            first = result.issues.fatals[0] if result.issues.fatals else None
            code = None if first is None else first.code
            category = "invalid_period" if code == "UNKNOWN_PERIOD" else "invalid_input"
            text = first.message if first is not None else "Normalization failed"
            raise RunRequestError(text, category=category)
        write_run_outputs(result, request.output_dir)
        report_path = request.output_dir / "validation_report.json"
        parquet_path = request.output_dir / "normalized_input.parquet"
        if not parquet_path.exists() or not report_path.exists():
            raise RunExecutionError(
                "Normalization did not write normalized_input.parquet and validation_report.json",
                category="artifact_write",
            )
        job.write_log(
            f"Prepared {selected.label} ({selected.n_intervals} quarter-hours); "
            "raw Fluvius CSVs were not copied"
        )
        return result.frame


def _sweep_from_frame(
    request: SweepRequest,
    frame,
    reporter: ProgressReporter,
    job: JobSession,
    stage_total: int,
) -> RevenueSweepRun:
    with sweep_stage_scope(
        reporter,
        STAGE_ANALYSE_SITE,
        stage_number=3,
        stage_total=stage_total,
        message=ANALYSE_SITE_MESSAGE,
        details={"n_candidates": len(request.candidates), "mode": request.mode},
    ):
        live = analyse_site(frame, request.durations_hours)
        frozen_ref = request.site_analysis.reference_power_kw
        if frozen_ref is not None and live.reference_power_kw is not None:
            if abs(float(live.reference_power_kw) - float(frozen_ref)) > 1e-6:
                raise SweepExecutionError(
                    "Site analysis reference power changed after the request was frozen",
                    category="execution",
                )
    output_flag = 1 if request.detailed_solver_output else 0
    tee: _StdoutLogTee | None = None
    if request.detailed_solver_output:
        tee = _StdoutLogTee(sys.stdout, job)
        sys.stdout = tee  # type: ignore[assignment]
        job.write_log("Detailed HiGHS solver console output is enabled")
    try:
        sweep = run_revenue_sweep(
            frame,
            request.candidates,
            request.battery,
            request.tariffs,
            request.sweep,
            output_flag=output_flag,
            progress=reporter,
            stage_total=stage_total,
        )
    finally:
        if tee is not None:
            tee.flush()
            sys.stdout = tee.original
    with sweep_stage_scope(
        reporter,
        STAGE_RECOMMEND,
        stage_number=recommend_stage_number(len(request.candidates)),
        stage_total=stage_total,
        message=RECOMMEND_MESSAGE,
    ):
        if sweep.recommendation is None:
            raise SweepExecutionError("Sweep did not produce a recommendation", category="execution")
    return sweep


def _write_and_verify(
    request: SweepRequest,
    sweep: RevenueSweepRun,
    reporter: ProgressReporter,
    stage_total: int,
) -> None:
    n_candidates = len(request.candidates)
    with sweep_stage_scope(
        reporter,
        STAGE_WRITE_ARTIFACTS,
        stage_number=write_artifacts_stage_number(n_candidates),
        stage_total=stage_total,
        message=WRITE_ARTIFACTS_MESSAGE,
        completed_message=SWEEP_COMPLETED_MESSAGE,
    ):
        write_sweep_directory(
            run_dir=request.output_dir,
            run=sweep,
            site=request.site_analysis,
            request_payload={
                "software_version": request.software_version,
                "job_id": request.job_id,
                "period_id": request.period_id,
            },
            config_audit=request.config_audit,
        )
        missing = [name for name in REQUIRED_FINAL_FILES if not (request.output_dir / name).exists()]
        if missing:
            raise SweepExecutionError(
                "Sweep folder is incomplete; missing: " + ", ".join(missing),
                category="artifact_write",
            )
        csv_names = {
            path.name
            for path in request.output_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".csv"
        }
        raw_names = {item.original_name for item in request.fluvius_inputs}
        copied = sorted(csv_names & raw_names)
        if copied:
            raise SweepExecutionError(
                "Raw Fluvius files must not be copied into the sweep folder: " + ", ".join(copied),
                category="artifact_write",
            )
        parquets = list(request.output_dir.glob("normalized_input*.parquet"))
        if len(parquets) != 1:
            raise SweepExecutionError(
                f"Expected one normalized_input.parquet, found {len(parquets)}",
                category="artifact_write",
            )
        if (request.output_dir / "run_request.json").exists():
            raise SweepExecutionError(
                "Sweep folder must write sweep_request.json, not run_request.json",
                category="artifact_write",
            )


def _map_exception(exc: BaseException) -> RunError:
    if isinstance(exc, RunError):
        return exc
    if isinstance(exc, SweepError):
        return exc
    if isinstance(exc, ConfigError):
        return RunRequestError(str(exc), category="invalid_configuration")
    if isinstance(exc, OptimizerError):
        return RunExecutionError(str(exc), category="optimizer")
    return RunExecutionError(str(exc) or type(exc).__name__, category="execution")
