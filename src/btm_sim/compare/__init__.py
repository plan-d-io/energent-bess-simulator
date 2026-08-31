"""Unified comparison of baseline, reference, and best-case dispatch cases."""

from btm_sim.compare.exceptions import ComparisonError
from btm_sim.compare.runner import ComparisonRun, run_comparison, run_comparison_from_resolved

__all__ = ["ComparisonError", "ComparisonRun", "run_comparison", "run_comparison_from_resolved"]
