from datetime import datetime, timedelta, timezone

from btm_sim.fluvius.intervals import aware_candidates, resolve_utc_interval, utc_interval_matches
from tests.helpers import AUTUMN_STARTS, SPRING_STARTS, wall_clock

UTC = timezone.utc


def test_spring_forward_is_one_physical_quarter_hour():
    start = datetime(2024, 3, 31, 1, 45)
    end = datetime(2024, 3, 31, 3, 0)
    matches = utc_interval_matches(start, end)
    assert len(matches) == 1
    utc_start = matches[0][0].astimezone(UTC)
    utc_end = matches[0][1].astimezone(UTC)
    assert utc_start == datetime(2024, 3, 31, 0, 45, tzinfo=UTC)
    assert utc_end - utc_start == timedelta(minutes=15)


def test_spring_nonexistent_times_have_no_candidates():
    assert aware_candidates(datetime(2024, 3, 31, 2, 30)) == []


def test_autumn_duplicate_wall_clocks_are_two_utc_intervals():
    start = datetime(2024, 10, 27, 2, 0)
    end = datetime(2024, 10, 27, 2, 15)
    first = resolve_utc_interval(start, end, 0)
    second = resolve_utc_interval(start, end, 1)
    assert first is not None and second is not None
    assert first[0].astimezone(UTC) == datetime(2024, 10, 27, 0, 0, tzinfo=UTC)
    assert second[0].astimezone(UTC) == datetime(2024, 10, 27, 1, 0, tzinfo=UTC)
    assert first[0].utcoffset() == timedelta(hours=2)
    assert second[0].utcoffset() == timedelta(hours=1)


def test_autumn_fold_row_with_tot_0200_is_the_cest_occurrence():
    start = datetime(2024, 10, 27, 2, 45)
    end = datetime(2024, 10, 27, 2, 0)
    resolved = resolve_utc_interval(start, end, 0)
    assert resolved is not None
    assert resolved[0].astimezone(UTC) == datetime(2024, 10, 27, 0, 45, tzinfo=UTC)
    assert resolved[1].astimezone(UTC) == datetime(2024, 10, 27, 1, 0, tzinfo=UTC)


def test_fixture_wall_clocks_match_fluvius_dst_encoding():
    spring_wall = [wall_clock(ts)[1] for ts in SPRING_STARTS]
    assert spring_wall == ["01:00:00", "01:15:00", "01:30:00", "01:45:00", "03:00:00"]
    autumn_end = wall_clock(AUTUMN_STARTS[4] + timedelta(minutes=15))
    assert wall_clock(AUTUMN_STARTS[4])[1] == "02:45:00"
    assert autumn_end[1] == "02:00:00"
