"""Per-file Fluvius date-format detection and parsing."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from btm_sim.fluvius.intervals import classify_date_value, parse_naive
from btm_sim.fluvius.pipeline import ingest_fluvius, normalize_fluvius
from tests.helpers import AUTUMN_STARTS, SPRING_STARTS, balanced_site, qh_range, write_site

UTC = timezone.utc


def _normalize(tmp_path: Path, starts, **kwargs):
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv, **kwargs)
    return normalize_fluvius(paths, period="common", allow_unvalidated=True)


def _slash_first_row_dates(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    parts = lines[1].split(";")
    parts[0] = parts[0].replace("-", "/")
    parts[2] = parts[2].replace("-", "/")
    lines[1] = ";".join(parts)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rewrite_dates(path: Path, replacer) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    rewritten = [lines[0]]
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(";")
        parts[0] = replacer(parts[0])
        parts[2] = replacer(parts[2])
        rewritten.append(";".join(parts))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def test_classify_explicit_day_month_formats_only():
    assert classify_date_value("30-08-2023") == "DD-MM-YYYY"
    assert classify_date_value("29/01/2024") == "DD/MM/YYYY"
    assert classify_date_value("9/1/2024") == "DD/MM/YYYY"
    assert classify_date_value("2024-01-29") is None
    assert classify_date_value("01/13/2024") is None
    assert classify_date_value("13/13/2024") is None
    assert classify_date_value("32-01-2024") is None


def test_parse_naive_accepts_single_digit_and_padded_hours():
    hyphen = parse_naive("30-08-2023", "0:00:00", date_format="DD-MM-YYYY")
    slash = parse_naive("29/01/2024", "9:15", date_format="DD/MM/YYYY")
    padded = parse_naive("29/01/2024", "09:15:00", date_format="DD/MM/YYYY")
    assert hyphen == datetime(2023, 8, 30, 0, 0, 0)
    assert slash == datetime(2024, 1, 29, 9, 15, 0)
    assert padded == datetime(2024, 1, 29, 9, 15, 0)


def test_hyphen_format_file_is_detected_and_ingested(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    result = _normalize(tmp_path, starts)
    assert result.ok
    assert {source["date_format"] for source in result.report["sources"]} == {"DD-MM-YYYY"}
    assert {meta["date_format"] for meta in result.report["roles"].values()} == {"DD-MM-YYYY"}
    assert len(result.frame) == 4


def test_slash_format_file_is_detected_and_ingested(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    result = _normalize(tmp_path, starts, date_sep="/")
    assert result.ok
    assert {source["date_format"] for source in result.report["sources"]} == {"DD/MM/YYYY"}
    assert {meta["date_format"] for meta in result.report["roles"].values()} == {"DD/MM/YYYY"}
    assert len(result.frame) == 4
    assert list(result.frame["grid_import_baseline_kwh"]) == [1.0, 1.0, 1.0, 1.0]


def test_single_digit_and_zero_padded_hours(tmp_path: Path):
    starts = qh_range(datetime(2024, 1, 29, 23, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(
        tmp_path,
        starts,
        import_kwh=imp,
        export_kwh=exp,
        pv_kwh=pv,
        date_sep="/",
        pad_hours=False,
    )
    # Keep the first offtake row unpadded (`0:00:00`) and zero-pad a later time.
    lines = paths[0].read_text(encoding="utf-8").splitlines()
    parts = lines[2].split(";")
    parts[1] = "00:15:00"
    parts[3] = "00:30:00"
    lines[2] = ";".join(parts)
    paths[0].write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = normalize_fluvius(paths, period="common", allow_unvalidated=True)
    assert result.ok, result.report["fatal"]
    assert len(result.frame) == 4
    assert {source["date_format"] for source in result.report["sources"]} == {"DD/MM/YYYY"}


def test_mixed_date_formats_fail_with_file_name_and_examples(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)
    _slash_first_row_dates(paths[0])

    ingest = ingest_fluvius(paths)
    codes = [item.code for item in ingest.issues.fatals]
    assert codes == ["DATE_FORMAT_MIXED"]
    fatal = ingest.issues.fatals[0]
    assert "offtake.csv" in fatal.message
    assert "DD-MM-YYYY" in fatal.message
    assert "DD/MM/YYYY" in fatal.message
    assert "UNPARSEABLE_INTERVAL" not in codes
    offtake_source = next(source for source in ingest.sources if source["path"].endswith("offtake.csv"))
    assert offtake_source["date_format"] is None


def test_unsupported_iso_dates_fail_clearly(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)

    def to_iso(token: str) -> str:
        match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", token)
        assert match is not None
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"

    _rewrite_dates(paths[0], to_iso)
    ingest = ingest_fluvius(paths)
    fatal = next(item for item in ingest.issues.fatals if item.code == "DATE_FORMAT_UNSUPPORTED")
    assert "offtake.csv" in fatal.message
    assert "2024-06-01" in fatal.message
    assert all(item.code != "UNPARSEABLE_INTERVAL" for item in ingest.issues.fatals)


def test_us_month_first_dates_are_not_reinterpreted(tmp_path: Path):
    starts = qh_range(datetime(2024, 1, 13, 10, 0, tzinfo=UTC), 2)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv, date_sep="/")

    def to_us(token: str) -> str:
        match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", token)
        assert match is not None
        return f"{match.group(2)}/{match.group(1)}/{match.group(3)}"

    _rewrite_dates(paths[0], to_us)
    ingest = ingest_fluvius(paths)
    fatal = next(item for item in ingest.issues.fatals if item.code == "DATE_FORMAT_UNSUPPORTED")
    assert "offtake.csv" in fatal.message
    assert "01/13/2024" in fatal.message


def test_invalid_day_is_inconsistent_with_detected_format(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)
    lines = paths[0].read_text(encoding="utf-8").splitlines()
    parts = lines[1].split(";")
    parts[0] = "32-01-2024"
    lines[1] = ";".join(parts)
    paths[0].write_text("\n".join(lines) + "\n", encoding="utf-8")

    ingest = ingest_fluvius(paths)
    codes = [item.code for item in ingest.issues.fatals]
    assert codes == ["DATE_FORMAT_INCONSISTENT"]
    fatal = ingest.issues.fatals[0]
    assert "offtake.csv" in fatal.message
    assert "32-01-2024" in fatal.message
    assert "DD-MM-YYYY" in fatal.message


def test_slash_autumn_keeps_eight_distinct_utc_fold_intervals(tmp_path: Path):
    result = _normalize(tmp_path, AUTUMN_STARTS, date_sep="/")
    assert result.ok, result.report["fatal"]
    frame = result.frame.sort_values("timestamp_utc")
    fold = frame[
        (frame["timestamp_utc"] >= datetime(2024, 10, 27, 0, 0, tzinfo=UTC))
        & (frame["timestamp_utc"] < datetime(2024, 10, 27, 2, 0, tzinfo=UTC))
    ]
    assert len(fold) == 8
    assert fold["timestamp_utc"].is_unique
    locals_ = [ts.isoformat() for ts in fold["timestamp_local"]]
    assert locals_.count("2024-10-27T02:00:00+02:00") == 1
    assert locals_.count("2024-10-27T02:00:00+01:00") == 1
    assert locals_.count("2024-10-27T02:45:00+02:00") == 1
    assert locals_.count("2024-10-27T02:45:00+01:00") == 1
    assert result.report["dst"]["n_autumn_repeated_wall_clock"] >= 1


def test_slash_spring_skips_nonexistent_hour_without_inventing_rows(tmp_path: Path):
    result = _normalize(tmp_path, SPRING_STARTS, date_sep="/")
    assert result.ok, result.report["fatal"]
    frame = result.frame.sort_values("timestamp_utc")
    assert len(frame) == 5
    row = frame.loc[frame["timestamp_utc"] == datetime(2024, 3, 31, 0, 45, tzinfo=UTC)].iloc[0]
    assert row["timestamp_local"].isoformat() == "2024-03-31T01:45:00+01:00"
    next_local = frame.loc[frame["timestamp_utc"] == datetime(2024, 3, 31, 1, 0, tzinfo=UTC)].iloc[0]
    assert next_local["timestamp_local"].isoformat() == "2024-03-31T03:00:00+02:00"
    diffs = frame["timestamp_utc"].diff().iloc[1:]
    assert (diffs == pd.Timedelta(minutes=15)).all()
    assert result.report["dst"]["n_spring_skipped_wall_clock"] >= 1


def test_hyphen_dst_rows_still_resolve(tmp_path: Path):
    spring_dir = tmp_path / "spring"
    autumn_dir = tmp_path / "autumn"
    spring_dir.mkdir()
    autumn_dir.mkdir()
    spring = _normalize(spring_dir, SPRING_STARTS)
    autumn = _normalize(autumn_dir, AUTUMN_STARTS)
    assert spring.ok and autumn.ok
    assert len(spring.frame) == 5
    fold = autumn.frame[
        (autumn.frame["timestamp_utc"] >= datetime(2024, 10, 27, 0, 0, tzinfo=UTC))
        & (autumn.frame["timestamp_utc"] < datetime(2024, 10, 27, 2, 0, tzinfo=UTC))
    ]
    assert len(fold) == 8
