"""Best-case self-consumption, peak-reduction, and revenue linear programs."""

from btm_sim.optimizer.dynamic_injection import DynamicInjectionRun, optimize_dynamic_injection
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.peak_reduction import PeakReductionRun, optimize_peak_reduction
from btm_sim.optimizer.revenue import RevenueRun, optimize_revenue
from btm_sim.optimizer.self_consumption import SelfConsumptionRun, optimize_self_consumption

__all__ = [
    "DynamicInjectionRun",
    "OptimizerError",
    "PeakReductionRun",
    "RevenueRun",
    "SelfConsumptionRun",
    "optimize_dynamic_injection",
    "optimize_peak_reduction",
    "optimize_revenue",
    "optimize_self_consumption",
]
