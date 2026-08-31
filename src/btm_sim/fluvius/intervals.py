"""Convert Fluvius local wall-clock intervals to physical UTC quarter-hours."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from btm_sim.fluvius.constants import INTERVAL, TZ, TZ_NAME
from btm_sim.fluvius.issues import IssueLog

UTC = timezone.utc

# Explicit day-month-year formats only. Never infer, never month-first.
DATE_FORMATS = {
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
}
DATE_FORMAT_COLUMN = "_fluvius_date_format"
TIME_FORMATS = ("%H:%M:%S", "%H:%M")
DATE_FORMAT_CODES = frozenset(
    {
        "DATE_FORMAT_EMPTY",
        "DATE_FORMAT_MIXED",
        "DATE_FORMAT_UNSUPPORTED",
        "DATE_FORMAT_INCONSISTENT",
    }
)
_EMPTY_DATE_TOKENS = frozenset({"", "nan", "none"})
_EXAMPLE_LIMIT = 5


def collect_date_values(frame: pd.DataFrame) -> list[str]:
    """Unique non-empty `Van (datum)` and `Tot (datum)` values, in file order."""
    parts: list[pd.Series] = []
    for column in ("Van (datum)", "Tot (datum)"):
        if column not in frame.columns:
            continue
        series = frame[column].astype(str).str.strip()
        series = series[~series.str.lower().isin(_EMPTY_DATE_TOKENS)]
        parts.append(series)
    if not parts:
        return []
    return [str(value) for value in pd.concat(parts, ignore_index=True).unique()]


def classify_date_value(text: str) -> str | None:
    """Return the explicit Fluvius date-format label, or None if unsupported."""
    token = (text or "").strip()
    if not token or token.lower() in _EMPTY_DATE_TOKENS:
        return None
    matches = [label for label, fmt in DATE_FORMATS.items() if _matches_explicit_format(token, fmt)]
    if len(matches) == 1:
        return matches[0]
    return None


def _matches_explicit_format(token: str, strptime_fmt: str) -> bool:
    try:
        datetime.strptime(token, strptime_fmt)
    except ValueError:
        return False
    return True


def detect_date_format(
    values: list[str],
    issues: IssueLog,
    *,
    path: str | Path,
) -> str | None:
    """Require one supported date format across a Fluvius file's date columns."""
    path = Path(path)
    non_empty = [str(value).strip() for value in values if str(value).strip() and str(value).strip().lower() not in _EMPTY_DATE_TOKENS]
    if not non_empty:
        issues.fatal(
            "DATE_FORMAT_EMPTY",
            (
                f"{path.name} has no non-empty Van (datum) or Tot (datum) values "
                f"from which to detect DD-MM-YYYY or DD/MM/YYYY"
            ),
            path=str(path),
        )
        return None

    by_label: dict[str, list[str]] = {label: [] for label in DATE_FORMATS}
    invalid: list[str] = []
    seen: set[str] = set()
    for token in non_empty:
        if token in seen:
            continue
        seen.add(token)
        label = classify_date_value(token)
        if label is None:
            invalid.append(token)
        else:
            by_label[label].append(token)

    present = [label for label, examples in by_label.items() if examples]
    if len(present) > 1:
        mixed_examples = {label: examples[:_EXAMPLE_LIMIT] for label, examples in by_label.items() if examples}
        issues.fatal(
            "DATE_FORMAT_MIXED",
            (
                f"{path.name} mixes date formats {present[0]} and {present[1]}. "
                f"Each Fluvius file must use one format. "
                f"Examples: {mixed_examples}"
            ),
            path=str(path),
            formats=present,
            examples=mixed_examples,
        )
        return None
    if invalid and not present:
        examples = invalid[:_EXAMPLE_LIMIT]
        issues.fatal(
            "DATE_FORMAT_UNSUPPORTED",
            (
                f"{path.name} uses an unsupported date format. "
                f"Expected DD-MM-YYYY or DD/MM/YYYY. Examples: {examples}"
            ),
            path=str(path),
            examples=examples,
        )
        return None
    if invalid and present:
        detected = present[0]
        examples = invalid[:_EXAMPLE_LIMIT]
        issues.fatal(
            "DATE_FORMAT_INCONSISTENT",
            (
                f"{path.name} has date values that do not match the detected "
                f"{detected} format. Examples: {examples}"
            ),
            path=str(path),
            date_format=detected,
            examples=examples,
        )
        return None
    return present[0]


