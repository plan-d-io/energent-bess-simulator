"""Local-month grouping, DST completeness, and average-monthly-peak rules."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.metrics import attach_baseline_dispatch, average_monthly_peak_payload, scenario_metrics
from btm_sim.compare.months import (
    LocalMonthWindow,
    expected_month_quarter_hours,
    local_month_coverage,
    month_bounds,
)
from tests.lp_frames import qh_frame

UTC = timezone.utc


def test_expected_dst_month_counts_are_not_days_times_96():
    assert expected_month_quarter_hours(2024, 3) == 2972
    assert expected_month_quarter_hours(2024, 3) != 31 * 96
    assert expected_month_quarter_hours(2024, 10) == 2980
    assert expected_month_quarter_hours(2024, 10) != 31 * 96
    assert expected_month_quarter_hours(2024, 1) == 31 * 96
    assert expected_month_quarter_hours(2024, 2) == 29 * 96


def test_march_and_october_complete_months_use_physical_interval_counts():
    for month, expected in ((3, 2972), (10, 2980)):
        frame = complete_month_frame(2024, month)
        coverage = local_month_coverage(frame)
        assert len(coverage) == 1
        window = coverage[0]
        assert window.month == f"2024-{month:02d}"
        assert window.expected_n_intervals == expected
        assert window.n_intervals == expected
        assert window.complete is True


def test_rows_group_by_local_month_not_utc_month():
    frame = qh_frame(
        [
            {"imp": 1.0, "exp": 0.0, "pv": 0.0, "ts": datetime(2024, 6, 30, 21, 45, tzinfo=UTC)},
            {"imp": 2.0, "exp": 0.0, "pv": 0.0, "ts": datetime(2024, 6, 30, 22, 0, tzinfo=UTC)},
        ]
    )
    assert pd.Timestamp(frame["timestamp_utc"].iloc[1]).tz_convert("UTC").month == 6
    assert str(frame["timestamp_local"].iloc[1])[:10] == "2024-07-01"
    coverage = local_month_coverage(frame)
    assert [window.month for window in coverage] == ["2024-06", "2024-07"]
    assert all(window.complete is False for window in coverage)
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    metrics = scenario_metrics(attach_baseline_dispatch(frame, cfg), cfg, scenario="no_battery")
    assert set(metrics["monthly_peaks_kw"]) == {"2024-06", "2024-07"}
    assert metrics["monthly_peaks_kw"]["2024-06"] == pytest.approx(4.0)
    assert metrics["monthly_peaks_kw"]["2024-07"] == pytest.approx(8.0)


def test_partial_month_is_flagged_and_excluded_from_average():
    frame = qh_frame(
        [
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    coverage = local_month_coverage(frame)
    assert coverage[0].month == "2024-06"
    assert coverage[0].complete is False
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    metrics = scenario_metrics(attach_baseline_dispatch(frame, cfg), cfg, scenario="no_battery")
    assert metrics["average_monthly_peak_n_complete_months"] == 0
    assert metrics["average_monthly_peak_kw"] is None
    assert metrics["average_monthly_peak_reduction_kw"] is None
    assert metrics["average_monthly_peak_reduction_pct"] is None
    assert metrics["annual_peak_kw"] == pytest.approx(8.0)


def test_complete_year_average_equals_mean_of_twelve_monthly_peaks():
    peaks = {f"2024-{month:02d}": float(month) for month in range(1, 13)}
    coverage = [window_stub(f"2024-{month:02d}", complete=True) for month in range(1, 13)]
    payload = average_monthly_peak_payload(peaks, peaks, coverage)
    assert payload["average_monthly_peak_n_complete_months"] == 12
    assert payload["average_monthly_peak_kw"] == pytest.approx(sum(range(1, 13)) / 12)
    assert payload["average_monthly_peak_reduction_kw"] == pytest.approx(0.0)
    assert payload["average_monthly_peak_reduction_pct"] == pytest.approx(0.0)


def test_partial_edges_keep_a_complete_middle_month_in_the_average():
    june_partial = qh_frame(
        [{"imp": 3.0, "exp": 0.0, "pv": 0.0, "ts": datetime(2024, 6, 30, 21, 45, tzinfo=UTC)}]
    )
    july = complete_month_frame(2024, 7, import_kwh=0.5, peak_import_kwh=2.0)
    august_partial = qh_frame(
        [{"imp": 4.0, "exp": 0.0, "pv": 0.0, "ts": datetime(2024, 8, 1, 0, 0, tzinfo=UTC)}]
    )
    frame = pd.concat([june_partial, july, august_partial], ignore_index=True)
    coverage = local_month_coverage(frame)
    assert [window.month for window in coverage] == ["2024-06", "2024-07", "2024-08"]
    assert [window.complete for window in coverage] == [False, True, False]
    cfg = BatteryConfig(10, 8, 8, 1.0, 1.0, soc_initial_kwh=0.0)
    metrics = scenario_metrics(attach_baseline_dispatch(frame, cfg), cfg, scenario="no_battery")
    assert metrics["average_monthly_peak_n_complete_months"] == 1
    assert metrics["average_monthly_peak_kw"] == pytest.approx(metrics["monthly_peaks_kw"]["2024-07"])
    assert metrics["annual_peak_kw"] == pytest.approx(max(metrics["monthly_peaks_kw"].values()))


def complete_month_frame(
    year: int,
    month: int,
    *,
    import_kwh: float = 0.25,
    export_kwh: float = 0.0,
    pv_kwh: float = 0.5,
    peak_import_kwh: float | None = None,
    peak_offset: int = 0,
) -> pd.DataFrame:
    _, _, start_utc, end_utc = month_bounds(year, month)
    utc = pd.date_range(start_utc, end_utc, freq="15min", inclusive="left")
    n = len(utc)
    imp = pd.Series(import_kwh, index=range(n), dtype=float)
    if peak_import_kwh is not None:
        imp.iloc[peak_offset] = peak_import_kwh
    exp = pd.Series(export_kwh, index=range(n), dtype=float)
    pv = pd.Series(pv_kwh, index=range(n), dtype=float)
    return pd.DataFrame(
        {
            "timestamp_utc": utc.tz_convert("UTC"),
            "timestamp_local": utc.tz_convert("Europe/Brussels"),
            "interval_hours": 0.25,
            "grid_import_baseline_kwh": imp.to_numpy(),
            "grid_export_baseline_kwh": exp.to_numpy(),
            "pv_production_kwh": pv.to_numpy(),
            "site_load_kwh": (pv + imp - exp).to_numpy(),
            "offtake_quality": "validated",
            "injection_quality": "validated",
            "pv_quality": "validated",
            "quality_flag": "validated",
            "pv_source": "measured_fluvius",
        }
    )


def window_stub(month: str, *, complete: bool) -> LocalMonthWindow:
    year = int(month[:4])
    month_number = int(month[5:7])
    start_local, end_local, start_utc, end_utc = month_bounds(year, month_number)
    expected = expected_month_quarter_hours(year, month_number)
    return LocalMonthWindow(
        month=month,
        year=year,
        month_number=month_number,
        start_local=start_local,
        end_local=end_local,
        start_utc=start_utc,
        end_utc=end_utc,
        expected_n_intervals=expected,
        n_intervals=expected if complete else 1,
        complete=complete,
    )
