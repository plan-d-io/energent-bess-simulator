"""Annual equivalent-full-cycle limit: config, proration, LP, and controller."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig, BatteryConfigError
from btm_sim.battery.cycles import (
    allowed_stored_throughput_kwh,
    calendar_year_physical_hours,
    cycle_limit_report,
    selected_period_year_fraction,
)
from btm_sim.battery.dispatch import run_reference_controller
from btm_sim.config.defaults import load_central_defaults
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_simulation_config
from btm_sim.fluvius.constants import INTERVAL_HOURS
from btm_sim.optimizer.self_consumption import optimize_self_consumption
from tests.lp_frames import qh_frame

UNCONSTRAINED = 1_000_000.0
PROJECT_DEFAULTS = Path(__file__).resolve().parents[1] / "configs" / "defaults.toml"


def _write_parquet(path: Path) -> Path:
    qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}]).to_parquet(
        path, index=False
    )
    return path


def test_central_defaults_require_and_expose_400_cycles():
    loaded = load_central_defaults()
    assert loaded.battery.max_equivalent_full_cycles_per_year == 400.0
    assert loaded.payload()["battery"]["max_equivalent_full_cycles_per_year"] == 400.0


def test_missing_cycle_limit_in_central_defaults_fails(tmp_path: Path):
    text = PROJECT_DEFAULTS.read_text(encoding="utf-8")
    path = tmp_path / "defaults.toml"
    path.write_text(
        text.replace("max_equivalent_full_cycles_per_year = 400.0\n", "", 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="max_equivalent_full_cycles_per_year"):
        load_central_defaults(path)


def test_negative_or_nonfinite_cycle_limit_fails():
    with pytest.raises(BatteryConfigError, match="max_equivalent_full_cycles_per_year"):
        BatteryConfig(10, 8, 8, 1.0, 1.0, max_equivalent_full_cycles_per_year=-1.0)
    with pytest.raises(BatteryConfigError, match="finite"):
        BatteryConfig(10, 8, 8, 1.0, 1.0, max_equivalent_full_cycles_per_year=float("nan"))
    with pytest.raises(BatteryConfigError, match="finite"):
        BatteryConfig(10, 8, 8, 1.0, 1.0, max_equivalent_full_cycles_per_year=float("inf"))


def test_toml_and_cli_cycle_limit_precedence(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    run_toml = tmp_path / "site.toml"
    run_toml.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"

[output]
directory = "{(tmp_path / "run").as_posix()}"

[battery]
max_equivalent_full_cycles_per_year = 250.0
""",
        encoding="utf-8",
    )
    config, audit = resolve_simulation_config(toml_path=run_toml)
    assert config.battery.max_equivalent_full_cycles_per_year == 250.0
    assert audit["value_sources"]["battery"]["max_equivalent_full_cycles_per_year"] == "run_toml"

    config_cli, audit_cli = resolve_simulation_config(
        toml_path=run_toml,
        cli={"max_equivalent_full_cycles_per_year": 180.0},
    )
    assert config_cli.battery.max_equivalent_full_cycles_per_year == 180.0
    assert audit_cli["value_sources"]["battery"]["max_equivalent_full_cycles_per_year"] == "cli"

    defaults_only, audit_defaults = resolve_simulation_config(
        cli={"input": parquet, "output_dir": tmp_path / "out"}
    )
    assert defaults_only.battery.max_equivalent_full_cycles_per_year == 400.0
    assert audit_defaults["value_sources"]["battery"]["max_equivalent_full_cycles_per_year"] == "defaults_toml"


def test_complete_2024_year_fraction_is_exactly_one():
    start = pd.Timestamp("2024-01-01", tz="Europe/Brussels")
    end = pd.Timestamp("2025-01-01", tz="Europe/Brussels")
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    frame = pd.DataFrame({"timestamp_local": index, "interval_hours": INTERVAL_HOURS})
    assert selected_period_year_fraction(frame) == 1.0
    assert calendar_year_physical_hours(2024) == pytest.approx(366 * 24.0)


