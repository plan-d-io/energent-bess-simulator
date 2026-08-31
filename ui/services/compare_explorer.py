"""UTC-filtered dispatch queries for the full-comparison Data explorer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from ui.services.compare_display import ChartSpec, _chart
from ui.services.compare_format import SEASON_LABELS, case_label, fmt_count
from ui.presentation.tokens import CHART_EXPLORER

BRUSSELS = ZoneInfo("Europe/Brussels")
ORDINARY_WEEK_INTERVALS = 672
SPRING_DST_WEEK_INTERVALS = 668
AUTUMN_DST_WEEK_INTERVALS = 676
DISPATCH_PARQUET = "comparison_dispatch.parquet"
DISPATCH_BASE_COLUMNS = (
    "timestamp_utc",
    "timestamp_local",
    "interval_hours",
    "pv_production_kwh",
    "site_load_kwh",
    "grid_import_baseline_kwh",
    "grid_export_baseline_kwh",
)


class DispatchQueryError(Exception):
    """The explorer could not read the requested Parquet slice."""


@dataclass(frozen=True)
class WeekWindow:
    iso_year: int
    iso_week: int
    start_local: str
    end_local_exclusive: str
    start_utc: str
    end_utc_exclusive: str
    label: str
    source: str
    season: str | None = None


@dataclass(frozen=True)
class WeekQueryResult:
    frame: pd.DataFrame
    n_rows: int
    columns: tuple[str, ...]
    start_utc: str
    end_utc_exclusive: str
    parquet_identity: tuple[Any, ...]


def parquet_identity(path: Path | str) -> tuple[Any, ...]:
    file = Path(path)
    try:
        stat = file.stat()
        return (str(file.resolve()), int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        return (str(file), None, None)


def scenario_columns(scenario: str) -> tuple[str, ...]:
    return (
        f"{scenario}_grid_import_kwh",
        f"{scenario}_grid_export_kwh",
        f"{scenario}_charge_pv_kwh",
        f"{scenario}_discharge_load_kwh",
        f"{scenario}_soc_end_kwh",
    )


def optional_columns(scenario: str) -> tuple[str, ...]:
    return ("da_price_eur_mwh", f"{scenario}_discharge_grid_kwh")


def dispatch_columns_for(scenario: str, available: set[str] | None = None) -> tuple[str, ...]:
    required = DISPATCH_BASE_COLUMNS + scenario_columns(scenario)
    extras = optional_columns(scenario)
    if available is None:
        return required
    return required + tuple(name for name in extras if name in available)


def utc_scalar(value: str) -> pa.Scalar:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return pa.scalar(stamp.to_pydatetime(), type=pa.timestamp("ns", tz="UTC"))


def query_dispatch_week(
    parquet_path: Path | str,
    start_utc: str,
    end_utc_exclusive: str,
    scenario: str,
) -> WeekQueryResult:
    path = Path(parquet_path)
    if not path.is_file():
        raise DispatchQueryError("The dispatch Parquet file is missing.")
    try:
        dataset = ds.dataset(path, format="parquet")
    except (OSError, pa.ArrowInvalid) as exc:
        raise DispatchQueryError("The dispatch Parquet file could not be opened.") from exc
    names = set(dataset.schema.names)
    required = dispatch_columns_for(scenario, available=None)
    missing = [name for name in required if name not in names]
    if missing:
        raise DispatchQueryError("The dispatch Parquet file is missing required columns.")
    columns = list(dispatch_columns_for(scenario, available=names))
    start = utc_scalar(start_utc)
    end = utc_scalar(end_utc_exclusive)
    week_filter = (ds.field("timestamp_utc") >= start) & (ds.field("timestamp_utc") < end)
    try:
        table = dataset.to_table(filter=week_filter, columns=columns)
    except (OSError, pa.ArrowInvalid, TypeError, ValueError) as exc:
        raise DispatchQueryError("The selected week could not be read from the dispatch file.") from exc
    frame = table.to_pandas()
    return WeekQueryResult(
        frame=frame,
        n_rows=len(frame),
        columns=tuple(columns),
        start_utc=start_utc,
        end_utc_exclusive=end_utc_exclusive,
        parquet_identity=parquet_identity(path),
    )


def seasonal_windows(summary: Mapping[str, Any]) -> list[WeekWindow]:
    plots = summary.get("seasonal_plots")
    included = plots.get("included") if isinstance(plots, Mapping) else None
    if not isinstance(included, list) or not included:
        return []
    windows: list[WeekWindow] = []
    for item in included:
        if not isinstance(item, Mapping):
            continue
        season = str(item.get("season") or "")
        start_local = str(item.get("start_local") or "")
        end_local = str(item.get("end_local_exclusive") or "")
        start_utc = str(item.get("start_utc") or "")
        end_utc = str(item.get("end_utc_exclusive") or "")
        if not (season and start_utc and end_utc and start_local and end_local):
            continue
        iso_week = int(item.get("iso_week") or 0)
        iso_year = int(item.get("iso_year") or 0)
        windows.append(
            WeekWindow(
                iso_year=iso_year,
                iso_week=iso_week,
                start_local=start_local,
                end_local_exclusive=end_local,
                start_utc=start_utc,
                end_utc_exclusive=end_utc,
                label=(
                    f"{SEASON_LABELS.get(season, season)} · ISO week {iso_week:02d} "
                    f"({start_local[:10]} to {end_local[:10]})"
                ),
                source="seasonal_week",
                season=season,
            )
        )
    return windows


def iso_weeks_wholly_inside(start_local: str, end_local_exclusive: str) -> list[WeekWindow]:
    start = datetime.fromisoformat(start_local).astimezone(BRUSSELS)
    end = datetime.fromisoformat(end_local_exclusive).astimezone(BRUSSELS)
    day = start.date()
    if day.weekday() != 0:
        day = day + timedelta(days=(7 - day.weekday()))
    monday = datetime.combine(day, time.min, tzinfo=BRUSSELS)
    if monday < start:
        day = day + timedelta(days=7)
    weeks: list[WeekWindow] = []
    while True:
        week_start = datetime.combine(day, time.min, tzinfo=BRUSSELS)
        week_end = datetime.combine(day + timedelta(days=7), time.min, tzinfo=BRUSSELS)
        if week_end > end:
            break
        iso = week_start.isocalendar()
        weeks.append(
            WeekWindow(
                iso_year=iso.year,
                iso_week=iso.week,
                start_local=week_start.isoformat(),
                end_local_exclusive=week_end.isoformat(),
                start_utc=week_start.astimezone(timezone.utc).isoformat(),
                end_utc_exclusive=week_end.astimezone(timezone.utc).isoformat(),
                label=(
                    f"ISO week {iso.week:02d} "
                    f"({week_start.date().isoformat()} to {week_end.date().isoformat()})"
                ),
                source="choose_a_week",
            )
        )
        day = day + timedelta(days=7)
    return weeks


def week_interval_note(n_rows: int) -> str:
    if n_rows == ORDINARY_WEEK_INTERVALS:
        return f"{n_rows} physical quarter-hours is a normal week."
    if n_rows == SPRING_DST_WEEK_INTERVALS:
        return (
            f"{n_rows} physical quarter-hours is the spring clock-change week. "
            "The local clock moves forward, so this week contains one hour fewer."
        )
    if n_rows == AUTUMN_DST_WEEK_INTERVALS:
        return (
            f"{n_rows} physical quarter-hours is the autumn clock-change week. "
            "The local clock moves back, so this week contains one repeated hour."
        )
    return f"{n_rows} physical quarter-hours were returned for the selected week."


def local_naive(series: pd.Series) -> pd.Series:
    index = pd.DatetimeIndex(pd.to_datetime(series))
    if index.tz is not None:
        index = index.tz_localize(None)
    return pd.Series(index, index=series.index)


def to_kw(energy_kwh: pd.Series, interval_hours: pd.Series) -> pd.Series:
    return energy_kwh.astype(float) / interval_hours.astype(float)


def explorer_colour(key: str) -> str:
    return CHART_EXPLORER[key]


def explorer_chart_models(frame: pd.DataFrame, *, scenario: str) -> tuple[ChartSpec, ...]:
    hours = frame["interval_hours"]
    times = local_naive(frame["timestamp_local"])
    label = case_label(scenario)

    def _rows(series_map: Mapping[str, pd.Series]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, values in series_map.items():
            for stamp, value in zip(times.tolist(), values.to_numpy(), strict=False):
                rows.append({"Time": stamp, "Series": name, "Value": float(value)})
        return rows

    panels = [
        _chart(
            title="PV production and site use",
            x_title="Local time",
            y_title="Power (kW)",
            kind="line",
            value_format=",.2f",
            series_order=("PV production", "Site use"),
            colours={
                "PV production": explorer_colour("PV production"),
                "Site use": explorer_colour("Site use"),
            },
            rows=_rows(
                {
                    "PV production": to_kw(frame["pv_production_kwh"], hours),
                    "Site use": to_kw(frame["site_load_kwh"], hours),
                }
            ),
            x_type="time",
        ),
        _chart(
            title="Grid import and PV injection",
            x_title="Local time",
            y_title="Power (kW)",
            kind="line",
            value_format=",.2f",
            series_order=(
                "Grid import - no battery",
                f"Grid import - {label}",
                "PV injection - no battery",
                f"PV injection - {label}",
            ),
            colours={
                "Grid import - no battery": explorer_colour("Grid import - no battery"),
                f"Grid import - {label}": explorer_colour("Grid import - battery"),
                "PV injection - no battery": explorer_colour("PV injection - no battery"),
                f"PV injection - {label}": explorer_colour("PV injection - battery"),
            },
            rows=_rows(
                {
                    "Grid import - no battery": to_kw(frame["grid_import_baseline_kwh"], hours),
                    f"Grid import - {label}": to_kw(frame[f"{scenario}_grid_import_kwh"], hours),
                    "PV injection - no battery": -to_kw(frame["grid_export_baseline_kwh"], hours),
                    f"PV injection - {label}": -to_kw(frame[f"{scenario}_grid_export_kwh"], hours),
                }
            ),
            x_type="time",
        ),
    ]
    grid_col = f"{scenario}_discharge_grid_kwh"
    if grid_col in frame.columns:
        battery_map = {
            "Charging": to_kw(frame[f"{scenario}_charge_pv_kwh"], hours),
            "Discharge to customer": to_kw(frame[f"{scenario}_discharge_load_kwh"], hours),
            "Discharge to grid": to_kw(frame[grid_col], hours),
        }
        battery_order = ("Charging", "Discharge to customer", "Discharge to grid")
    else:
        battery_map = {
            "Charging": to_kw(frame[f"{scenario}_charge_pv_kwh"], hours),
            "Discharging": to_kw(frame[f"{scenario}_discharge_load_kwh"], hours),
        }
        battery_order = ("Charging", "Discharging")
    panels.append(
        _chart(
            title="Battery charging and discharging",
            x_title="Local time",
            y_title="Power (kW)",
            kind="line",
            value_format=",.2f",
            series_order=battery_order,
            colours={name: explorer_colour(name) for name in battery_order},
            rows=_rows(battery_map),
            x_type="time",
        )
    )
    panels.append(
        _chart(
            title="Stored energy",
            x_title="Local time",
            y_title="Stored energy (kWh)",
            kind="line",
            value_format=",.2f",
            series_order=("Stored energy",),
            colours={"Stored energy": explorer_colour("Stored energy")},
            rows=_rows({"Stored energy": frame[f"{scenario}_soc_end_kwh"].astype(float)}),
            x_type="time",
        )
    )
    if "da_price_eur_mwh" in frame.columns and scenario == "dynamic_injection":
        panels.append(
            _chart(
                title="Day-ahead injection price",
                x_title="Local time",
                y_title="Price (EUR/MWh)",
                kind="line",
                value_format=",.2f",
                series_order=("Day-ahead price",),
                colours={"Day-ahead price": explorer_colour("Day-ahead price")},
                rows=_rows({"Day-ahead price": frame["da_price_eur_mwh"].astype(float)}),
                x_type="time",
            )
        )
    return tuple(panels)


def week_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def week_caption(window: WeekWindow, *, n_rows: int, label: str) -> str:
    return (
        f"Local window: {window.start_local[:10]} to {window.end_local_exclusive[:10]}. "
        f"Case: {label}. Returned physical quarter-hours: {fmt_count(n_rows)}."
    )
