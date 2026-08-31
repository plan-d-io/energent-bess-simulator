"""Hand-computable peak/off-peak classification, including DST."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from btm_sim.config.exceptions import ConfigError
from btm_sim.config.schema import TariffConfig, parse_hhmm
from btm_sim.settlement.tariffs import TARIFF_CLASS_OFFPEAK, TARIFF_CLASS_PEAK, classify_interval_starts
from tests.helpers import AUTUMN_STARTS, SPRING_STARTS
from tests.lp_frames import qh_frame

UTC = timezone.utc
BRUSSELS = ZoneInfo("Europe/Brussels")


def _classify(ts: datetime, tariffs: TariffConfig | None = None) -> pd.Series:
    tariffs = tariffs or TariffConfig()
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return classify_interval_starts([stamp], tariffs).iloc[0]


def test_weekday_boundaries_0745_0800_1945_2000():
    # Wednesday 2024-01-03 in Europe/Brussels (UTC+1).
    tariffs = TariffConfig()
    cases = {
        datetime(2024, 1, 3, 6, 45, tzinfo=UTC): TARIFF_CLASS_OFFPEAK,  # 07:45
        datetime(2024, 1, 3, 7, 0, tzinfo=UTC): TARIFF_CLASS_PEAK,  # 08:00 inclusive
        datetime(2024, 1, 3, 18, 45, tzinfo=UTC): TARIFF_CLASS_PEAK,  # 19:45
        datetime(2024, 1, 3, 19, 0, tzinfo=UTC): TARIFF_CLASS_OFFPEAK,  # 20:00 exclusive
    }
    for ts, expected in cases.items():
        row = _classify(ts, tariffs)
        assert row["tariff_class"] == expected, ts.astimezone(BRUSSELS)
        rate = 60.0 if expected == TARIFF_CLASS_PEAK else 30.0
        assert row["export_rate_eur_per_mwh"] == pytest.approx(rate)


def test_weekends_offpeak_true_makes_saturday_and_sunday_offpeak():
    tariffs = TariffConfig(weekends_offpeak=True)
    saturday_peak_hours = datetime(2024, 1, 6, 10, 0, tzinfo=UTC)  # 11:00 Saturday
    sunday_peak_hours = datetime(2024, 1, 7, 10, 0, tzinfo=UTC)  # 11:00 Sunday
    assert _classify(saturday_peak_hours, tariffs)["tariff_class"] == TARIFF_CLASS_OFFPEAK
    assert _classify(sunday_peak_hours, tariffs)["tariff_class"] == TARIFF_CLASS_OFFPEAK
    assert _classify(saturday_peak_hours, tariffs)["export_rate_eur_per_mwh"] == pytest.approx(30.0)


def test_weekends_offpeak_false_applies_peak_hours_on_weekend():
    tariffs = TariffConfig(weekends_offpeak=False)
    saturday_peak = datetime(2024, 1, 6, 10, 0, tzinfo=UTC)  # 11:00
    saturday_off = datetime(2024, 1, 6, 6, 0, tzinfo=UTC)  # 07:00
    assert _classify(saturday_peak, tariffs)["tariff_class"] == TARIFF_CLASS_PEAK
    assert _classify(saturday_off, tariffs)["tariff_class"] == TARIFF_CLASS_OFFPEAK
    assert _classify(saturday_peak, tariffs)["export_rate_eur_per_mwh"] == pytest.approx(60.0)


def test_configurable_peak_window():
    tariffs = TariffConfig(
        peak_start_local=parse_hhmm("09:15", name="peak_start"),
        peak_end_local=parse_hhmm("09:30", name="peak_end"),
        weekends_offpeak=False,
    )
    before = datetime(2024, 1, 3, 8, 0, tzinfo=UTC)  # 09:00
    start = datetime(2024, 1, 3, 8, 15, tzinfo=UTC)  # 09:15
    end = datetime(2024, 1, 3, 8, 30, tzinfo=UTC)  # 09:30
    assert _classify(before, tariffs)["tariff_class"] == TARIFF_CLASS_OFFPEAK
    assert _classify(start, tariffs)["tariff_class"] == TARIFF_CLASS_PEAK
    assert _classify(end, tariffs)["tariff_class"] == TARIFF_CLASS_OFFPEAK


def test_dst_spring_and_autumn_use_local_interval_start():
    tariffs = TariffConfig(weekends_offpeak=False)
    spring = classify_interval_starts(SPRING_STARTS, tariffs)
    local = [pd.Timestamp(ts).tz_convert("Europe/Brussels") for ts in SPRING_STARTS]
    assert [stamp.strftime("%H:%M") for stamp in local] == ["01:00", "01:15", "01:30", "01:45", "03:00"]
    assert list(spring["tariff_class"]) == [TARIFF_CLASS_OFFPEAK] * 5

    autumn = classify_interval_starts(AUTUMN_STARTS, tariffs)
    autumn_local = [pd.Timestamp(ts).tz_convert("Europe/Brussels") for ts in AUTUMN_STARTS]
    clocks = [stamp.strftime("%H:%M") for stamp in autumn_local]
    assert clocks[1] == "02:00"
    assert clocks[5] == "02:00"
    assert clocks[-1] == "03:00"
    assert list(autumn["tariff_class"]) == [TARIFF_CLASS_OFFPEAK] * len(AUTUMN_STARTS)


def test_overnight_peak_window_is_rejected():
    with pytest.raises(ConfigError, match="earlier"):
        TariffConfig(
            peak_start_local=parse_hhmm("20:00", name="peak_start"),
            peak_end_local=parse_hhmm("08:00", name="peak_end"),
        )


def test_classification_uses_frame_utc_starts():
    frame = qh_frame(
        [{"imp": 0.0, "exp": 1.0, "pv": 1.0}],
        start=datetime(2024, 1, 3, 7, 0, tzinfo=UTC),
    )
    row = classify_interval_starts(frame["timestamp_utc"], TariffConfig()).iloc[0]
    assert row["tariff_class"] == TARIFF_CLASS_PEAK
