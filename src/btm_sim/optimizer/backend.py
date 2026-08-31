"""Internal optimizer backend boundary for HiGHS production and Gurobi differentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from btm_sim.optimizer.exceptions import OptimizerError

BackendName = Literal["gurobi", "highs"]
DEFAULT_OPTIMIZER_BACKEND: BackendName = "highs"


@dataclass(frozen=True)
class OptimizerBackend:
    """Explicit bundle of the four optimized dispatch callables for one solver."""

    name: BackendName
    optimize_self_consumption: Callable[..., Any]
    optimize_peak_reduction: Callable[..., Any]
    optimize_revenue: Callable[..., Any]
    optimize_dynamic_injection: Callable[..., Any]


def get_production_backend() -> OptimizerBackend:
    """Return the sole production optimizer backend (HiGHS)."""
    return get_optimizer_backend(DEFAULT_OPTIMIZER_BACKEND)


def get_optimizer_backend(name: str) -> OptimizerBackend:
    """Resolve an explicit backend. Lazy-loads solver-specific dependencies."""
    key = str(name).strip().lower()
    if key == "gurobi":
        return _gurobi_backend()
    if key == "highs":
        return _highs_backend()
    raise OptimizerError(
        f"Unknown optimizer backend {name!r}; expected 'gurobi' or 'highs'",
        details={"backend": name},
    )


def _gurobi_backend() -> OptimizerBackend:
    from btm_sim.optimizer.gurobi_dynamic_injection import optimize_dynamic_injection_gurobi
    from btm_sim.optimizer.gurobi_peak_reduction import optimize_peak_reduction_gurobi
    from btm_sim.optimizer.gurobi_revenue import optimize_revenue_gurobi
    from btm_sim.optimizer.gurobi_self_consumption import optimize_self_consumption_gurobi

    return OptimizerBackend(
        name="gurobi",
        optimize_self_consumption=optimize_self_consumption_gurobi,
        optimize_peak_reduction=optimize_peak_reduction_gurobi,
        optimize_revenue=optimize_revenue_gurobi,
        optimize_dynamic_injection=optimize_dynamic_injection_gurobi,
    )


def _highs_backend() -> OptimizerBackend:
    from btm_sim.optimizer.highs_backend import import_highspy
    from btm_sim.optimizer.highs_dynamic_injection import optimize_dynamic_injection_highs
    from btm_sim.optimizer.highs_peak_reduction import optimize_peak_reduction_highs
    from btm_sim.optimizer.highs_revenue import optimize_revenue_highs
    from btm_sim.optimizer.highs_self_consumption import optimize_self_consumption_highs

    import_highspy()
    return OptimizerBackend(
        name="highs",
        optimize_self_consumption=optimize_self_consumption_highs,
        optimize_peak_reduction=optimize_peak_reduction_highs,
        optimize_revenue=optimize_revenue_highs,
        optimize_dynamic_injection=optimize_dynamic_injection_highs,
    )
