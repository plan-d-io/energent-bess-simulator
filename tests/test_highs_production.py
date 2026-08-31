"""Production HiGHS backend: public paths, packaging boundary, and orchestration."""

from __future__ import annotations

import builtins
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.config.schema import SweepConfig, TariffConfig
from btm_sim.optimizer import (
    optimize_dynamic_injection,
    optimize_peak_reduction,
    optimize_revenue,
    optimize_self_consumption,
)
from btm_sim.optimizer.backend import DEFAULT_OPTIMIZER_BACKEND, get_optimizer_backend
from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.sweep.candidates import SweepCandidate
from btm_sim.sweep.runner import run_revenue_sweep
from tests.lp_frames import qh_frame

pytest.importorskip("highspy")

ROOT = Path(__file__).resolve().parents[1]


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


def test_default_production_backend_is_highs():
    assert DEFAULT_OPTIMIZER_BACKEND == "highs"


@pytest.mark.parametrize(
    "optimize_fn",
    [
        optimize_self_consumption,
        optimize_peak_reduction,
        optimize_revenue,
    ],
)
def test_public_optimize_reports_highs(optimize_fn):
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0)
    if optimize_fn is optimize_revenue:
        result = optimize_fn(frame, cfg, TariffConfig())
    else:
        result = optimize_fn(frame, cfg)
    assert result.summary["solver"]["name"] == "HiGHS"
    assert result.summary["solver"]["production_backend"] is True
    assert result.summary["solver"].get("gurobipy_version") is None
    for step in result.summary["objective_steps"]:
        assert "solver_status" in step
        assert step["gurobi_status"] is None


def test_public_dynamic_injection_reports_highs():
    frame = qh_frame([{"imp": 0.0, "exp": 2.0, "pv": 2.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    prices = [50.0, 50.0]
    result = optimize_dynamic_injection(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0), prices)
    assert result.summary["solver"]["name"] == "HiGHS"
    assert result.summary["self_consumption_solver"]["name"] == "HiGHS"


def test_production_import_does_not_import_gurobipy(monkeypatch):
    real_import = builtins.__import__
    gurobi_imports: list[str] = []

    def tracking_import(name, *args, **kwargs):
        if name == "gurobipy" or name.startswith("gurobipy."):
            gurobi_imports.append(name)
            raise ImportError("gurobipy blocked for production import test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    import btm_sim.optimizer.self_consumption as sc

    importlib.reload(sc)
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    result = sc.optimize_self_consumption(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0))
    assert result.summary["solver"]["name"] == "HiGHS"
    assert gurobi_imports == []


def test_missing_highspy_blocks_highs_backend(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "highspy" or name.startswith("highspy."):
            raise ImportError("forced missing highspy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(OptimizerError, match="highspy"):
        get_optimizer_backend("highs")


@pytest.mark.skipif(not _gurobi_available(), reason="Gurobi package or licence is not available")
def test_explicit_gurobi_backend_still_available():
    gurobi = get_optimizer_backend("gurobi")
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    result = gurobi.optimize_self_consumption(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0))
    assert result.summary["solver"]["name"] == "Gurobi"
    assert result.summary["solver"]["production_backend"] is False


def test_production_sweep_uses_highs_by_default():
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
    ]
    sweep = run_revenue_sweep(frame, candidates, template, TariffConfig(), SweepConfig())
    assert sweep.rows[0]["solver_name"] == "HiGHS"
    assert sweep.rows[0]["continuous_lp"] is True


def test_historical_gurobi_objective_steps_fixture_unchanged():
    fixture = ROOT / "reference" / "optimizer" / "gurobi_self_consumption_summary_fixture.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert payload["solver"]["name"] == "Gurobi"
    for step in payload["objective_steps"]:
        assert step["gurobi_status"] is not None


def test_wheel_installs_highspy_only_and_solves_without_gurobi(tmp_path):
    dist = tmp_path / "dist"
    install_target = tmp_path / "site-packages"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "-w",
            str(dist),
            "--no-deps",
            "--no-build-isolation",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist.glob("btm_sim-*.whl"))
    assert len(wheels) == 1
    import zipfile

    with zipfile.ZipFile(wheels[0]) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith("METADATA"))
        metadata = zf.read(meta_name).decode("utf-8")
    assert "Requires-Dist: highspy" in metadata
    assert "Requires-Dist: gurobipy" not in metadata

    subprocess.run(
        [sys.executable, "-m", "pip", "install", str(wheels[0]), "--no-deps", "--target", str(install_target)],
        check=True,
        capture_output=True,
        text=True,
    )

    smoke = (
        "import os, sys\n"
        "class BlockGurobi:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if name == 'gurobipy' or name.startswith('gurobipy.') else None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError('gurobipy blocked')\n"
        "sys.meta_path.insert(0, BlockGurobi())\n"
        "import btm_sim\n"
        "from btm_sim.optimizer import optimize_self_consumption\n"
        "from btm_sim.battery.config import BatteryConfig\n"
        "import pandas as pd\n"
        f"assert os.path.commonpath([btm_sim.__file__, r'{install_target}']) == r'{install_target}'\n"
        "frame = pd.DataFrame({\n"
        "    'timestamp_utc': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T00:15:00Z'], utc=True),\n"
        "    'timestamp_local': pd.to_datetime(['2024-01-01T00:00:00', '2024-01-01T00:15:00']),\n"
        "    'interval_hours': [0.25, 0.25],\n"
        "    'grid_import_baseline_kwh': [0.0, 1.0],\n"
        "    'grid_export_baseline_kwh': [1.0, 0.0],\n"
        "    'pv_production_kwh': [1.0, 0.0],\n"
        "    'site_load_kwh': [0.0, 1.0],\n"
        "})\n"
        "run = optimize_self_consumption(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0))\n"
        "assert run.summary['solver']['name'] == 'HiGHS'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(install_target)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_no_gurobi_subprocess_smoke():
    code = (
        "import sys\n"
        "class BlockGurobi:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if name == 'gurobipy' or name.startswith('gurobipy.') else None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError('gurobipy blocked')\n"
        "sys.meta_path.insert(0, BlockGurobi())\n"
        "from btm_sim.optimizer import optimize_self_consumption\n"
        "from btm_sim.battery.config import BatteryConfig\n"
        "import pandas as pd\n"
        "frame = pd.DataFrame({\n"
        "    'timestamp_utc': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T00:15:00Z'], utc=True),\n"
        "    'timestamp_local': pd.to_datetime(['2024-01-01T00:00:00', '2024-01-01T00:15:00']),\n"
        "    'interval_hours': [0.25, 0.25],\n"
        "    'grid_import_baseline_kwh': [0.0, 1.0],\n"
        "    'grid_export_baseline_kwh': [1.0, 0.0],\n"
        "    'pv_production_kwh': [1.0, 0.0],\n"
        "    'site_load_kwh': [0.0, 1.0],\n"
        "})\n"
        "run = optimize_self_consumption(frame, BatteryConfig(10, 100, 100, 1.0, 1.0, 0.0))\n"
        "assert run.summary['solver']['name'] == 'HiGHS'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
