from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from btm_sim.fluvius.constants import MATERIAL_IMBALANCE_KWH
from btm_sim.fluvius.pipeline import ingest_fluvius, materialize_period, normalize_fluvius
from tests.helpers import AUTUMN_STARTS, SPRING_STARTS, balanced_site, qh_range, write_site

UTC = timezone.utc


def _normalize(tmp_path: Path, starts, **kwargs):
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv, **kwargs)
    return normalize_fluvius(paths, period="common", allow_unvalidated=True)


def test_semicolon_comma_decimals_and_status(tmp_path: Path):
    starts = qh_range(datetime(2024, 1, 1, 23, 0, tzinfo=UTC), 3)
    result = _normalize(tmp_path, starts)
    assert result.ok
    frame = result.frame
    assert list(frame["grid_import_baseline_kwh"]) == [1.0, 1.0, 1.0]
    assert frame["site_load_kwh"].tolist() == pytest.approx([1.5, 1.5, 1.5])
    assert set(frame["quality_flag"]) == {"validated"}


def test_autumn_keeps_eight_distinct_utc_fold_intervals(tmp_path: Path):
    result = _normalize(tmp_path, AUTUMN_STARTS)
    assert result.ok
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
    assert result.report["dst"]["transitions"] == [
        {
            "date_local": "2024-10-27",
            "kind": "autumn_backward",
            "utc_offset_before": "+02:00",
            "utc_offset_after": "+01:00",
            "physical_quarter_hours_in_local_day": 100,
        }
    ]


def test_spring_skips_nonexistent_hour_without_inventing_rows(tmp_path: Path):
    result = _normalize(tmp_path, SPRING_STARTS)
    assert result.ok
    frame = result.frame.sort_values("timestamp_utc")
    assert len(frame) == 5
    row = frame.loc[frame["timestamp_utc"] == datetime(2024, 3, 31, 0, 45, tzinfo=UTC)].iloc[0]
    assert row["timestamp_local"].isoformat() == "2024-03-31T01:45:00+01:00"
    next_local = frame.loc[frame["timestamp_utc"] == datetime(2024, 3, 31, 1, 0, tzinfo=UTC)].iloc[0]
    assert next_local["timestamp_local"].isoformat() == "2024-03-31T03:00:00+02:00"
    diffs = frame["timestamp_utc"].diff().iloc[1:]
    assert (diffs == pd.Timedelta(minutes=15)).all()
    assert result.report["dst"]["transitions"] == [
        {
            "date_local": "2024-03-31",
            "kind": "spring_forward",
            "utc_offset_before": "+01:00",
            "utc_offset_after": "+02:00",
            "physical_quarter_hours_in_local_day": 92,
        }
    ]


def test_utc_index_is_unique_increasing_and_gap_free(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 8)
    result = _normalize(tmp_path, starts)
    utc = result.frame["timestamp_utc"]
    assert utc.is_unique
    assert utc.is_monotonic_increasing
    assert (utc.diff().iloc[1:] == pd.Timedelta(minutes=15)).all()


def test_gaps_are_fatal_and_never_zero_filled(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 5)
    del starts[2]
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)
    ingest = ingest_fluvius(paths)
    commons = [offer for offer in ingest.periods if offer.kind == "common_overlap"]
    assert len(commons) == 2
    result = materialize_period(ingest, "common")
    assert not result.ok
    assert any(item.code == "UNKNOWN_PERIOD" for item in result.issues.fatals)


def test_null_reading_in_selected_period_is_fatal(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    pv[1] = None
    statuses = ["Gevalideerd", "Geen gegevens", "Gevalideerd", "Gevalideerd"]
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv, statuses=statuses)
    ingest = ingest_fluvius(paths)
    assert len([offer for offer in ingest.periods if offer.kind == "common_overlap"]) == 2
    result = normalize_fluvius(paths, period="common")
    assert not result.ok


