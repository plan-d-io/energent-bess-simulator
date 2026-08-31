"""Fixed ISO-week windows for seasonal dispatch plots."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from btm_sim.fluvius.constants import INTERVAL_HOURS, TZ_NAME

SEASON_WEEKS = {
    "winter": 3,
    "spring": 19,
    "summer": 26,
    "autumn": 41,
}

SEASON_ORDER = ("winter", "spring", "summer", "autumn")


def select_seasonal_weeks(
    frame: pd.DataFrame,
    *,
    season_weeks: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return complete target weeks present in the frame, plus omitted seasons."""
    weeks = dict(SEASON_WEEKS if season_weeks is None else season_weeks)
    if frame.empty:
        return {
            "iso_year": None,
            "included": [],
            "omitted_seasons": list(SEASON_ORDER),
            "note": (
                "Fixed seasonal traces for visual inspection; not statistically "
                "representative weeks."
            ),
        }

    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    utc = pd.to_datetime(frame["timestamp_utc"], utc=True)
    iso_years = [int(ts.isocalendar().year) for ts in local]
    iso_year = int(pd.Series(iso_years).value_counts().idxmax())
    zone = ZoneInfo(TZ_NAME)

    included: list[dict[str, Any]] = []
    omitted: list[str] = []
    for season in SEASON_ORDER:
        week = weeks[season]
        window = _week_window(iso_year, week, zone)
        if window is None:
            omitted.append(season)
            continue
        start_utc, end_utc, start_local, end_local = window
        mask = (utc >= start_utc) & (utc < end_utc)
        expected = int(round((end_utc - start_utc).total_seconds() / 3600.0 / INTERVAL_HOURS))
        if int(mask.sum()) == expected:
            included.append(
                {
                    "season": season,
                    "iso_week": week,
                    "iso_year": iso_year,
                    "start_utc": start_utc.isoformat(),
                    "end_utc_exclusive": end_utc.isoformat(),
                    "start_local": start_local.isoformat(),
                    "end_local_exclusive": end_local.isoformat(),
                }
            )
        else:
            omitted.append(season)

    return {
        "iso_year": iso_year,
        "included": included,
        "omitted_seasons": omitted,
        "note": (
            "Fixed seasonal traces for visual inspection; not statistically "
            "representative weeks."
        ),
    }


def week_mask(frame: pd.DataFrame, window: dict[str, Any]) -> pd.Series:
    utc = pd.to_datetime(frame["timestamp_utc"], utc=True)
    start = pd.Timestamp(window["start_utc"])
    end = pd.Timestamp(window["end_utc_exclusive"])
    return (utc >= start) & (utc < end)


def plot_filename(scenario: str, season: str, iso_week: int) -> str:
    return f"{scenario}_{season}_week{iso_week:02d}.png"


def _week_window(
    iso_year: int, iso_week: int, zone: ZoneInfo
) -> tuple[pd.Timestamp, pd.Timestamp, datetime, datetime] | None:
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError:
        return None
    start_local = datetime(monday.year, monday.month, monday.day, tzinfo=zone)
    end_local = start_local + timedelta(days=7)
    start_utc = pd.Timestamp(start_local).tz_convert("UTC")
    end_utc = pd.Timestamp(end_local).tz_convert("UTC")
    return start_utc, end_utc, start_local, end_local
