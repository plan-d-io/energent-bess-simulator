"""Exact day-ahead price coverage for the selected period timestamps."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Sequence

from btm_sim import __version__ as SIMULATOR_VERSION
from btm_sim import normalize_fluvius
from btm_sim.market import PriceDataError, load_day_ahead_prices, standard_day_ahead_prices_path

from ui.services.period_inspection import as_serialisable, stage_three_payloads

_PRICE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def clear_price_coverage_cache() -> None:
    _PRICE_CACHE.clear()


def _dataset_identity() -> str:
    try:
        path = standard_day_ahead_prices_path()
        return path.name
    except Exception:
        return "da_prices_qh.parquet"


def price_coverage_cache_key(
    signature: Sequence[Any],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
    simulator_version: str | None = None,
    dataset_identity: str | None = None,
) -> tuple[Any, ...]:
    return (
        tuple(signature),
        str(period_id),
        bool(allow_unvalidated),
        bool(acknowledge_site_boundary),
        str(simulator_version or SIMULATOR_VERSION),
        str(dataset_identity or _dataset_identity()),
    )


def unavailable_coverage(*, exception_type: str | None = None, message: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": "PRICE_UNAVAILABLE",
        "message": message or "Day-ahead prices do not cover this period.",
    }
    if exception_type:
        error["exception_type"] = exception_type
    return {
        "covered": False,
        "unavailable": True,
        "one_battery_unavailable": True,
        "selected_row_count": None,
        "source_basename": None,
        "coverage_utc": None,
        "native_resolution_counts": None,
        "hourly_values_repeated": None,
        "error": error,
    }


def project_price_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    payload = as_serialisable(audit)
    source = payload.get("source_path")
    return {
        "covered": True,
        "unavailable": False,
        "one_battery_unavailable": False,
        "selected_row_count": payload.get("selected_row_count"),
        "source_basename": Path(str(source)).name if source else None,
        "coverage_utc": payload.get("coverage_utc"),
        "native_resolution_counts": payload.get("native_resolution_counts"),
        "hourly_values_repeated": payload.get("hourly_values_repeated"),
        "error": None,
    }


def price_coverage_for_payloads(
    payloads: Sequence[tuple[str, bytes]],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
    signature: Sequence[Any] | None = None,
    normalize: Callable[..., Any] | None = None,
    load_prices: Callable[..., Any] | None = None,
    simulator_version: str | None = None,
    dataset_identity: str | None = None,
) -> dict[str, Any]:
    items = tuple((str(name), bytes(data)) for name, data in payloads)
    if len(items) != 3:
        return unavailable_coverage(message="The selected period could not be normalised.")
    cache_key = price_coverage_cache_key(
        signature if signature is not None else items,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
        simulator_version=simulator_version,
        dataset_identity=dataset_identity,
    )
    if normalize is None and load_prices is None:
        cached = _PRICE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        snapshot = _coverage_uncached(
            items,
            period_id,
            allow_unvalidated=allow_unvalidated,
            acknowledge_site_boundary=acknowledge_site_boundary,
            normalize=normalize_fluvius,
            load_prices=load_day_ahead_prices,
        )
        _PRICE_CACHE[cache_key] = snapshot
        return snapshot
    return _coverage_uncached(
        items,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
        normalize=normalize or normalize_fluvius,
        load_prices=load_prices or load_day_ahead_prices,
    )


def _coverage_uncached(
    payloads: tuple[tuple[str, bytes], ...],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
    normalize: Callable[..., Any],
    load_prices: Callable[..., Any],
) -> dict[str, Any]:
    try:
        with TemporaryDirectory(prefix="btm_v2_upload_") as tmp:
            paths = stage_three_payloads(Path(tmp), payloads)
            result = normalize(
                paths,
                period=str(period_id),
                allow_unvalidated=bool(allow_unvalidated),
                acknowledge_site_boundary=bool(acknowledge_site_boundary),
                output_dir=None,
            )
            frame = getattr(result, "frame", None)
            if frame is None or "timestamp_utc" not in getattr(frame, "columns", []):
                return unavailable_coverage(message="The selected period could not be normalised.")
            timestamps = frame["timestamp_utc"]
            aligned = load_prices(timestamps)
            audit = aligned.aligned_audit() if hasattr(aligned, "aligned_audit") else dict(aligned)
            return project_price_audit(audit)
    except PriceDataError as exc:
        return unavailable_coverage(exception_type=type(exc).__name__, message=str(exc) or None)
    except Exception as exc:
        return unavailable_coverage(exception_type=type(exc).__name__)
