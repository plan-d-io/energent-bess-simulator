"""ISO-week selection for seasonal plots."""

from datetime import datetime, timezone

import pandas as pd

from btm_sim.compare.weeks import plot_filename, select_seasonal_weeks
from tests.lp_frames import qh_frame

UTC = timezone.utc


def _span(start: datetime, n: int) -> pd.DataFrame:
    rows = [{"imp": 1.0, "exp": 0.2, "pv": 0.5} for _ in range(n)]
    return qh_frame(rows, start=start)


def test_partial_period_omits_incomplete_target_weeks():
    # Two quarter-hours in June 2024: none of the four target weeks.
    frame = _span(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    weeks = select_seasonal_weeks(frame)
    assert weeks["included"] == []
    assert weeks["omitted_seasons"] == ["winter", "spring", "summer", "autumn"]


def test_complete_winter_week_is_selected_and_named_deterministically():
    # Monday 15 Jan 2024 00:00 Europe/Brussels = 14 Jan 23:00 UTC; 672 QH.
    frame = _span(datetime(2024, 1, 14, 23, 0, tzinfo=UTC), 7 * 96)
    weeks = select_seasonal_weeks(frame)
    assert [item["season"] for item in weeks["included"]] == ["winter"]
    assert weeks["included"][0]["iso_week"] == 3
    assert weeks["included"][0]["iso_year"] == 2024
    assert weeks["omitted_seasons"] == ["spring", "summer", "autumn"]
    assert plot_filename("self_consumption", "winter", 3) == "self_consumption_winter_week03.png"
    assert plot_filename("peak_reduction", "winter", 3) == "peak_reduction_winter_week03.png"
    assert plot_filename("revenue", "winter", 3) == "revenue_winter_week03.png"


def test_incomplete_winter_week_is_omitted():
    frame = _span(datetime(2024, 1, 14, 23, 0, tzinfo=UTC), 96)  # one day only
    weeks = select_seasonal_weeks(frame)
    assert weeks["included"] == []
    assert "winter" in weeks["omitted_seasons"]
