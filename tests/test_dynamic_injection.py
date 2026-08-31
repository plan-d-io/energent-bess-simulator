"""Customer-first dynamic injection LP and settlement."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.runner import run_comparison
from btm_sim.config.schema import TariffConfig
from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH
from btm_sim.optimizer.dynamic_injection import optimize_dynamic_injection
from btm_sim.optimizer.self_consumption import optimize_self_consumption
from tests.lp_frames import qh_frame

UNCONSTRAINED = 1_000_000.0


def _cfg() -> BatteryConfig:
    return BatteryConfig(10, 100, 100, 1.0, 1.0, max_equivalent_full_cycles_per_year=UNCONSTRAINED)


def _spread_frame() -> pd.DataFrame:
    return qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 0.0, "pv": 0.0},
        ]
    )


def _write_prices(path: Path, frame: pd.DataFrame, values: list[float]) -> Path:
    table = pd.DataFrame(
        {
            "datetime_utc": pd.to_datetime(frame["timestamp_utc"], utc=True),
            "da_price_eur_mwh": values,
            "native_resolution": "PT15M",
            "upsampled_from_hourly": False,
            "source_file": "fixture.csv",
        }
    )
    table.to_parquet(path, index=False)
    return path


def test_dynamic_preserves_customer_dispatch_and_exports_after_zero_import():
    frame = _spread_frame()
    prices = np.array([10.0, 10.0, 10.0, 200.0])
    cfg = _cfg()
    sc = optimize_self_consumption(frame, cfg)
    result = optimize_dynamic_injection(frame, cfg, prices, customer_first=sc)
    assert result.ok
    assert result.summary["solver"]["num_int_vars"] == 0
    assert result.summary["solver"]["num_bin_vars"] == 0
    assert result.summary["solver"]["continuous_lp"] is True
    np.testing.assert_allclose(
        result.frame["discharge_load_kwh"].to_numpy(),
        sc.frame["discharge_load_kwh"].to_numpy(),
        atol=DOCUMENTED_TOLERANCE_KWH,
    )
    assert float(result.frame["discharge_load_kwh"].sum()) == pytest.approx(2.0)
    assert float(result.frame["discharge_grid_kwh"].sum()) == pytest.approx(2.0, abs=1e-6)
    assert float(result.frame["discharge_grid_kwh"].iloc[3]) == pytest.approx(2.0, abs=1e-6)
    import_when_exporting = result.frame.loc[result.frame["discharge_grid_kwh"] > 1e-9, "grid_import_kwh"]
    assert (import_when_exporting <= DOCUMENTED_TOLERANCE_KWH).all()
    assert (result.frame["grid_import_kwh"] <= result.frame["grid_import_baseline_kwh"] + 1e-9).all()
    assert float(result.frame["charge_pv_kwh"].sum()) <= float(result.frame["grid_export_baseline_kwh"].sum()) + 1e-9


def test_high_price_cannot_cut_preserved_customer_supply():
    frame = _spread_frame()
    prices = np.array([10.0, 10.0, 1000.0, 10.0])
    cfg = _cfg()
    sc = optimize_self_consumption(frame, cfg)
    result = optimize_dynamic_injection(frame, cfg, prices, customer_first=sc)
    np.testing.assert_allclose(
        result.frame["discharge_load_kwh"].to_numpy(),
        sc.frame["discharge_load_kwh"].to_numpy(),
        atol=DOCUMENTED_TOLERANCE_KWH,
    )
    assert float(result.frame.loc[2, "discharge_load_kwh"]) == pytest.approx(2.0)


def test_dynamic_settlement_uses_interval_prices_and_fixed_tariff_baseline(tmp_path: Path):
    frame = _spread_frame()
    prices = [10.0, 10.0, 10.0, 200.0]
    path = _write_prices(tmp_path / "prices.parquet", frame, prices)
    cfg = _cfg()
    result = run_comparison(
        frame,
        cfg,
        output_dir=tmp_path / "run",
        create_plots=False,
        dynamic_injection_prices=path,
    )
    assert result.ok
    assert list(result.summary["scenario_order"])[-1] == "dynamic_injection"
    assert result.summary["artifact_schema_version"] == 2
    dynamic = result.summary["scenarios"]["dynamic_injection"]
    no_battery = result.summary["scenarios"]["no_battery"]
    revenue = result.summary["scenarios"]["revenue"]
    assert dynamic["battery_discharge_to_grid_kwh"] == pytest.approx(2.0, abs=1e-6)
    assert dynamic["discharge_load_kwh"] == pytest.approx(
        result.summary["scenarios"]["self_consumption"]["discharge_load_kwh"]
    )
    assert dynamic["revenue"]["financial_baseline"] == "fixed_tariff_no_battery"
    assert dynamic["revenue"]["revenue_change_eur"] == pytest.approx(
        dynamic["revenue"]["total_energent_pv_revenue_eur"]
        - no_battery["revenue"]["total_energent_pv_revenue_eur"]
    )
    export = result.dispatch["dynamic_injection_grid_export_kwh"].to_numpy(dtype=float)
    da = result.dispatch["da_price_eur_mwh"].to_numpy(dtype=float)
    assert dynamic["revenue"]["dynamic_grid_injection_revenue_eur"] == pytest.approx(float((da * export / 1000.0).sum()))
    tariffs = TariffConfig()
    assert no_battery["revenue"]["total_export_eur"] == pytest.approx(
        float(
            (
                result.dispatch["no_battery_grid_export_kwh"].to_numpy(dtype=float)
                * result.dispatch["export_rate_eur_per_mwh"].to_numpy(dtype=float)
                / 1000.0
            ).sum()
        )
    )
    del tariffs
    assert (tmp_path / "run" / "dynamic_injection_prices.parquet").exists()
    aligned = pd.read_parquet(tmp_path / "run" / "dynamic_injection_prices.parquet")
    assert len(aligned) == 4
    assert list(aligned["da_price_eur_mwh"]) == pytest.approx(prices)
    assert revenue["discharge_load_kwh"] == pytest.approx(
        result.summary["scenarios"]["self_consumption"]["discharge_load_kwh"]
    )