def test_half_year_and_cross_year_proration():
    start = pd.Timestamp("2024-01-01", tz="Europe/Brussels")
    mid = pd.Timestamp("2024-07-01", tz="Europe/Brussels")
    half = pd.date_range(start, mid, freq="15min", inclusive="left")
    half_frame = pd.DataFrame({"timestamp_local": half, "interval_hours": INTERVAL_HOURS})
    assert selected_period_year_fraction(half_frame) == pytest.approx(0.5, abs=0.01)

    late = pd.date_range(
        pd.Timestamp("2024-12-31 22:00", tz="Europe/Brussels"),
        pd.Timestamp("2025-01-01 02:00", tz="Europe/Brussels"),
        freq="15min",
        inclusive="left",
    )
    cross = pd.DataFrame({"timestamp_local": late, "interval_hours": INTERVAL_HOURS})
    hours_2024 = float(cross.loc[pd.to_datetime(cross["timestamp_local"]).dt.year == 2024, "interval_hours"].sum())
    hours_2025 = float(cross.loc[pd.to_datetime(cross["timestamp_local"]).dt.year == 2025, "interval_hours"].sum())
    expected = hours_2024 / calendar_year_physical_hours(2024) + hours_2025 / calendar_year_physical_hours(2025)
    assert selected_period_year_fraction(cross) == pytest.approx(expected)
    assert 0.0 < selected_period_year_fraction(cross) < 0.01


def test_low_cycle_limit_binds_and_changes_self_consumption_dispatch():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    frame = frame.copy()
    frame["interval_hours"] = calendar_year_physical_hours(2024) / len(frame)
    free = BatteryConfig(10, 100, 100, 1.0, 1.0, max_equivalent_full_cycles_per_year=UNCONSTRAINED)
    limited = BatteryConfig(10, 100, 100, 1.0, 1.0, max_equivalent_full_cycles_per_year=0.05)
    free_run = optimize_self_consumption(frame, free)
    limited_run = optimize_self_consumption(frame, limited)
    assert float(free_run.frame["discharge_load_kwh"].sum()) > float(limited_run.frame["discharge_load_kwh"].sum())
    report = cycle_limit_report(limited_run.frame, limited)
    assert report["cycle_limit_binding"] is True
    assert report["equivalent_full_cycles"] == pytest.approx(0.05, abs=1e-6)
    assert float(limited_run.frame["discharge_load_kwh"].sum()) == pytest.approx(0.5, abs=1e-6)


def test_rule_based_controller_never_exceeds_cycle_budget():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 5.0, "pv": 5.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 5.0, "pv": 5.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    frame = frame.copy()
    frame["interval_hours"] = calendar_year_physical_hours(2024) / len(frame)
    config = BatteryConfig(10, 100, 100, 1.0, 1.0, max_equivalent_full_cycles_per_year=0.05)
    result = run_reference_controller(frame, config)
    assert result.feasibility_ok
    allowed = allowed_stored_throughput_kwh(config, selected_period_year_fraction(frame))
    actual = float(
        config.eta_charge * result.frame["charge_pv_kwh"].sum()
        + result.frame["discharge_load_kwh"].sum() / config.eta_discharge
    )
    assert actual <= allowed + 1e-9
    report = cycle_limit_report(result.frame, config)
    assert report["equivalent_full_cycles"] <= report["allowed_equivalent_full_cycles"] + 1e-12
    unconstrained = BatteryConfig(10, 100, 100, 1.0, 1.0, max_equivalent_full_cycles_per_year=UNCONSTRAINED)
    free = run_reference_controller(frame, unconstrained)
    assert float(free.frame["discharge_load_kwh"].sum()) > float(result.frame["discharge_load_kwh"].sum())
