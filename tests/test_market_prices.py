"""Standard and override day-ahead price loading."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from btm_sim.market.prices import (
    PriceDataError,
    load_day_ahead_prices,
    standard_day_ahead_prices_path,
)
from tests.lp_frames import qh_frame


def _price_table(timestamps: pd.DatetimeIndex, prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime_utc": timestamps,
            "da_price_eur_mwh": prices,
            "native_resolution": "PT15M",
            "upsampled_from_hourly": False,
            "source_file": "fixture.csv",
        }
    )


def test_standard_path_is_stable_when_cwd_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    expected = standard_day_ahead_prices_path()
    assert expected.name == "da_prices_qh.parquet"
    monkeypatch.chdir(tmp_path)
    assert standard_day_ahead_prices_path() == expected
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    loaded = load_day_ahead_prices(frame["timestamp_utc"])
    assert loaded.used_standard_dataset is True
    assert loaded.source_path == expected
    assert len(loaded.frame) == 2
    assert loaded.frame["datetime_utc"].tolist() == list(pd.to_datetime(frame["timestamp_utc"], utc=True))
    assert loaded.source_sha256 == "20d11bf9d3296412b7ae24fef8972d30bc7b8b8977dff7f2fac51502bcbcd646"


def test_override_aligns_by_utc_and_accepts_extra_rows_and_negative_prices(tmp_path: Path):
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    selected = pd.to_datetime(frame["timestamp_utc"], utc=True)
    extra = selected.max() + pd.Timedelta(minutes=15)
    before = selected.min() - pd.Timedelta(minutes=15)
    table = _price_table(
        pd.DatetimeIndex([before, *list(selected), extra], tz="UTC"),
        [-12.5, 0.0, 80.0, 15.0],
    )
    path = tmp_path / "override.parquet"
    table.to_parquet(path, index=False)
    loaded = load_day_ahead_prices(frame["timestamp_utc"], path=path)
    assert loaded.used_standard_dataset is False
    assert loaded.manifest_path is None
    assert list(loaded.prices_eur_mwh()) == pytest.approx([0.0, 80.0])
    assert loaded.frame["datetime_utc"].tolist() == list(selected)


def test_missing_duplicate_naive_and_nonfinite_prices_fail(tmp_path: Path):
    frame = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    selected = pd.to_datetime(frame["timestamp_utc"], utc=True)

    missing = _price_table(pd.DatetimeIndex([selected.iloc[0]], tz="UTC"), [10.0])
    missing_path = tmp_path / "missing.parquet"
    missing.to_parquet(missing_path, index=False)
    with pytest.raises(PriceDataError, match="no day-ahead price"):
        load_day_ahead_prices(frame["timestamp_utc"], path=missing_path)

    naive_index = pd.DatetimeIndex([pd.Timestamp(ts).tz_convert("UTC").replace(tzinfo=None) for ts in selected])
    naive = _price_table(naive_index, [10.0, 11.0])
    naive_path = tmp_path / "naive.parquet"
    naive.to_parquet(naive_path, index=False)
    with pytest.raises(PriceDataError, match="timezone-aware UTC"):
        load_day_ahead_prices(frame["timestamp_utc"], path=naive_path)

    bad = _price_table(selected, [10.0, float("nan")])
    bad_path = tmp_path / "nan.parquet"
    bad.to_parquet(bad_path, index=False)
    with pytest.raises(PriceDataError, match="non-finite"):
        load_day_ahead_prices(frame["timestamp_utc"], path=bad_path)
