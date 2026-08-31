"""HiGHS detailed logging lifecycle and temp-file cleanup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.optimizer.self_consumption import optimize_self_consumption
from tests.lp_frames import qh_frame

pytest.importorskip("highspy")


def _btm_highs_logs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("btm_highs_*.log"))


def test_detailed_highs_logging_leaves_no_temp_files_on_success(capsys):
    before = _btm_highs_logs()
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    result = optimize_self_consumption(
        frame,
        BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0),
        output_flag=1,
    )
    captured = capsys.readouterr()
    assert result.summary["solver"]["name"] == "HiGHS"
    assert "HiGHS" in captured.out
    assert "log_file" not in result.summary["solver"].get("options", {})
    assert _btm_highs_logs() == before


def test_detailed_highs_logging_leaves_no_temp_files_on_failure(monkeypatch):
    before = _btm_highs_logs()
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    from btm_sim.optimizer.exceptions import OptimizerError

    def fail_stage(lp, *, stage):
        raise OptimizerError(f"forced failure at {stage}", status="INFEASIBLE", stage=stage)

    monkeypatch.setattr(
        "btm_sim.optimizer.highs_self_consumption.optimize_highs_stage",
        fail_stage,
    )
    with pytest.raises(OptimizerError, match="forced failure"):
        optimize_self_consumption(
            frame,
            BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0),
            output_flag=1,
        )
    assert _btm_highs_logs() == before
