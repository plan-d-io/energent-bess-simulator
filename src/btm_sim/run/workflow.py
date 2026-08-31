"""Synchronous end-to-end Fluvius-to-comparison workflow."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from btm_sim.compare.artifacts import ARTIFACT_SCHEMA_VERSION
from btm_sim.compare.exceptions import ComparisonError
from btm_sim.compare.metrics import SCENARIO_ORDER
from btm_sim.compare.runner import ComparisonRun, run_comparison
from btm_sim.config.exceptions import ConfigError
from btm_sim.fluvius.pipeline import ingest_fluvius, materialize_period, write_run_outputs
from btm_sim.market.prices import PriceDataError
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.progress import (
    STAGE_NORMALIZE_PERIOD,
    STAGE_READ_FLUVIUS,
    CompositeProgress,
    ProgressReporter,
    emit,
    prepare_period_message,
    stage_scope,
)
from btm_sim.run.exceptions import RunError, RunExecutionError, RunRequestError
from btm_sim.run.request import EndToEndRunRequest, validate_frozen_inputs, write_run_request
from btm_sim.run.status import JobFileProgress, JobSession, REQUEST_FILENAME

REQUIRED_FINAL_FILES = (
    "normalized_input.parquet",
    "validation_report.json",
    "comparison_summary.json",
    "comparison_summary.csv",
    "monthly_summary.csv",
    "monthly_peaks.csv",
    "comparison_dispatch.csv",
    "comparison_dispatch.parquet",
    "run_metadata.json",
    "run_request.json",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "dynamic_injection_prices.parquet",
)


@dataclass
class EndToEndRun:
    directory: Path
    request: EndToEndRunRequest
    comparison: ComparisonRun | None
    status: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status.get("state") == "completed"


class _StdoutLogTee:
    """Copy stdout (including optional HiGHS console output) into the run log."""

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


def run_end_to_end(
    request: EndToEndRunRequest,
    *,
    progress: ProgressReporter | None = None,
    console: bool = False,
    session: JobSession | None = None,
) -> EndToEndRun:
    """Run Fluvius ingestion through the six-case comparison into ``request.output_dir``."""
    owns_session = session is None
    job = session or JobSession.create(request.output_dir, request.job_id)
    reporters: list[ProgressReporter] = [JobFileProgress(job)]
    if console:
        from btm_sim.progress import ConsoleProgress

        reporters.append(ConsoleProgress())
    if progress is not None:
        reporters.append(progress)
    reporter: ProgressReporter = CompositeProgress(reporters)
    comparison: ComparisonRun | None = None
    original_stdout = sys.stdout
    try:
        write_run_request(request, job.output_dir / REQUEST_FILENAME)
        validate_frozen_inputs(request)
        _run_pipeline(request, reporter, job)
        comparison = _comparison_from_dir(request, reporter, job)
        _verify_complete(request, comparison, reporter)
        job.complete(artifact_schema_version=ARTIFACT_SCHEMA_VERSION)
        return EndToEndRun(
            directory=job.output_dir,
            request=request,
            comparison=comparison,
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


def _run_pipeline(request: EndToEndRunRequest, reporter: ProgressReporter, job: JobSession) -> None:
    from btm_sim.fluvius.periods import resolve_period_id

    with stage_scope(reporter, STAGE_READ_FLUVIUS):
        ingest = ingest_fluvius(
            request.fluvius_paths(),
            allow_unvalidated=request.allow_unvalidated,
            acknowledge_site_boundary=request.acknowledge_site_boundary,
        )
        for warning in ingest.issues.warnings:
            emit(
                reporter,
                STAGE_READ_FLUVIUS,
                "started",
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
    with stage_scope(reporter, STAGE_NORMALIZE_PERIOD, message=period_message):
        result = materialize_period(
            ingest,
            request.period_id,
            allow_unvalidated=request.allow_unvalidated,
            acknowledge_site_boundary=request.acknowledge_site_boundary,
        )
        if not result.ok:
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


def _comparison_from_dir(
    request: EndToEndRunRequest,
    reporter: ProgressReporter,
    job: JobSession,
) -> ComparisonRun:
    import pandas as pd

    parquet_path = request.output_dir / "normalized_input.parquet"
    report_path = request.output_dir / "validation_report.json"
    frame = pd.read_parquet(parquet_path)
    output_flag = 1 if request.detailed_solver_output else 0
    tee: _StdoutLogTee | None = None
    if request.detailed_solver_output:
        tee = _StdoutLogTee(sys.stdout, job)
        sys.stdout = tee  # type: ignore[assignment]
        job.write_log("Detailed HiGHS solver console output is enabled")
    try:
        return run_comparison(
            frame,
            request.battery,
            tariffs=request.tariffs,
            reporting=request.reporting,
            output_dir=request.output_dir,
            source_path=parquet_path,
            validation_report=report_path,
            create_plots=request.reporting.seasonal_plots,
            config_audit=request.config_audit,
            toml_path=request.run_toml_path,
            dynamic_injection_prices=request.dynamic_injection_prices,
            progress=reporter,
            output_flag=output_flag,
            economics=request.economics,
        )
    except PriceDataError as exc:
        raise RunRequestError(str(exc), category="price_coverage") from exc
    except ComparisonError as exc:
        text = str(exc)
        if "day-ahead" in text.lower() or "price" in text.lower():
            raise RunRequestError(text, category="price_coverage") from exc
        raise RunExecutionError(text, category="optimizer") from exc
    except OptimizerError as exc:
        raise RunExecutionError(str(exc), category="optimizer") from exc
    finally:
        if tee is not None:
            tee.flush()
            sys.stdout = tee.original


def _verify_complete(
    request: EndToEndRunRequest,
    comparison: ComparisonRun,
    reporter: ProgressReporter,
) -> None:
    from btm_sim.progress import STAGE_VERIFY_COMPLETE

    with stage_scope(reporter, STAGE_VERIFY_COMPLETE):
        missing = [name for name in REQUIRED_FINAL_FILES if not (request.output_dir / name).exists()]
        if missing:
            raise RunExecutionError(
                "Run folder is incomplete; missing: " + ", ".join(missing),
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
            raise RunExecutionError(
                "Raw Fluvius files must not be copied into the run folder: " + ", ".join(copied),
                category="artifact_write",
            )
        parquets = list(request.output_dir.glob("normalized_input*.parquet"))
        if len(parquets) != 1:
            raise RunExecutionError(
                f"Expected one normalized_input.parquet, found {len(parquets)}",
                category="artifact_write",
            )
        summary = comparison.summary
        if summary.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise RunExecutionError("artifact_schema_version is not 2", category="artifact_write")
        for name in SCENARIO_ORDER:
            if name not in summary.get("scenarios", {}):
                raise RunExecutionError(f"Missing scenario {name}", category="artifact_write")
        if list(summary.get("scenario_order") or []) != list(SCENARIO_ORDER):
            raise RunExecutionError("scenario_order does not match the six-case contract", category="artifact_write")
        if not comparison.ok:
            raise RunExecutionError("Comparison summary is not ok", category="artifact_write")


def _map_exception(exc: BaseException) -> RunError:
    if isinstance(exc, RunError):
        return exc
    if isinstance(exc, ConfigError):
        return RunRequestError(str(exc), category="invalid_configuration")
    if isinstance(exc, PriceDataError):
        return RunRequestError(str(exc), category="price_coverage")
    if isinstance(exc, OptimizerError):
        return RunExecutionError(str(exc), category="optimizer")
    if isinstance(exc, ComparisonError):
        text = str(exc)
        if "day-ahead" in text.lower() or "price" in text.lower():
            return RunRequestError(text, category="price_coverage")
        return RunExecutionError(text, category="optimizer")
    return RunExecutionError(str(exc) or type(exc).__name__, category="execution")