def parse_naive(date_text: str, time_text: str, *, date_format: str) -> datetime | None:
    date_text = (date_text or "").strip()
    time_text = (time_text or "").strip()
    if not date_text or not time_text:
        return None
    date_fmt = DATE_FORMATS.get(date_format)
    if date_fmt is None:
        return None
    combined = f"{date_text} {time_text}"
    for time_fmt in TIME_FORMATS:
        try:
            return datetime.strptime(combined, f"{date_fmt} {time_fmt}")
        except ValueError:
            continue
    return None


def _empty_converted_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["timestamp_utc"] = pd.NaT
    out["timestamp_local"] = pd.NaT
    out.attrs["n_spring_skipped_wall_clock"] = 0
    out.attrs["n_autumn_repeated_wall_clock"] = 0
    return out


def _date_format_from_frame(frame: pd.DataFrame) -> str | None:
    if DATE_FORMAT_COLUMN not in frame.columns:
        return None
    unique = {str(value) for value in frame[DATE_FORMAT_COLUMN].tolist() if value and str(value) not in _EMPTY_DATE_TOKENS}
    if len(unique) == 1:
        return next(iter(unique))
    return None


def _round_trip_local(naive: datetime, fold: int) -> datetime | None:
    attached = naive.replace(tzinfo=TZ, fold=fold)
    back = attached.astimezone(UTC).astimezone(TZ)
    if back.replace(tzinfo=None) != naive:
        return None
    return attached


def aware_candidates(naive: datetime) -> list[datetime]:
    """Possible Europe/Brussels instants for a naive local wall time.

    Nonexistent spring-forward times yield an empty list. Ambiguous autumn
    times yield fold=0 (CEST) then fold=1 (CET).
    """
    first = _round_trip_local(naive, fold=0)
    if first is None:
        return []
    second = naive.replace(tzinfo=TZ, fold=1)
    if second.utcoffset() == first.utcoffset():
        return [first]
    if _round_trip_local(naive, fold=1) is None:
        return [first]
    return [first, second]


def utc_interval_matches(start_naive: datetime, end_naive: datetime) -> list[tuple[datetime, datetime]]:
    """Pairs of local-aware start/end whose UTC span is exactly 15 minutes.

    Datetimes that share the same ZoneInfo tzinfo subtract as naive wall-clock
    values, so DST-crossing rows must be compared after conversion to UTC.
    """
    matches: list[tuple[datetime, datetime]] = []
    for start in aware_candidates(start_naive):
        for end in aware_candidates(end_naive):
            if end.astimezone(UTC) - start.astimezone(UTC) == INTERVAL:
                matches.append((start, end))
    matches.sort(key=lambda pair: pair[0].astimezone(UTC))
    return matches


def resolve_utc_interval(
    start_naive: datetime,
    end_naive: datetime,
    occurrence_index: int,
) -> tuple[datetime, datetime] | None:
    """Resolve one Fluvius row to a unique UTC interval.

    Duplicate autumn wall-clock rows (02:00/02:15/02:30 with identical start
    and end) are disambiguated by row order: the first occurrence is CEST,
    the second is CET. Spring 01:45–03:00 is a single 15-minute UTC interval
    because that is the only pair whose UTC span is 15 minutes.
    """
    matches = utc_interval_matches(start_naive, end_naive)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if occurrence_index < 0 or occurrence_index >= len(matches):
        return None
    return matches[occurrence_index]


