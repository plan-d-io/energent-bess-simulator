"""Peak-reduction-first sequential LP (production: HiGHS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.reporting import write_peak_reduction_outputs


@dataclass
class PeakReductionRun:
    frame: pd.DataFrame
    summary: dict[str, Any]
    config: BatteryConfig
    stages: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("ok"))


def optimize_peak_reduction(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    output_dir: str | None = None,
    source_path=None,
    output_flag: int = 0,
) -> PeakReductionRun:
    """Solve the peak-reduction-first case and optionally write audit files."""
    from btm_sim.optimizer.backend import get_production_backend

    result = get_production_backend().optimize_peak_reduction(
        frame, config, output_flag=output_flag
    )
    if output_dir is not None:
        write_peak_reduction_outputs(
            result.frame, result.summary, output_dir, source_path=source_path
        )
    return result
