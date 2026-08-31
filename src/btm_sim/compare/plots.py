"""Deterministic three-panel seasonal dispatch plots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.weeks import plot_filename, week_mask
from btm_sim.fluvius.constants import TZ_NAME

SCENARIO_TITLES = {
    "self_consumption": "Best-case self-consumption",
    "peak_reduction": "Best-case peak reduction",
    "revenue": "Best-case Energent PV revenue",
    "dynamic_injection": "Dynamic injection tariff",
}

PLOT_SCENARIOS = ("self_consumption", "peak_reduction", "revenue", "dynamic_injection")


def write_seasonal_plots(
    dispatch: pd.DataFrame,
    config: BatteryConfig,
    weeks: dict[str, Any],
    plots_dir: Path,
) -> list[str]:
    plots_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for window in weeks.get("included", []):
        slice_frame = dispatch.loc[week_mask(dispatch, window)].copy()
        if slice_frame.empty:
            continue
        for scenario in PLOT_SCENARIOS:
            name = plot_filename(scenario, window["season"], int(window["iso_week"]))
            path = plots_dir / name
            _draw_week(slice_frame, config, scenario=scenario, window=window, path=path)
            written.append(f"plots/{name}")
            files = window.setdefault("files", [])
            if f"plots/{name}" not in files:
                files.append(f"plots/{name}")
    return written


def _draw_week(
    frame: pd.DataFrame,
    config: BatteryConfig,
    *,
    scenario: str,
    window: dict[str, Any],
    path: Path,
) -> None:
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    dt = frame["interval_hours"].to_numpy(dtype=float)
    pv_kw = frame["pv_production_kwh"].to_numpy(dtype=float) / dt
    load_kw = frame["site_load_kwh"].to_numpy(dtype=float) / dt
    base_imp = frame["grid_import_baseline_kwh"].to_numpy(dtype=float) / dt
    base_exp = frame["grid_export_baseline_kwh"].to_numpy(dtype=float) / dt
    batt_imp = frame[f"{scenario}_grid_import_kwh"].to_numpy(dtype=float) / dt
    batt_exp = frame[f"{scenario}_grid_export_kwh"].to_numpy(dtype=float) / dt
    soc = frame[f"{scenario}_soc_start_kwh"].to_numpy(dtype=float)
    x = local.to_numpy()

    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(12.5, 9.0), constrained_layout=True)
    power, grid, battery = axes

    power.step(x, pv_kw, where="post", color="#d4a017", linewidth=1.2, label="PV production")
    power.step(x, load_kw, where="post", color="#1f4e79", linewidth=1.2, label="Customer load")
    power.set_ylabel("kW")
    power.legend(loc="upper right", frameon=False)
    power.grid(True, axis="y", alpha=0.3)

    grid.axhline(0.0, color="0.4", linewidth=0.8)
    grid.step(x, base_imp, where="post", color="#c47b7b", linestyle="--", linewidth=1.0, label="Baseline import")
    grid.step(x, -base_exp, where="post", color="#7aa37a", linestyle="--", linewidth=1.0, label="Baseline export")
    grid.step(x, batt_imp, where="post", color="#b22222", linewidth=1.2, label="Import after battery")
    grid.step(x, -batt_exp, where="post", color="#2e8b57", linewidth=1.2, label="Export after battery")
    grid.set_ylabel("kW")
    grid.legend(loc="upper right", frameon=False, ncol=2)
    grid.grid(True, axis="y", alpha=0.3)

    battery.step(x, soc, where="post", color="#5b4b8a", linewidth=1.2, label="State of charge")
    battery.axhline(config.e_usable_kwh, color="#5b4b8a", linestyle=":", linewidth=1.0, label="Usable capacity")
    battery.set_ylabel("kWh")
    battery.set_ylim(bottom=0.0, top=max(config.e_usable_kwh, 0.0) * 1.08 if config.e_usable_kwh else 1.0)
    battery.legend(loc="upper right", frameon=False)
    battery.grid(True, axis="y", alpha=0.3)
    battery.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b", tz=local.dt.tz))
    battery.set_xlabel("Europe/Brussels local time")

    start_local = window["start_local"][:10]
    end_local = window["end_local_exclusive"][:10]
    power_label = (
        f"{config.p_charge_kw:g}/{config.p_discharge_kw:g} kW"
        if config.p_charge_kw != config.p_discharge_kw
        else f"{config.p_charge_kw:g} kW"
    )
    fig.suptitle(
        f"{SCENARIO_TITLES[scenario]} · {window['season']} · ISO week {int(window['iso_week']):02d} "
        f"· {start_local} to {end_local} · {config.e_usable_kwh:g} kWh / {power_label}\n"
        "Fixed seasonal trace for visual inspection, not a representative week",
        fontsize=11,
    )
    fig.savefig(path, dpi=120)
    plt.close(fig)
