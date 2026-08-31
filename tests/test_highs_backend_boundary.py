"""Internal optimizer backend boundary and sweep-injection compatibility."""

from __future__ import annotations

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.config.schema import SweepConfig, TariffConfig, parse_hhmm
from btm_sim.optimizer import __all__ as OPTIMIZER_PUBLIC
from btm_sim.optimizer.backend import get_optimizer_backend
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.sweep.candidates import SweepCandidate
from btm_sim.sweep.runner import run_revenue_sweep
from tests.lp_frames import qh_frame

pytest.importorskip("highspy")


def _gurobi_license_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


def test_backend_resolves_gurobi_and_highs():
    gurobi = get_optimizer_backend("gurobi")
    highs = get_optimizer_backend("highs")
    assert gurobi.name == "gurobi"
    assert highs.name == "highs"
    assert gurobi.optimize_self_consumption is not highs.optimize_self_consumption


def test_unknown_backend_raises():
    with pytest.raises(OptimizerError, match="Unknown optimizer backend"):
        get_optimizer_backend("cbc")


def test_public_optimizer_exports_unchanged():
    assert "get_optimizer_backend" not in OPTIMIZER_PUBLIC
    assert "optimize_self_consumption_highs" not in OPTIMIZER_PUBLIC
    assert "optimize_peak_reduction_highs" not in OPTIMIZER_PUBLIC
    assert "optimize_revenue_highs" not in OPTIMIZER_PUBLIC
    assert "optimize_dynamic_injection_highs" not in OPTIMIZER_PUBLIC
    import btm_sim

    assert not hasattr(btm_sim, "get_optimizer_backend")


def test_missing_highspy_error_on_highs_backend(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "highspy" or name.startswith("highspy."):
            raise ImportError("forced missing highspy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(OptimizerError, match="highspy"):
        get_optimizer_backend("highs")


@pytest.mark.skipif(
    not _gurobi_license_available(),
    reason="Gurobi package or licence is not available",
)
def test_missing_highspy_does_not_break_explicit_gurobi_backend(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "highspy" or name.startswith("highspy."):
            raise ImportError("forced missing highspy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(OptimizerError, match="highspy"):
        get_optimizer_backend("highs")
    gurobi = get_optimizer_backend("gurobi")
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    result = gurobi.optimize_self_consumption(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0))
    assert result.ok
    assert result.summary["solver"]["name"] == "Gurobi"


def test_composite_highs_revenue_uses_highs_self_consumption():
    backend = get_optimizer_backend("highs")
    frame = qh_frame(
        [{"imp": 0.0, "exp": 2.0, "pv": 2.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}],
    )
    tariffs = TariffConfig(
        customer_sale_eur_per_mwh=130.0,
        peak_export_eur_per_mwh=80.0,
        offpeak_export_eur_per_mwh=20.0,
        peak_start_local=parse_hhmm("00:00", name="peak_start"),
        peak_end_local=parse_hhmm("23:59", name="peak_end"),
        weekends_offpeak=False,
    )
    result = backend.optimize_revenue(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0), tariffs)
    assert result.self_consumption is not None
    assert result.self_consumption.summary["solver"]["name"] == "HiGHS"
    assert result.summary["solver"]["name"] == "HiGHS"
    assert result.summary["self_consumption_solver"]["name"] == "HiGHS"


def test_small_in_memory_sweep_can_use_highs_revenue_callable():
    backend = get_optimizer_backend("highs")
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    template = BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0)
    candidates = [
        SweepCandidate("c001_4kW_2kWh", 4.0, 2.0, 0.5, False, True, "explicit"),
        SweepCandidate("c002_8kW_4kWh", 8.0, 4.0, 0.5, False, True, "explicit"),
    ]
    sweep = run_revenue_sweep(
        frame,
        candidates,
        template,
        TariffConfig(),
        SweepConfig(),
        optimize=backend.optimize_revenue,
    )
    assert len(sweep.rows) == 2
    assert {"candidate_id", "period_revenue_uplift_eur", "simple_payback_years"} <= set(sweep.rows[0])
    assert all(row["feasibility_ok"] for row in sweep.rows)
