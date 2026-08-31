"""Belgian day-ahead wholesale prices for dynamic injection settlement."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from btm_sim.fluvius.csv_io import sha256_file

REQUIRED_COLUMNS = (
    "datetime_utc",
    "da_price_eur_mwh",
    "native_resolution",
    "upsampled_from_hourly",
    "source_file",
)
STANDARD_FILENAME = "da_prices_qh.parquet"
STANDARD_MANIFEST_NAME = "MANIFEST.json"
EXPECTED_STANDARD_SHA256 = "20d11bf9d3296412b7ae24fef8972d30bc7b8b8977dff7f2fac51502bcbcd646"
EXPECTED_STANDARD_ROWS = 403964
EXPECTED_COVERAGE_START = "2015-01-04T23:00:00Z"
EXPECTED_COVERAGE_END = "2026-07-13T21:45:00Z"


class PriceDataError(ValueError):
    """Invalid or incomplete day-ahead price input."""


@dataclass(frozen=True)
class DayAheadPrices:
    """Aligned selected-period prices plus source audit fields."""

    frame: pd.DataFrame
    source_path: Path
    source_sha256: str
    manifest_path: Path | None
    manifest_sha256: str | None
    row_count_source: int
    coverage_utc_start: str
    coverage_utc_end: str
    native_resolution_counts: dict[str, int]
    hourly_values_repeated: bool
    used_standard_dataset: bool

    def prices_eur_mwh(self) -> np.ndarray:
        return self.frame["da_price_eur_mwh"].to_numpy(dtype=float)

    def aligned_audit(self) -> dict[str, Any]:
        prices = self.prices_eur_mwh()
        return {
            "source_path": str(self.source_path),
            "source_sha256": self.source_sha256,
            "manifest_path": None if self.manifest_path is None else str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "used_standard_dataset": self.used_standard_dataset,
            "source_row_count": self.row_count_source,
            "selected_row_count": int(len(self.frame)),
            "coverage_utc": [self.coverage_utc_start, self.coverage_utc_end],
            "native_resolution_counts": self.native_resolution_counts,
            "hourly_values_repeated": self.hourly_values_repeated,
            "selected_min_eur_mwh": float(np.min(prices)) if len(prices) else None,
            "selected_max_eur_mwh": float(np.max(prices)) if len(prices) else None,
            "selected_mean_eur_mwh": float(np.mean(prices)) if len(prices) else None,
        }


def standard_day_ahead_prices_path() -> Path:
    """Return the project's ``data/market/da_prices_qh.parquet``, independent of cwd."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "market" / STANDARD_FILENAME
        if candidate.is_file():
            return candidate
    raise PriceDataError(
        "Standard day-ahead price file not found: looked for data/market/"
        f"{STANDARD_FILENAME} from the application location ({here})"
    )


def load_day_ahead_prices(
    selected_timestamps: pd.Series,
    *,
    path: Path | str | None = None,
) -> DayAheadPrices:
    """Load, validate, and align prices by exact UTC equality. Do not resample."""
    source = Path(path) if path is not None else standard_day_ahead_prices_path()
    if not source.exists():
        raise PriceDataError(f"Day-ahead price file not found: {source}")
    used_standard = source.resolve() == standard_day_ahead_prices_path().resolve()
    try:
        table = pd.read_parquet(source)
    except Exception as exc:
        raise PriceDataError(f"Cannot read day-ahead price Parquet {source}: {exc}") from exc
    _validate_schema(table, source)
    source_hash = sha256_file(source)
    coverage_start, coverage_end = _coverage_bounds(table)
    native_counts, hourly_repeated = _resolution_counts(table)
    manifest_path, manifest_hash = _validate_manifest_if_present(
        source,
        source_hash=source_hash,
        row_count=len(table),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        require_standard=used_standard,
    )
    aligned = _align_selected(table, selected_timestamps, source)
    return DayAheadPrices(
        frame=aligned,
        source_path=source.resolve(),
        source_sha256=source_hash,
        manifest_path=manifest_path,
        manifest_sha256=manifest_hash,
        row_count_source=int(len(table)),
        coverage_utc_start=coverage_start,
        coverage_utc_end=coverage_end,
        native_resolution_counts=native_counts,
        hourly_values_repeated=hourly_repeated,
        used_standard_dataset=used_standard,
    )


