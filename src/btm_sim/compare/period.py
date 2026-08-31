"""Describe the selected local period as calendar year, rolling, or partial."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from btm_sim.fluvius.constants import TZ, TZ_NAME
from btm_sim.fluvius.periods import add_calendar_months, expected_quarter_hours

KIND_CALENDAR_YEAR = "full_calendar_year"
KIND_ROLLING = "rolling_twelve_months"
KIND_PARTIAL = "partial_period"


def describe_selected_period(frame: pd.DataFrame) -> dict[str, Any]:
    """Return UTC/local bounds and a user-facing period kind."""
    if frame.empty:
        raise ValueError("Cannot describe an empty interval frame")
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    last_hours = float(frame["interval_hours"].iloc[-1])
    start_utc = pd.Timestamp(frame["timestamp_utc"].iloc[0])
    end_utc = pd.Timestamp(frame["timestamp_utc"].iloc[-1]) + pd.Timedelta(hours=last_hours)
    start_local = pd.Timestamp(local.iloc[0])
    end_local = pd.Timestamp(local.iloc[-1]) + pd.Timedelta(hours=last_hours)
    n_intervals = int(len(frame))
    kind, label, complete = _classify(start_utc, end_utc, start_local, end_local, n_intervals)
    return {
        "start_utc": start_utc.isoformat(),
        "end_utc_exclusive": end_utc.isoformat(),
        "start_local": start_local.isoformat(),
        "end_local_exclusive": end_local.isoformat(),
        "n_intervals": n_intervals,
        "kind": kind,
        "label": label,
        "complete_calendar_year": complete,
    }


def _classify(
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    start_local: pd.Timestamp,
    end_local: pd.Timestamp,
    n_intervals: int,
) -> tuple[str, str, bool]:
    start = start_local.tz_convert(TZ_NAME) if start_local.tzinfo is not None else start_local.tz_localize(TZ_NAME)
    end = end_local.tz_convert(TZ_NAME) if end_local.tzinfo is not None else end_local.tz_localize(TZ_NAME)
    midnight = start.hour == 0 and start.minute == 0 and start.second == 0 and start.microsecond == 0
    if start.month == 1 and start.day == 1 and midnight:
        year = int(start.year)
        expected_end = pd.Timestamp(datetime(year + 1, 1, 1, tzinfo=TZ))
        if abs((end - expected_end).total_seconds()) < 1e-6 and n_intervals == expected_quarter_hours(year):
            return KIND_CALENDAR_YEAR, f"Complete calendar year {year}", True
    start_u = start_utc.tz_convert("UTC") if start_utc.tzinfo is not None else start_utc.tz_localize("UTC")
    end_u = end_utc.tz_convert("UTC") if end_utc.tzinfo is not None else end_utc.tz_localize("UTC")
    rolling_end = add_calendar_months(start_u, 12)
    if abs((rolling_end - end_u).total_seconds()) < 1.0:
        return KIND_ROLLING, "Rolling twelve-month period", False
    return KIND_PARTIAL, "Partial period", False
