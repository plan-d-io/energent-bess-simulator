"""Optimizer failures: missing Gurobi, licence, or non-optimal solves."""

from __future__ import annotations

from typing import Any


class OptimizerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.stage = stage
        self.details = details or {}
