"""Canonical quarter-hour frames for optimizer tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from btm_sim.fluvius.constants import CANONICAL_COLUMNS, INTERVAL_HOURS

HIGH_CYCLE_LIMIT = 1_000_000.0


def battery_cfg(*args, **kwargs):
    """BatteryConfig for short synthetic frames; do not prorate 400 cycles onto a few QH."""
    from btm_sim.battery.config import BatteryConfig

    kwargs.setdefault("max_equivalent_full_cycles_per_year", HIGH_CYCLE_LIMIT)
    return BatteryConfig(*args, **kwargs)

UTC = timezone.utc


def qh_frame(rows: list[dict], start: datetime | None = None) -> pd.DataFrame:
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
