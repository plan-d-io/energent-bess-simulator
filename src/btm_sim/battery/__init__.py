"""Library API for battery physics and the diagnostic reference controller."""

from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.battery.controller import attach_reference_dispatch, reference_actions
from btm_sim.battery.dispatch import ReferenceRun, run_reference_controller, write_reference_outputs
from btm_sim.battery.physics import apply_step, check_dispatch_feasibility

__all__ = [
    "BatteryConfig",
    "BatteryConfigError",
    "ReferenceRun",
    "apply_step",
    "attach_reference_dispatch",
    "check_dispatch_feasibility",
    "reference_actions",
    "run_reference_controller",
    "write_reference_outputs",
]
