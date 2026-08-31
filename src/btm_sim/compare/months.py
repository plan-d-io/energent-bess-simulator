"""Europe/Brussels local calendar months and completeness from UTC bounds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from btm_sim.fluvius.constants import TZ, TZ_NAME

STEP = pd.Timedelta(minutes=15)


@dataclass(frozen=True)
class LocalMonthWindow:
    """One local calendar month overlapping the selected physical intervals."""

    month: str
    year: int
    month_number: int
    start_local: pd.Timestamp
    end_local: pd.Timestamp
    start_utc: pd.Timestamp
    end_utc: pd.Timestamp
    expected_n_intervals: int
    n_intervals: int
    complete: bool

    def to_identity(self) -> dict[str, object]:
        return {
            "month": self.month,
            "month_start_local": self.start_local.isoformat(),
            "month_end_local_exclusive": self.end_local.isoformat(),
            "complete_local_month": self.complete,
            "n_intervals": self.n_intervals,
        }


def month_bounds(year: int, month: int) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Return local start/end (end exclusive) and the matching UTC bounds."""
    start_local = pd.Timestamp(datetime(year, month, 1, tzinfo=TZ))
    if month == 12:
        end_local = pd.Timestamp(datetime(year + 1, 1, 1, tzinfo=TZ))
    else:
        end_local = pd.Timestamp(datetime(year, month + 1, 1, tzinfo=TZ))
    start_utc = start_local.tz_convert("UTC")
    end_utc = end_local.tz_convert("UTC")
    return start_local, end_local, start_utc, end_utc


def expected_month_quarter_hours(year: int, month: int) -> int:
    """Physical 15-minute count from local month start to the next local month start."""
    _, _, start_utc, end_utc = month_bounds(year, month)
    return int((end_utc - start_utc) / STEP)


def utc_series(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["timestamp_utc"], utc=True)


def local_series(frame: pd.DataFrame) -> pd.Series:
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        return local.dt.tz_localize(TZ_NAME)
    return local.dt.tz_convert(TZ_NAME)


def month_interval_mask(frame: pd.DataFrame, window: LocalMonthWindow) -> pd.Series:
    utc = utc_series(frame)
    return (utc >= window.start_utc) & (utc < window.end_utc)


def local_month_coverage(frame: pd.DataFrame) -> list[LocalMonthWindow]:
    """Describe each local month present in the frame and whether it is complete.

    Completeness is the physical UTC span of [local month start, next month start).
    Do not assume ``days * 96`` intervals: March and October clock changes change
    the quarter-hour count.
    """
    if frame.empty:
        return []
    local = local_series(frame)
    utc = utc_series(frame)
    keys = sorted({f"{year:04d}-{month:02d}" for year, month in zip(local.dt.year, local.dt.month, strict=True)})
    windows: list[LocalMonthWindow] = []
    for key in keys:
        year = int(key[:4])
        month = int(key[5:7])
        start_local, end_local, start_utc, end_utc = month_bounds(year, month)
        expected = int((end_utc - start_utc) / STEP)
        in_month = (utc >= start_utc) & (utc < end_utc)
        actual = pd.DatetimeIndex(pd.to_datetime(utc.loc[in_month], utc=True)).sort_values()
        n_intervals = int(len(actual))
        complete = _covers_month(actual, start_utc, end_utc, expected)
        windows.append(
            LocalMonthWindow(
                month=key,
                year=year,
                month_number=month,
                start_local=start_local,
                end_local=end_local,
                start_utc=start_utc,
                end_utc=end_utc,
                expected_n_intervals=expected,
                n_intervals=n_intervals,
                complete=complete,
            )
        )
    return windows


def complete_month_keys(coverage: list[LocalMonthWindow]) -> list[str]:
    return [window.month for window in coverage if window.complete]


def _covers_month(
    actual: pd.DatetimeIndex,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    expected_n: int,
) -> bool:
    if len(actual) != expected_n or actual.has_duplicates:
        return False
    start = pd.Timestamp(start_utc).tz_convert("UTC")
    end = pd.Timestamp(end_utc).tz_convert("UTC")
    if abs((actual[0] - start).total_seconds()) > 1e-6:
        return False
    if abs((actual[-1] + STEP - end).total_seconds()) > 1e-6:
        return False
    if len(actual) == 1:
        return True
    diffs = pd.Series(actual[1:]) - pd.Series(actual[:-1])
    return bool((diffs == STEP).all())
