"""Discover continuous common periods and calendar-year coverage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from btm_sim.fluvius.constants import INTERVAL, TZ, TZ_NAME

PeriodKind = Literal[
    "full_calendar_year",
    "partial_calendar_year",
    "rolling_twelve_months",
    "common_overlap",
]


@dataclass(frozen=True)
class PeriodOffer:
    id: str
    kind: PeriodKind
    start_utc: pd.Timestamp
    end_utc: pd.Timestamp
    n_intervals: int
    n_unvalidated: int
    complete_calendar_year: bool
    label: str

    def contains(self, timestamps: pd.Series) -> pd.Series:
        return (timestamps >= self.start_utc) & (timestamps < self.end_utc)

    def to_dict(self) -> dict:
        start_local = self.start_utc.tz_convert(TZ_NAME)
        end_local = self.end_utc.tz_convert(TZ_NAME)
        return {
            "id": self.id,
            "kind": self.kind,
            "start_utc": _iso(self.start_utc),
            "end_utc_exclusive": _iso(self.end_utc),
            "start_local": _iso(start_local),
            "end_local_exclusive": _iso(end_local),
            "n_intervals": self.n_intervals,
            "n_unvalidated": self.n_unvalidated,
            "complete_calendar_year": self.complete_calendar_year,
            "label": self.label,
        }


def _iso(value: pd.Timestamp) -> str:
    return value.isoformat()


def year_bounds_utc(year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(datetime(year, 1, 1, tzinfo=TZ)).tz_convert("UTC")
    end = pd.Timestamp(datetime(year + 1, 1, 1, tzinfo=TZ)).tz_convert("UTC")
    return start, end


def expected_quarter_hours(year: int) -> int:
    start, end = year_bounds_utc(year)
    return int((end - start) / pd.Timedelta(minutes=15))


def contiguous_runs(timestamps: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    values = pd.to_datetime(timestamps, utc=True).dropna().sort_values().unique()
    if len(values) == 0:
        return []
    runs: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    run_start = values[0]
    prev = values[0]
    step = pd.Timedelta(minutes=15)
    for ts in values[1:]:
        if ts - prev != step:
            runs.append((pd.Timestamp(run_start), pd.Timestamp(prev) + step))
            run_start = ts
        prev = ts
    runs.append((pd.Timestamp(run_start), pd.Timestamp(prev) + step))
    return runs


def add_calendar_months(ts: pd.Timestamp, months: int) -> pd.Timestamp:
    local = ts.tz_convert(TZ_NAME)
    month = local.month - 1 + months
    year = local.year + month // 12
    month = month % 12 + 1
    day = min(local.day, _month_length(year, month))
    shifted = local.replace(year=year, month=month, day=day)
    return shifted.tz_convert("UTC")


def _month_length(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=TZ)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=TZ)
    cur = datetime(year, month, 1, tzinfo=TZ)
    return (nxt - cur).days


def _count_unvalidated(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> int:
    mask = (frame["timestamp_utc"] >= start) & (frame["timestamp_utc"] < end)
    if "quality_flag" in frame.columns:
        return int((mask & (frame["quality_flag"] == "unvalidated")).sum())
    return 0


def discover_periods(frame: pd.DataFrame) -> list[PeriodOffer]:
    """Offer continuous common periods, full/partial years, and rolling windows."""
    if frame.empty:
        return []

    runs = contiguous_runs(frame["timestamp_utc"])
    offers: list[PeriodOffer] = []
    n_runs = len(runs)

    for run_index, (run_start, run_end) in enumerate(runs, start=1):
        run_id = "common" if n_runs == 1 else f"common-{run_index}"
        n_intervals = int((run_end - run_start) / pd.Timedelta(minutes=15))
        offers.append(
            PeriodOffer(
                id=run_id,
                kind="common_overlap",
                start_utc=run_start,
                end_utc=run_end,
                n_intervals=n_intervals,
                n_unvalidated=_count_unvalidated(frame, run_start, run_end),
                complete_calendar_year=False,
                label="Continuous common measured overlap",
            )
        )

        start_year = int(run_start.tz_convert(TZ_NAME).year)
        end_year = int((run_end - INTERVAL).tz_convert(TZ_NAME).year)
        complete_years: list[int] = []
        for year in range(start_year, end_year + 1):
            year_start, year_end = year_bounds_utc(year)
            contained = year_start >= run_start and year_end <= run_end
            n_year = expected_quarter_hours(year)
            if contained:
                complete_years.append(year)
                offers.append(
                    PeriodOffer(
                        id=str(year),
                        kind="full_calendar_year",
                        start_utc=year_start,
                        end_utc=year_end,
                        n_intervals=n_year,
                        n_unvalidated=_count_unvalidated(frame, year_start, year_end),
                        complete_calendar_year=True,
                        label=f"Calendar year {year}",
                    )
                )
            else:
                clipped_start = max(year_start, run_start)
                clipped_end = min(year_end, run_end)
                if clipped_end <= clipped_start:
                    continue
                n_partial = int((clipped_end - clipped_start) / pd.Timedelta(minutes=15))
                offers.append(
                    PeriodOffer(
                        id=str(year),
                        kind="partial_calendar_year",
                        start_utc=clipped_start,
                        end_utc=clipped_end,
                        n_intervals=n_partial,
                        n_unvalidated=_count_unvalidated(frame, clipped_start, clipped_end),
                        complete_calendar_year=False,
                        label=f"Partial calendar year {year}",
                    )
                )

        if not complete_years:
            twelve_end = add_calendar_months(run_start, 12)
            if twelve_end <= run_end:
                offers.append(
                    PeriodOffer(
                        id=f"rolling-{run_start.tz_convert(TZ_NAME).strftime('%Y-%m-%d')}",
                        kind="rolling_twelve_months",
                        start_utc=run_start,
                        end_utc=twelve_end,
                        n_intervals=int((twelve_end - run_start) / pd.Timedelta(minutes=15)),
                        n_unvalidated=_count_unvalidated(frame, run_start, twelve_end),
                        complete_calendar_year=False,
                        label="Rolling twelve-month window from overlap start",
                    )
                )
            twelve_start = add_calendar_months(run_end, -12)
            if (
                twelve_start >= run_start
                and twelve_start < run_end
                and (not offers or offers[-1].start_utc != twelve_start)
            ):
                offers.append(
                    PeriodOffer(
                        id=f"rolling-end-{twelve_start.tz_convert(TZ_NAME).strftime('%Y-%m-%d')}",
                        kind="rolling_twelve_months",
                        start_utc=twelve_start,
                        end_utc=run_end,
                        n_intervals=int((run_end - twelve_start) / pd.Timedelta(minutes=15)),
                        n_unvalidated=_count_unvalidated(frame, twelve_start, run_end),
                        complete_calendar_year=False,
                        label="Rolling twelve-month window ending at overlap end",
                    )
                )

    # Stable unique ids: if a year appears in multiple runs, suffix the later ones.
    seen: dict[str, int] = {}
    unique: list[PeriodOffer] = []
    for offer in offers:
        count = seen.get(offer.id, 0)
        seen[offer.id] = count + 1
        if count:
            offer = PeriodOffer(
                id=f"{offer.id}-{count + 1}",
                kind=offer.kind,
                start_utc=offer.start_utc,
                end_utc=offer.end_utc,
                n_intervals=offer.n_intervals,
                n_unvalidated=offer.n_unvalidated,
                complete_calendar_year=offer.complete_calendar_year,
                label=offer.label,
            )
        unique.append(offer)
    return unique


def resolve_period_id(offers: list[PeriodOffer], period_id: str) -> PeriodOffer | None:
    wanted = period_id.strip()
    for offer in offers:
        if offer.id == wanted:
            return offer
    return None
