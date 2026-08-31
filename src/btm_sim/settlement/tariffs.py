"""Classify quarter-hours into peak and off-peak export rates."""

from __future__ import annotations

import pandas as pd

from btm_sim.config.schema import TariffConfig, format_hhmm

TARIFF_CLASS_PEAK = "peak"
TARIFF_CLASS_OFFPEAK = "offpeak"


def classify_interval_starts(timestamps, tariffs: TariffConfig) -> pd.DataFrame:
    """Return tariff class and export rate for each interval start.

    Classification uses the interval start in the configured timezone.
    Peak hours are inclusive at ``peak_start_local`` and exclusive at
    ``peak_end_local``. Saturday and Sunday are off-peak when
    ``weekends_offpeak`` is true.
    """
    local = _local_index(timestamps, tariffs)
    peak_start = tariffs.peak_start_local
    peak_end = tariffs.peak_end_local
    classes: list[str] = []
    rates: list[float] = []
    for stamp in local:
        ts = pd.Timestamp(stamp)
        clock = ts.time()
        weekday = int(ts.weekday())
        weekend = weekday >= 5
        in_peak_hours = peak_start <= clock < peak_end
        if tariffs.weekends_offpeak and weekend:
            is_peak = False
        else:
            is_peak = in_peak_hours
        if is_peak:
            classes.append(TARIFF_CLASS_PEAK)
            rates.append(float(tariffs.peak_export_eur_per_mwh))
        else:
            classes.append(TARIFF_CLASS_OFFPEAK)
            rates.append(float(tariffs.offpeak_export_eur_per_mwh))
    return pd.DataFrame(
        {
            "timestamp_local": local,
            "tariff_class": classes,
            "export_rate_eur_per_mwh": rates,
            "customer_rate_eur_per_mwh": float(tariffs.customer_sale_eur_per_mwh),
        }
    )


def classify_frame(frame: pd.DataFrame, tariffs: TariffConfig) -> pd.DataFrame:
    if "timestamp_utc" in frame.columns:
        classified = classify_interval_starts(frame["timestamp_utc"], tariffs)
    else:
        classified = classify_interval_starts(frame["timestamp_local"], tariffs)
    return classified.reset_index(drop=True)


def tariff_schedule_dict(tariffs: TariffConfig) -> dict[str, object]:
    return {
        **tariffs.to_dict(),
        "peak_start_inclusive": format_hhmm(tariffs.peak_start_local),
        "peak_end_exclusive": format_hhmm(tariffs.peak_end_local),
        "classification": "interval_start_local",
    }


def _local_index(timestamps, tariffs: TariffConfig) -> pd.DatetimeIndex:
    zone = tariffs.zone()
    series = pd.to_datetime(pd.Series(timestamps))
    if getattr(series.dt, "tz", None) is None:
        # Naive values are treated as UTC physical interval starts.
        series = series.dt.tz_localize("UTC")
    return series.dt.tz_convert(zone)