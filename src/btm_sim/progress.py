"""UI-independent progress events for end-to-end runs and the comparison runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, Sequence

Level = Literal["info", "warning", "error"]
EventState = Literal["started", "completed", "failed"]

REQUEST_SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 1

STAGE_READ_FLUVIUS = "read_fluvius"
STAGE_NORMALIZE_PERIOD = "normalize_period"
STAGE_CHECK_PRICES = "check_prices"
STAGE_RUN_REFERENCE = "run_reference"
STAGE_OPTIMIZE_SELF_CONSUMPTION = "optimize_self_consumption"
STAGE_OPTIMIZE_PEAK_REDUCTION = "optimize_peak_reduction"
STAGE_OPTIMIZE_REVENUE = "optimize_revenue"
STAGE_OPTIMIZE_DYNAMIC_INJECTION = "optimize_dynamic_injection"
STAGE_WRITE_ARTIFACTS = "write_artifacts"
STAGE_VERIFY_COMPLETE = "verify_complete"

STAGE_DEFAULT_MESSAGES: tuple[tuple[str, str], ...] = (
    (STAGE_READ_FLUVIUS, "Reading and checking the three Fluvius files"),
    (STAGE_NORMALIZE_PERIOD, "Preparing the selected period"),
    (STAGE_CHECK_PRICES, "Checking day-ahead injection prices"),
    (STAGE_RUN_REFERENCE, "Running rule-based control"),
    (STAGE_OPTIMIZE_SELF_CONSUMPTION, "Optimising self-consumption"),
    (STAGE_OPTIMIZE_PEAK_REDUCTION, "Optimising peak reduction"),
    (STAGE_OPTIMIZE_REVENUE, "Optimising revenue maximisation"),
    (STAGE_OPTIMIZE_DYNAMIC_INJECTION, "Optimising dynamic injection tariff"),
    (STAGE_WRITE_ARTIFACTS, "Writing result files"),
    (STAGE_VERIFY_COMPLETE, "Verifying and completing the run"),
)
STAGE_ORDER: tuple[str, ...] = tuple(key for key, _message in STAGE_DEFAULT_MESSAGES)
STAGE_TOTAL = len(STAGE_ORDER)
STAGE_INDEX = {key: index + 1 for index, key in enumerate(STAGE_ORDER)}
STAGE_MESSAGE = dict(STAGE_DEFAULT_MESSAGES)
COMPLETED_RUN_MESSAGE = "Run completed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime | None = None) -> str:
    value = utc_now() if moment is None else moment
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_interval_count(n_intervals: int) -> str:
    return f"{int(n_intervals):,}"


def prepare_period_message(label: str, n_intervals: int) -> str:
    text = str(label).strip() or "the selected period"
    if text and text[0].isupper():
        text = text[0].lower() + text[1:]
    return f"Preparing {text} ({format_interval_count(n_intervals)} quarter-hours)"


@dataclass(frozen=True)
class ProgressEvent:
    event_time_utc: str
    level: Level
    stage_key: str
    stage_number: int
    stage_total: int
    state: EventState
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_time_utc": self.event_time_utc,
            "level": self.level,
            "stage_key": self.stage_key,
            "stage_number": self.stage_number,
            "stage_total": self.stage_total,
            "state": self.state,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ProgressReporter(Protocol):
    def emit(self, event: ProgressEvent) -> None:
        """Handle one structured progress event."""


class NullProgress:
    def emit(self, event: ProgressEvent) -> None:
        return None


class CompositeProgress:
    def __init__(self, reporters: Sequence[ProgressReporter]):
        self.reporters = tuple(reporters)

    def emit(self, event: ProgressEvent) -> None:
        for reporter in self.reporters:
            reporter.emit(event)


class CallbackProgress:
    def __init__(self, callback):
        self.callback = callback

    def emit(self, event: ProgressEvent) -> None:
        self.callback(event)


class ConsoleProgress:
    """Print plain user-facing messages to stdout as they occur."""

    def __init__(self, stream=None):
        self.stream = stream

    def emit(self, event: ProgressEvent) -> None:
        stream = self.stream
        if stream is None:
            import sys

            stream = sys.stdout
        if event.state == "started" or event.level in {"warning", "error"}:
            print(event.message, file=stream, flush=True)
        elif event.state == "completed" and event.stage_key == STAGE_VERIFY_COMPLETE:
            print(event.message, file=stream, flush=True)


def make_event(
    stage_key: str,
    state: EventState,
    *,
    message: str | None = None,
    level: Level = "info",
    details: dict[str, Any] | None = None,
    event_time_utc: str | None = None,
) -> ProgressEvent:
    if stage_key not in STAGE_INDEX:
        raise KeyError(f"Unknown progress stage {stage_key!r}")
    text = message if message is not None else STAGE_MESSAGE[stage_key]
    if state == "completed" and stage_key == STAGE_VERIFY_COMPLETE and message is None:
        text = COMPLETED_RUN_MESSAGE
    return ProgressEvent(
        event_time_utc=event_time_utc or iso_utc(),
        level=level,
        stage_key=stage_key,
        stage_number=STAGE_INDEX[stage_key],
        stage_total=STAGE_TOTAL,
        state=state,
        message=text,
        details=dict(details or {}),
    )


def emit(
    reporter: ProgressReporter | None,
    stage_key: str,
    state: EventState,
    *,
    message: str | None = None,
    level: Level = "info",
    details: dict[str, Any] | None = None,
) -> ProgressEvent | None:
    if reporter is None:
        return None
    event = make_event(stage_key, state, message=message, level=level, details=details)
    reporter.emit(event)
    return event


class stage_scope:
    """Emit started/completed/failed around a named stage."""

    def __init__(
        self,
        reporter: ProgressReporter | None,
        stage_key: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.reporter = reporter
        self.stage_key = stage_key
        self.message = message
        self.details = details

    def __enter__(self) -> stage_scope:
        emit(self.reporter, self.stage_key, "started", message=self.message, details=self.details)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc is None:
            emit(self.reporter, self.stage_key, "completed", message=self.message, details=self.details)
            return None
        emit(
            self.reporter,
            self.stage_key,
            "failed",
            message=str(exc) or STAGE_MESSAGE[self.stage_key],
            level="error",
            details={"exception_type": type(exc).__name__},
        )
        return None
