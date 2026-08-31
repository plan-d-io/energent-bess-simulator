"""Live selected-period inspection adapter. Public inspect_selected_period only."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Sequence

from btm_sim import __version__ as SIMULATOR_VERSION
from btm_sim import inspect_selected_period

from ui.services.period import INSPECT_DURATIONS
from ui.services.uploads import unique_basename

_INSPECT_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_PATH_KEYS = frozenset({"path", "source_path", "manifest_path", "output_dir"})


def clear_period_inspect_cache() -> None:
    _INSPECT_CACHE.clear()


def stage_three_payloads(folder: Path, payloads: Sequence[tuple[str, bytes]]) -> list[Path]:
    used: set[str] = set()
    paths: list[Path] = []
    for name, data in payloads:
        dest = folder / unique_basename(name, used)
        dest.write_bytes(data)
        paths.append(dest)
    return paths


def period_inspection_cache_key(
    signature: Sequence[Any],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
    simulator_version: str | None = None,
    durations_hours: Sequence[float] | None = None,
) -> tuple[Any, ...]:
    durations = tuple(
        float(item) for item in (durations_hours if durations_hours is not None else INSPECT_DURATIONS)
    )
    return (
        tuple(signature),
        str(period_id),
        bool(allow_unvalidated),
        bool(acknowledge_site_boundary),
        durations,
        str(simulator_version or SIMULATOR_VERSION),
    )


def failed_inspection(period_id: str, *, exception_type: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": "INSPECTION_FAILED",
        "message": "The selected period could not be checked.",
    }
    if exception_type:
        error["exception_type"] = exception_type
    return {
        "ok": False,
        "requires_site_boundary_acknowledgement": False,
        "period_id": str(period_id),
        "selected_period": None,
        "fatal": [
            {
                "code": "INSPECTION_FAILED",
                "message": "The selected period could not be checked.",
                "details": {},
                "severity": "fatal",
            }
        ],
        "warnings": [],
        "report": {},
        "site_analysis": None,
        "automatic_candidates": [],
        "error": error,
    }


def sanitize_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in _PATH_KEYS and isinstance(item, str) and item:
                out[str(key)] = Path(item).name
            else:
                out[str(key)] = sanitize_paths(item)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_paths(item) for item in value]
    return value


def as_serialisable(payload: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = sanitize_paths(dict(payload))
    return json.loads(json.dumps(cleaned, default=str))


def project_inspection(result: Any, period_id: str) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        payload = dict(result.to_dict())
    elif isinstance(result, Mapping):
        payload = dict(result)
    else:
        return failed_inspection(period_id)
    payload.setdefault("period_id", str(period_id))
    return as_serialisable(payload)


def inspect_period_payloads(
    payloads: Sequence[tuple[str, bytes]],
    period_id: str,
    *,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
    signature: Sequence[Any] | None = None,
    inspector: Callable[..., Any] | None = None,
    simulator_version: str | None = None,
    durations_hours: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Inspect one selected period. Wrong counts and exceptions become structured failures."""
    items = tuple((str(name), bytes(data)) for name, data in payloads)
    if len(items) != 3:
        return failed_inspection(period_id)
    durations = tuple(
        float(item) for item in (durations_hours if durations_hours is not None else INSPECT_DURATIONS)
    )
    cache_key = period_inspection_cache_key(
        signature if signature is not None else items,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
        simulator_version=simulator_version,
        durations_hours=durations,
    )
    if inspector is None:
        cached = _INSPECT_CACHE.get(cache_key)
        if cached is not None:
            return cached
        snapshot = _inspect_uncached(
            items,
            period_id,
            allow_unvalidated=allow_unvalidated,
            acknowledge_site_boundary=acknowledge_site_boundary,
            inspector=inspect_selected_period,
            durations_hours=durations,
        )
        _INSPECT_CACHE[cache_key] = snapshot
        return snapshot
    return _inspect_uncached(
        items,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
        inspector=inspector,
        durations_hours=durations,
    )


def _inspect_uncached(
    payloads: tuple[tuple[str, bytes], ...],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
    inspector: Callable[..., Any],
    durations_hours: Sequence[float],
) -> dict[str, Any]:
    try:
        with TemporaryDirectory(prefix="btm_v2_upload_") as tmp:
            paths = stage_three_payloads(Path(tmp), payloads)
            result = inspector(
                paths,
                period_id,
                allow_unvalidated=bool(allow_unvalidated),
                acknowledge_site_boundary=bool(acknowledge_site_boundary),
                durations_hours=tuple(float(item) for item in durations_hours),
            )
            snapshot = project_inspection(result, period_id)
            if snapshot.get("error"):
                return snapshot
            return snapshot
    except Exception as exc:
        return failed_inspection(period_id, exception_type=type(exc).__name__)
