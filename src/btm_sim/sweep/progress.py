"""Sweep progress events. Does not reuse the six-case stage index."""

from __future__ import annotations

from typing import Any

from btm_sim.progress import (
    EventState,
    Level,
    ProgressEvent,
    ProgressReporter,
    iso_utc,
)

STAGE_READ_FLUVIUS = "read_fluvius"
STAGE_NORMALIZE_PERIOD = "normalize_period"
STAGE_ANALYSE_SITE = "analyse_site"
STAGE_TEST_CANDIDATE = "test_candidate"
STAGE_RECOMMEND = "recommend"
STAGE_WRITE_ARTIFACTS = "write_artifacts"

SWEEP_COMPLETED_MESSAGE = "Sweep completed"
READ_FLUVIUS_MESSAGE = "Reading and checking the three Fluvius files"
ANALYSE_SITE_MESSAGE = "Analysing the site and preparing battery sizes"
RECOMMEND_MESSAGE = "Calculating sizing recommendations"
WRITE_ARTIFACTS_MESSAGE = "Writing result files"


def sweep_stage_total(n_candidates: int) -> int:
    return 5 + int(n_candidates)


def candidate_stage_number(index_1based: int) -> int:
    return 3 + int(index_1based)


def recommend_stage_number(n_candidates: int) -> int:
    return 4 + int(n_candidates)


def write_artifacts_stage_number(n_candidates: int) -> int:
    return 5 + int(n_candidates)


def emit_sweep(
    reporter: ProgressReporter | None,
    stage_key: str,
    state: EventState,
    *,
    stage_number: int,
    stage_total: int,
    message: str,
    level: Level = "info",
    details: dict[str, Any] | None = None,
) -> ProgressEvent | None:
    event = ProgressEvent(
        event_time_utc=iso_utc(),
        level=level,
        stage_key=stage_key,
        stage_number=int(stage_number),
        stage_total=int(stage_total),
        state=state,
        message=message,
        details=dict(details or {}),
    )
    if reporter is None:
        return None
    reporter.emit(event)
    return event


class sweep_stage_scope:
    """Emit started/completed/failed around one sweep stage."""

    def __init__(
        self,
        reporter: ProgressReporter | None,
        stage_key: str,
        *,
        stage_number: int,
        stage_total: int,
        message: str,
        details: dict[str, Any] | None = None,
        completed_message: str | None = None,
    ) -> None:
        self.reporter = reporter
        self.stage_key = stage_key
        self.stage_number = stage_number
        self.stage_total = stage_total
        self.message = message
        self.details = details
        self.completed_message = completed_message

    def __enter__(self) -> sweep_stage_scope:
        emit_sweep(
            self.reporter,
            self.stage_key,
            "started",
            stage_number=self.stage_number,
            stage_total=self.stage_total,
            message=self.message,
            details=self.details,
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            emit_sweep(
                self.reporter,
                self.stage_key,
                "failed",
                stage_number=self.stage_number,
                stage_total=self.stage_total,
                message=str(exc) if exc is not None else self.message,
                level="error",
                details=self.details,
            )
            return None
        text = self.completed_message if self.completed_message is not None else self.message
        emit_sweep(
            self.reporter,
            self.stage_key,
            "completed",
            stage_number=self.stage_number,
            stage_total=self.stage_total,
            message=text,
            details=self.details,
        )
        return None


class SweepConsoleProgress:
    """Print started messages and the final Sweep completed line."""

    def __init__(self, stream=None):
        self.stream = stream

    def emit(self, event: ProgressEvent) -> None:
        stream = self.stream
        if stream is None:
            import sys

            stream = sys.stdout
        if event.state == "started" or event.level in {"warning", "error"}:
            print(event.message, file=stream, flush=True)
        elif event.state == "completed" and event.stage_key == STAGE_WRITE_ARTIFACTS:
            print(event.message, file=stream, flush=True)


def candidate_message(index_1based: int, count: int, size_label: str) -> str:
    return f"Testing candidate {index_1based} of {count}: {size_label}"
