"""Fixed-tariff Revenue maximisation (production: HiGHS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.config.schema import TariffConfig
from btm_sim.optimizer.reporting import write_revenue_outputs
from btm_sim.optimizer.self_consumption import SelfConsumptionRun


@dataclass
class RevenueRun:
    frame: pd.DataFrame
    summary: dict[str, Any]
    config: BatteryConfig
    tariffs: TariffConfig
    stages: list[dict[str, Any]]
    self_consumption: SelfConsumptionRun | None = None

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))


def optimize_revenue(
    frame: pd.DataFrame,
    config: BatteryConfig,
    tariffs: TariffConfig | None = None,
    *,
    output_dir: str | None = None,
    source_path=None,
    output_flag: int = 0,
    customer_first: SelfConsumptionRun | None = None,
) -> RevenueRun:
    """Preserve customer-first dispatch, then value remaining flexibility at the fixed tariff."""
    from btm_sim.optimizer.backend import get_production_backend

    backend = get_production_backend()
    result = backend.optimize_revenue(
        frame,
        config,
        tariffs,
        output_flag=output_flag,
        customer_first=customer_first,
    )
    if output_dir is not None:
        write_revenue_outputs(result.frame, result.summary, output_dir, source_path=source_path)
    return result