def convert_series_intervals(
    frame: pd.DataFrame,
    issues: IssueLog,
    *,
    role: str,
    date_format: str | None = None,
) -> pd.DataFrame:
    """Add timestamp_utc / timestamp_local to a single-register Fluvius series."""
    if date_format is None:
        date_format = _date_format_from_frame(frame)
    if date_format is None:
        if DATE_FORMAT_COLUMN in frame.columns:
            unique = {str(value) for value in frame[DATE_FORMAT_COLUMN].tolist() if value and str(value) not in _EMPTY_DATE_TOKENS}
            if len(unique) > 1:
                path = frame["source_path"].iloc[0] if "source_path" in frame.columns and len(frame) else "<memory>"
                issues.fatal(
                    "DATE_FORMAT_MIXED",
                    f"{role} mixes date formats {sorted(unique)}",
                    role=role,
                    path=str(path),
                    formats=sorted(unique),
                )
            return _empty_converted_frame(frame)
        path = frame["source_path"].iloc[0] if "source_path" in frame.columns and len(frame) else "<memory>"
        date_format = detect_date_format(collect_date_values(frame), issues, path=path)
        if date_format is None:
            return _empty_converted_frame(frame)

    van_date = frame["Van (datum)"].tolist()
    van_time = frame["Van (tijdstip)"].tolist()
    tot_date = frame["Tot (datum)"].tolist()
    tot_time = frame["Tot (tijdstip)"].tolist()
    source_path = frame["source_path"].tolist()
    source_row = frame["source_row"].tolist()

    utc_starts: list[datetime | None] = []
    occurrence: dict[tuple[datetime, datetime], int] = {}
    n_autumn_repeated = 0
    n_spring_skipped = 0
    n_unparseable = 0

    for i in range(len(frame)):
        start_naive = parse_naive(van_date[i], van_time[i], date_format=date_format)
        end_naive = parse_naive(tot_date[i], tot_time[i], date_format=date_format)
        if start_naive is None or end_naive is None:
            n_unparseable += 1
            if n_unparseable <= 5:
                issues.fatal(
                    "UNPARSEABLE_INTERVAL",
                    f"Unparseable {role} interval boundaries at {source_path[i]} row {source_row[i]}",
                    role=role,
                    van_datum=van_date[i],
                    van_tijdstip=van_time[i],
                    tot_datum=tot_date[i],
                    tot_tijdstip=tot_time[i],
                )
            utc_starts.append(None)
            continue

        key = (start_naive, end_naive)
        occ = occurrence.get(key, 0)
        occurrence[key] = occ + 1
        resolved = resolve_utc_interval(start_naive, end_naive, occ)
        if resolved is None:
            n_unparseable += 1
            if n_unparseable <= 5:
                issues.fatal(
                    "UNPARSEABLE_INTERVAL",
                    (
                        f"Cannot resolve {role} local interval "
                        f"{start_naive.isoformat()}–{end_naive.isoformat()} "
                        f"to a 15-minute UTC quarter-hour"
                    ),
                    role=role,
                    occurrence_index=occ,
                    path=source_path[i],
                    source_row=int(source_row[i]),
                )
            utc_starts.append(None)
            continue

        start_aware, end_aware = resolved
        utc_delta = end_aware.astimezone(UTC) - start_aware.astimezone(UTC)
        if utc_delta != INTERVAL:
            issues.fatal(
                "INTERVAL_NOT_QUARTER_HOUR",
                f"{role} interval is {utc_delta} after timezone conversion, not 15 minutes",
                role=role,
            )
            utc_starts.append(None)
            continue

        wall_delta = end_naive - start_naive
        if wall_delta == timedelta(minutes=75):
            n_spring_skipped += 1
        elif wall_delta <= timedelta(0) or occ > 0:
            n_autumn_repeated += 1

        utc_starts.append(start_aware.astimezone(UTC))

    if n_unparseable > 5:
        issues.fatal(
            "UNPARSEABLE_INTERVAL",
            f"{n_unparseable} unparseable {role} intervals (first 5 listed)",
            role=role,
            count=n_unparseable,
        )

    out = frame.copy()
    out["timestamp_utc"] = pd.to_datetime(utc_starts, utc=True, errors="coerce")
    out["timestamp_local"] = out["timestamp_utc"].dt.tz_convert(TZ_NAME)
    out.attrs["n_spring_skipped_wall_clock"] = n_spring_skipped
    out.attrs["n_autumn_repeated_wall_clock"] = n_autumn_repeated
    return out


def assert_unique_utc(timestamps: pd.Series, issues: IssueLog, *, role: str) -> None:
    valid = timestamps.dropna()
    if valid.empty:
        issues.fatal("EMPTY_SERIES", f"{role} series has no parseable intervals", role=role)
        return
    duplicated = valid[valid.duplicated()]
    if not duplicated.empty:
        issues.fatal(
            "DUPLICATE_UTC",
            f"{role} has {int(duplicated.shape[0])} duplicate canonical UTC interval starts",
            role=role,
            examples=[ts.isoformat() for ts in duplicated.iloc[:5]],
        )
