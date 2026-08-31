"""Sweep-specific failures. CLI exit codes match the end-to-end run command."""

from __future__ import annotations

from btm_sim.run.exceptions import RunError, RunExecutionError, RunRequestError

SweepError = RunError
SweepRequestError = RunRequestError
SweepExecutionError = RunExecutionError

__all__ = [
    "SweepError",
    "SweepExecutionError",
    "SweepRequestError",
]
