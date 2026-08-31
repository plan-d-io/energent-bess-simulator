"""Live central-defaults adapter. Public load_central_defaults only."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from btm_sim.config import ConfigError, load_central_defaults

from ui.services.period_inspection import as_serialisable

REASON_DEFAULTS = "The central defaults could not be loaded."


def _basename(path: Any) -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return Path(text).name


def failed_defaults(*, exception_type: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": "DEFAULTS_UNAVAILABLE",
        "message": REASON_DEFAULTS,
    }
    if exception_type:
        error["exception_type"] = exception_type
    return {
        "ok": False,
        "error": error,
        "basename": None,
        "signature": None,
        "battery": None,
        "tariffs": None,
        "reporting": None,
        "economics": None,
        "sweep": None,
    }


def project_central_defaults(loaded: Any) -> dict[str, Any]:
    """Project a public CentralDefaults-like object into a serialisable mapping."""
    if hasattr(loaded, "payload"):
        payload = dict(loaded.payload())
    elif isinstance(loaded, Mapping):
        payload = dict(loaded)
    else:
        return failed_defaults(exception_type=type(loaded).__name__)
    battery = payload.get("battery")
    tariffs = payload.get("tariffs")
    reporting = payload.get("reporting")
    economics = payload.get("economics")
    sweep = payload.get("sweep")
    if not all(isinstance(item, Mapping) for item in (battery, tariffs, reporting, economics, sweep)):
        return failed_defaults()
    basename = _basename(getattr(loaded, "path", None) or payload.get("basename"))
    sha256 = getattr(loaded, "sha256", None) or payload.get("sha256")
    signature = None
    if basename and sha256:
        signature = f"{basename}:{sha256}"
    snapshot = as_serialisable(
        {
            "ok": True,
            "error": None,
            "basename": basename,
            "signature": signature,
            "battery": dict(battery),
            "tariffs": dict(tariffs),
            "reporting": dict(reporting),
            "economics": dict(economics),
            "sweep": dict(sweep),
        }
    )
    snapshot.pop("path", None)
    return snapshot


def load_defaults_snapshot(
    loader: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Load live starting values. Never parses TOML and never copies numeric fallbacks."""
    fn = loader or load_central_defaults
    try:
        loaded = fn()
    except ConfigError:
        return failed_defaults(exception_type="ConfigError")
    except Exception as exc:
        return failed_defaults(exception_type=type(exc).__name__)
    try:
        snapshot = project_central_defaults(loaded)
    except Exception as exc:
        return failed_defaults(exception_type=type(exc).__name__)
    if not snapshot.get("ok"):
        return snapshot
    return snapshot