def _validate_schema(table: pd.DataFrame, source: Path) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in table.columns]
    if missing:
        raise PriceDataError(f"{source} is missing required columns: {missing}")
    if table.empty:
        raise PriceDataError(f"{source} contains no price rows")
    dtype = table["datetime_utc"].dtype
    if getattr(dtype, "tz", None) is None:
        raise PriceDataError(f"{source} timestamps must be timezone-aware UTC, not naive local time")
    utc = pd.to_datetime(table["datetime_utc"], utc=True)
    if utc.duplicated().any():
        raise PriceDataError(f"{source} contains duplicate datetime_utc values")
    if len(utc) > 1:
        ordered = utc.sort_values()
        if not utc.is_monotonic_increasing:
            raise PriceDataError(f"{source} datetime_utc values are not strictly increasing")
        steps = ordered.diff().iloc[1:]
        if (steps != pd.Timedelta(minutes=15)).any():
            raise PriceDataError(f"{source} is not a continuous 15-minute UTC series")
    prices = pd.to_numeric(table["da_price_eur_mwh"], errors="coerce")
    if prices.isna().any() or not np.isfinite(prices.to_numpy(dtype=float)).all():
        raise PriceDataError(f"{source} contains missing or non-finite da_price_eur_mwh values")


def _coverage_bounds(table: pd.DataFrame) -> tuple[str, str]:
    utc = pd.to_datetime(table["datetime_utc"], utc=True)
    start = pd.Timestamp(utc.iloc[0]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    end = pd.Timestamp(utc.iloc[-1]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, end


def _resolution_counts(table: pd.DataFrame) -> tuple[dict[str, int], bool]:
    native = table["native_resolution"].astype(str)
    counts = {str(key): int(value) for key, value in native.value_counts().items()}
    upsampled = table["upsampled_from_hourly"].to_numpy(dtype=bool)
    return counts, bool(np.any(upsampled))


def _validate_manifest_if_present(
    source: Path,
    *,
    source_hash: str,
    row_count: int,
    coverage_start: str,
    coverage_end: str,
    require_standard: bool,
) -> tuple[Path | None, str | None]:
    manifest_path = source.parent / STANDARD_MANIFEST_NAME
    if not manifest_path.exists():
        if require_standard:
            raise PriceDataError(
                f"Standard day-ahead price dataset is missing {STANDARD_MANIFEST_NAME} next to {source}"
            )
        return None, None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PriceDataError(f"Invalid day-ahead price manifest {manifest_path}: {exc}") from exc
    if payload.get("file") != source.name:
        raise PriceDataError(
            f"Manifest {manifest_path} file name {payload.get('file')!r} does not match {source.name}"
        )
    if int(payload.get("rows", -1)) != row_count:
        raise PriceDataError(
            f"Manifest {manifest_path} row count {payload.get('rows')} does not match Parquet ({row_count})"
        )
    coverage = payload.get("coverage_utc") or []
    if list(coverage) != [coverage_start, coverage_end]:
        raise PriceDataError(
            f"Manifest {manifest_path} coverage {coverage} does not match Parquet "
            f"[{coverage_start}, {coverage_end}]"
        )
    expected_hash = str(payload.get("sha256", ""))
    if expected_hash != source_hash:
        raise PriceDataError(
            f"Manifest {manifest_path} sha256 {expected_hash} does not match Parquet {source_hash}"
        )
    if require_standard:
        if source_hash != EXPECTED_STANDARD_SHA256 or row_count != EXPECTED_STANDARD_ROWS:
            raise PriceDataError(
                f"Standard day-ahead price file {source} does not match the documented project dataset"
            )
        if coverage_start != EXPECTED_COVERAGE_START or coverage_end != EXPECTED_COVERAGE_END:
            raise PriceDataError(
                f"Standard day-ahead price coverage [{coverage_start}, {coverage_end}] "
                "does not match the documented project dataset"
            )
    return manifest_path.resolve(), sha256_file(manifest_path)


def _align_selected(table: pd.DataFrame, selected_timestamps: pd.Series, source: Path) -> pd.DataFrame:
    selected = pd.to_datetime(selected_timestamps, utc=True)
    if selected.isna().any():
        raise PriceDataError("Selected simulation timestamps include missing or naive values")
    if selected.duplicated().any():
        raise PriceDataError("Selected simulation timestamps must be unique")
    prices = table.loc[:, list(REQUIRED_COLUMNS)].copy()
    prices["datetime_utc"] = pd.to_datetime(prices["datetime_utc"], utc=True)
    aligned = pd.DataFrame({"datetime_utc": selected}).merge(prices, on="datetime_utc", how="left")
    missing = aligned["da_price_eur_mwh"].isna()
    if bool(missing.any()):
        first_missing = aligned.loc[missing, "datetime_utc"].iloc[0]
        raise PriceDataError(
            f"{source} has no day-ahead price for {int(missing.sum())} selected interval(s); "
            "the dynamic-injection case cannot invent or resample prices. "
            f"First missing timestamp: {first_missing}"
        )
    aligned["native_resolution"] = aligned["native_resolution"].astype(str)
    aligned["source_file"] = aligned["source_file"].astype(str)
    aligned["upsampled_from_hourly"] = aligned["upsampled_from_hourly"].astype(bool)
    return aligned
