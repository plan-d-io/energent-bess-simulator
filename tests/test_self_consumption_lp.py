"""Hand-computable self-consumption tests and an optional Gurobi check."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.battery.dispatch import run_reference_controller
from btm_sim.fluvius.constants import CANONICAL_COLUMNS, INTERVAL_HOURS
from btm_sim.optimizer.model import build_physical_lp
from btm_sim.optimizer.self_consumption import optimize_self_consumption

UTC = timezone.utc


def _gurobi_available() -> bool:
    try:
        from btm_sim.optimizer.model import start_gurobi_env

        _gp, env = start_gurobi_env()
        env.dispose()
        return True
    except Exception:
        return False


GUROBI_AVAILABLE = _gurobi_available()


def _frame(rows: list[dict], start: datetime | None = None) -> pd.DataFrame:
    origin = start or datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
    records = []
    for index, row in enumerate(rows):
        import0 = float(row["imp"])
        export0 = float(row["exp"])
        pv = float(row["pv"])
        ts = row.get("ts")
        if ts is None:
            ts = pd.Timestamp(origin) + pd.Timedelta(minutes=15 * index)
        else:
            ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        load = row.get("load", pv + import0 - export0)
        records.append(
            {
                "timestamp_utc": ts.tz_convert("UTC"),
                "timestamp_local": ts.tz_convert("Europe/Brussels"),
                "interval_hours": INTERVAL_HOURS,
                "grid_import_baseline_kwh": import0,
                "grid_export_baseline_kwh": export0,
                "pv_production_kwh": pv,
                "site_load_kwh": load,
                "offtake_quality": "validated",
                "injection_quality": "validated",
                "pv_quality": "validated",
                "quality_flag": "validated",
                "pv_source": "measured_fluvius",
            }
        )
    frame = pd.DataFrame.from_records(records)
    assert set(CANONICAL_COLUMNS) <= set(frame.columns)
    return frame


@pytest.mark.skipif(not GUROBI_AVAILABLE, reason="Gurobi package or licence is not available")
def test_lp_is_continuous_with_expected_physics_variables():
    frame = _frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(5, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    lp = build_physical_lp(frame, cfg)
    assert lp.model.NumIntVars == 0
    assert lp.model.NumBinVars == 0
    # charge, discharge, soc (n+1), annual peak, monthly peaks
    assert lp.charge.size == 2
    assert lp.discharge.size == 2
    assert lp.soc.size == 3
    names = {constr.ConstrName.split("[")[0] for constr in lp.model.getConstrs()}
    assert "soc_initial" in names
    assert "soc_terminal" in names
    assert "soc_transition" in names
    assert "inverter_time" in names


def test_zero_battery_leaves_baseline_unchanged():
    frame = _frame([{"imp": 2.0, "exp": 0.5, "pv": 1.0}, {"imp": 0.0, "exp": 1.0, "pv": 1.0}])
    result = optimize_self_consumption(frame, BatteryConfig(0.0, 0.0, 0.0, 1.0, 1.0, 0.0))
    assert result.ok
    assert result.frame["charge_pv_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert result.frame["discharge_load_kwh"].tolist() == pytest.approx([0.0, 0.0])
    assert result.frame["grid_import_kwh"].tolist() == pytest.approx(frame["grid_import_baseline_kwh"].tolist())
    assert result.summary["soc_final_kwh"] == pytest.approx(0.0)
    assert result.summary["label"] == "perfect_foresight_upper_bound"


def test_terminal_soc_equals_initial():
    frame = _frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption(frame, cfg)
    assert result.frame["soc_start_kwh"].iloc[0] == pytest.approx(0.0)
    assert result.frame["soc_end_kwh"].iloc[-1] == pytest.approx(0.0)
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(1.0, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(1.0, abs=1e-5)


def test_efficiencies_reduce_deliverable_energy():
    frame = _frame([{"imp": 0.0, "exp": 2.0, "pv": 2.0}, {"imp": 5.0, "exp": 0.0, "pv": 0.0}])
    cfg = BatteryConfig(10, 100, 100, eta_charge=0.5, eta_discharge=0.5, soc_initial_kwh=0.0)
    result = optimize_self_consumption(frame, cfg)
    # 2 kWh AC charge -> 1 kWh stored -> 0.5 kWh AC discharge, terminal SoC 0.
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(2.0, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(0.5, abs=1e-5)
    assert result.summary["energy_kwh"]["useful_additional_pv"] == pytest.approx(0.5, abs=1e-5)


def test_power_and_energy_limits():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 10.0, "pv": 10.0},
            {"imp": 10.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(e_usable_kwh=0.4, p_charge_kw=4.0, p_discharge_kw=4.0, eta_charge=1.0, eta_discharge=1.0)
    result = optimize_self_consumption(frame, cfg)
    # Power cap 4 kW * 0.25 h = 1 kWh, but capacity is 0.4 kWh.
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(0.4, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[1] == pytest.approx(0.4, abs=1e-5)


def test_inverter_time_prevents_full_power_both_directions():
    frame = _frame([{"imp": 1.0, "exp": 1.0, "pv": 1.0}])
    cfg = BatteryConfig(10, 4, 4, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption(frame, cfg)
    # Without inverter time, c=d=1 would close SoC. Time-share forces c+d <= 1 kWh, so 0.5/0.5.
    assert result.frame["charge_pv_kwh"].iloc[0] == pytest.approx(0.5, abs=1e-5)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.5, abs=1e-5)


def test_lexico_stage1_then_stage2_prefers_lower_annual_peak():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 4.0, "pv": 4.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
            {"imp": 5.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(4, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["maximize_discharge_load_kwh"]["optimum"] == pytest.approx(4.0, abs=1e-5)
    # Splitting 2 kWh onto each 5 kWh import yields 3 kWh = 12 kW rather than leaving 20 kW.
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(12.0, abs=1e-4)
    assert result.frame["discharge_load_kwh"].sum() == pytest.approx(4.0, abs=1e-5)
    assert result.frame["grid_import_kw"].max() == pytest.approx(12.0, abs=1e-4)


def test_lexico_stage3_does_not_waste_shave_on_a_subpeak():
    rows = [
        {"ts": datetime(2024, 1, 15, 10, 0, tzinfo=UTC), "imp": 10.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 0, tzinfo=UTC), "imp": 0.0, "exp": 2.0, "pv": 2.0},
        {"ts": datetime(2024, 1, 15, 11, 15, tzinfo=UTC), "imp": 8.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 1, 15, 11, 30, tzinfo=UTC), "imp": 3.0, "exp": 0.0, "pv": 0.0},
        {"ts": datetime(2024, 2, 15, 10, 0, tzinfo=UTC), "imp": 5.0, "exp": 0.0, "pv": 0.0},
    ]
    frame = _frame(rows)
    cfg = BatteryConfig(2, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    result = optimize_self_consumption(frame, cfg)
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["maximize_discharge_load_kwh"]["optimum"] == pytest.approx(2.0, abs=1e-4)
    # First January interval cannot be shaved (empty battery) so annual peak stays 40 kW.
    assert stages["minimize_annual_peak_import_kw"]["optimum"] == pytest.approx(40.0, abs=1e-4)
    # Shaving February (20 kW -> 12 kW) yields monthly sum 40+12=52 kW.
    # Shaving an already-dominated January interval would leave February at 20 kW (sum 60).
    assert stages["minimize_sum_monthly_peak_import_kw"]["optimum"] == pytest.approx(52.0, abs=1e-3)
    assert result.frame["discharge_load_kwh"].iloc[0] == pytest.approx(0.0, abs=1e-6)
    assert result.frame["discharge_load_kwh"].iloc[-1] == pytest.approx(2.0, abs=1e-4)


def test_throughput_identity_without_fourth_solve():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 3.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 0.9, 0.8, soc_initial_kwh=0.0)
    result = optimize_self_consumption(frame, cfg)
    discharge = result.summary["energy_kwh"]["discharge_load"]
    throughput = result.summary["throughput"]["stored_throughput_kwh"]
    # soc_T = soc_0 implies throughput = 2 * discharge / eta_d
    assert throughput == pytest.approx(2.0 * discharge / cfg.eta_discharge)
    assert len(result.stages) == 3
    assert result.stages[-1]["stage"] == "minimize_sum_monthly_peak_import_kw"


def test_lp_beats_or_matches_reference_on_foresight_example():
    frame = _frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    cfg = BatteryConfig(10, 100, 100, 1.0, 1.0, soc_initial_kwh=0.0)
    lp = optimize_self_consumption(frame, cfg)
    ref = run_reference_controller(frame, cfg)
    assert lp.summary["energy_kwh"]["useful_additional_pv"] + 1e-6 >= ref.summary["energy_kwh"]["useful_additional_pv"]
    assert lp.summary["perfect_foresight_upper_bound"] is True
    assert lp.summary["not_upper_bound"] is False
    assert lp.summary["solver"]["continuous_lp"] is True
    assert all(stage["status"] == "OPTIMAL" for stage in lp.stages)