def test_reconstructed_load_identity(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    paths = write_site(
        tmp_path,
        starts,
        import_kwh=[2.0, 0.0],
        export_kwh=[0.0, 1.5],
        pv_kwh=[0.5, 2.0],
    )
    result = normalize_fluvius(paths, period="common")
    assert result.ok
    load = result.frame["site_load_kwh"].tolist()
    assert load == pytest.approx([2.5, 0.5])


def test_material_negative_load_is_not_clipped(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    paths = write_site(
        tmp_path,
        starts,
        import_kwh=[0.0, 0.0],
        export_kwh=[2.0, 2.0],
        pv_kwh=[0.0, 0.0],
    )
    result = normalize_fluvius(paths, period="common")
    assert not result.ok
    assert any(item.code == "NEGATIVE_LOAD" for item in result.issues.fatals)
    assert result.frame is None


def test_simultaneous_import_export_is_informational_not_blocking(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    paths = write_site(
        tmp_path,
        starts,
        import_kwh=[1.0, 1.0],
        export_kwh=[0.5, 0.5],
        pv_kwh=[2.0, 2.0],
    )
    result = normalize_fluvius(paths, period="common")
    assert result.ok
    assert result.frame is not None
    assert result.frame["grid_import_baseline_kwh"].tolist() == pytest.approx([1.0, 1.0])
    assert result.frame["grid_export_baseline_kwh"].tolist() == pytest.approx([0.5, 0.5])
    assert result.frame["site_load_kwh"].tolist() == pytest.approx([2.5, 2.5])
    assert all(item.code != "SIMULTANEOUS_IMPORT_EXPORT" for item in result.issues.fatals)
    assert all(item.code != "SIMULTANEOUS_IMPORT_EXPORT" for item in result.issues.warnings)
    diagnostic = result.report["simultaneous_import_export"]
    assert diagnostic["n_intervals"] == 2
    assert diagnostic["threshold_kwh"] == pytest.approx(MATERIAL_IMBALANCE_KWH)
    assert diagnostic["n_intervals"] == int(
        (
            (result.frame["grid_import_baseline_kwh"] > diagnostic["threshold_kwh"])
            & (result.frame["grid_export_baseline_kwh"] > diagnostic["threshold_kwh"])
        ).sum()
    )


def test_material_negative_load_still_requires_site_boundary_ack(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    paths = write_site(
        tmp_path,
        starts,
        import_kwh=[0.0, 0.0],
        export_kwh=[2.0, 2.0],
        pv_kwh=[0.0, 0.0],
    )
    blocked = normalize_fluvius(paths, period="common")
    assert not blocked.ok
    assert any(item.code == "NEGATIVE_LOAD" for item in blocked.issues.fatals)
    allowed = normalize_fluvius(paths, period="common", acknowledge_site_boundary=True)
    assert allowed.ok
    assert any(item.code == "NEGATIVE_LOAD" for item in allowed.issues.warnings)
    assert (allowed.frame["site_load_kwh"] < 0).any()


def test_export_above_pv_still_requires_site_boundary_ack(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    paths = write_site(
        tmp_path,
        starts,
        import_kwh=[1.5, 1.5],
        export_kwh=[2.0, 2.0],
        pv_kwh=[1.0, 1.0],
    )
    blocked = normalize_fluvius(paths, period="common")
    assert not blocked.ok
    assert any(item.code == "EXPORT_EXCEEDS_PV" for item in blocked.issues.fatals)
    allowed = normalize_fluvius(paths, period="common", acknowledge_site_boundary=True)
    assert allowed.ok
    assert any(item.code == "EXPORT_EXCEEDS_PV" for item in allowed.issues.warnings)
    assert allowed.frame["grid_export_baseline_kwh"].tolist() == pytest.approx([2.0, 2.0])
    assert allowed.frame["pv_production_kwh"].tolist() == pytest.approx([1.0, 1.0])


def test_unvalidated_requires_explicit_option(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 3)
    statuses = ["Gevalideerd", "Ongevalideerd", "Gevalideerd"]
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv, statuses=statuses)
    blocked = normalize_fluvius(paths, period="common", allow_unvalidated=False)
    assert not blocked.ok
    assert any(item.code == "UNVALIDATED_NOT_ALLOWED" for item in blocked.issues.fatals)
    allowed = normalize_fluvius(paths, period="common", allow_unvalidated=True)
    assert allowed.ok
    assert allowed.report["unvalidated_policy"]["allow_unvalidated"] is True
    assert allowed.report["unvalidated_policy"]["acknowledged"] is True
    assert int((allowed.frame["quality_flag"] == "unvalidated").sum()) == 1
    assert any(item.code == "UNVALIDATED_USED" for item in allowed.issues.warnings)


def test_ean_mismatch_does_not_block_join(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    result = _normalize(
        tmp_path,
        starts,
        offtake_ean="541400000000000001",
        injection_ean="541400000000000002",
        pv_ean="541400000000000003",
    )
    assert result.ok
    assert any(item.code == "EAN_MISMATCH" for item in result.issues.warnings)
    assert len(result.frame) == 2


def test_full_versus_partial_calendar_years(tmp_path: Path):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Brussels")
    start = datetime(2023, 12, 31, 0, 0, tzinfo=tz).astimezone(UTC)
    starts = qh_range(start, 100)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)
    ingest = ingest_fluvius(paths)
    by_id = {offer.id: offer for offer in ingest.periods}
    assert by_id["2023"].kind == "partial_calendar_year"
    assert by_id["2023"].complete_calendar_year is False
    assert by_id["2024"].kind == "partial_calendar_year"
    assert "common" in by_id
    assert all(offer.kind != "full_calendar_year" for offer in ingest.periods)


def test_rolling_twelve_months_when_no_complete_year():
    from zoneinfo import ZoneInfo

    from btm_sim.fluvius.periods import discover_periods

    tz = ZoneInfo("Europe/Brussels")
    start = datetime(2023, 3, 1, 0, 0, tzinfo=tz).astimezone(UTC)
    n = int(timedelta(days=400) / timedelta(minutes=15))
    frame = pd.DataFrame(
        {
            "timestamp_utc": pd.date_range(start, periods=n, freq="15min", tz="UTC"),
            "quality_flag": "validated",
        }
    )
    offers = discover_periods(frame)
    kinds = {offer.kind for offer in offers}
    assert "full_calendar_year" not in kinds
    assert "rolling_twelve_months" in kinds
    by_id = {offer.id: offer for offer in offers}
    assert by_id["2023"].kind == "partial_calendar_year"
    assert by_id["2024"].kind == "partial_calendar_year"
    assert by_id["2023"].complete_calendar_year is False
    assert by_id["2024"].complete_calendar_year is False
