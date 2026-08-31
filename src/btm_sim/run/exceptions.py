"""End-to-end run failures and documented process exit codes."""

from __future__ import annotations

from typing import Any, Sequence

EXIT_SUCCESS = 0
EXIT_EXECUTION = 1
EXIT_INVALID_REQUEST = 2


class RunError(RuntimeError):
    """Base class for the end-to-end command."""

    exit_code = EXIT_EXECUTION
    category = "execution"


class RunRequestError(RunError):
    """Invalid request, configuration, inputs, period, or price coverage."""

    exit_code = EXIT_INVALID_REQUEST
    category = "invalid_request"

    def __init__(
        self,
        message: str,
        *,
        category: str | None = None,
        issues: Sequence[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category
        self.issues = [dict(item) for item in (issues or ())]
        self.details = dict(details or {})


class RunExecutionError(RunError):
    """Solver, artifact-writing, or other execution failure after a valid request."""

    exit_code = EXIT_EXECUTION
    category = "execution"

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category
