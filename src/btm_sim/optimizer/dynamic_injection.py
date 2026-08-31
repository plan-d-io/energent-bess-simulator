"""Customer-first dynamic injection LP (production: HiGHS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.self_consumption import SelfConsumptionRun


@dataclass
class DynamicInjectionRun:
    frame: pd.DataFrame
    summary: dict[str, Any]
    config: BatteryConfig
    stages: list[dict[str, Any]]
    self_consumption: SelfConsumptionRun

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))


def optimize_dynamic_injection(
    frame: pd.DataFrame,
    config: BatteryConfig,
    prices_eur_mwh: np.ndarray | pd.Series,
    *,
    tariffs=None,
    output_flag: int = 0,
    customer_first: SelfConsumptionRun | None = None,
) -> DynamicInjectionRun:
    """Preserve customer-first dispatch, then value remaining flexibility at DA prices."""
    from btm_sim.optimizer.backend import get_production_backend

    return get_production_backend().optimize_dynamic_injection(
        frame,
        config,
        prices_eur_mwh,
        tariffs=tariffs,
        output_flag=output_flag,
        customer_first=customer_first,
    )
