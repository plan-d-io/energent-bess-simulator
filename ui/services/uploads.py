"""Streamlit-to-path upload inspection. Calls only public ingest_fluvius."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Sequence

from btm_sim import ingest_fluvius

ROLE_ORDER = ("offtake", "injection", "pv")
ROLE_LABELS = {
    "offtake": "Offtake",
    "injection": "Injection",
    "pv": "PV production",
}
ROLE_REGISTERS = {
    "offtake": "Afname Actief",
    "injection": "Injectie Actief",
    "pv": "Productie Actief",
}

_INSPECT_CACHE: dict[tuple[tuple[str, bytes], ...], dict[str, Any]] = {}


def safe_basename(name: str) -> str:
    base = Path(str(name)).name.strip()
    return base or "upload.csv"


def unique_basename(name: str, used: set[str]) -> str:
    safe = safe_basename(name)
    if safe not in used:
        used.add(safe)
        return safe
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    index = 2
    while True:
        candidate = f"{stem}__{index}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def file_signature(payloads: Sequence[tuple[str, bytes]]) -> tuple[tuple[str, int, str], ...]:
    items: list[tuple[str, int, str]] = []
    for name, data in payloads:
        raw = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        items.append(
            (
                safe_basename(name),
                len(raw),
                hashlib.sha256(raw).hexdigest(),
            )
        )
    return tuple(items)


def clear_inspect_cache() -> None:
    _INSPECT_CACHE.clear()


def inspect_fluvius_payloads(
    payloads: Sequence[tuple[str, bytes]],
    *,
    ingest: Callable[[list[Path]], Any] | None = None,
) -> dict[str, Any] | None:
    """Inspect exactly three uploads. Wrong counts return None and do not call the core."""
    items = tuple((str(name), bytes(data)) for name, data in payloads)
    if len(items) != 3:
        return None
    if ingest is None:
        cached = _INSPECT_CACHE.get(items)
        if cached is not None:
            return cached
        snapshot = _inspect_uncached(items, ingest_fluvius)
        _INSPECT_CACHE[items] = snapshot
        return snapshot
    return _inspect_uncached(items, ingest)


def _inspect_uncached(
    payloads: tuple[tuple[str, bytes], ...],
    ingest: Callable[[list[Path]], Any],
) -> dict[str, Any]:
    try:
        with TemporaryDirectory(prefix="btm_v2_upload_") as tmp:
            folder = Path(tmp)
            used: set[str] = set()
            paths: list[Path] = []
            for name, data in payloads:
                dest = folder / unique_basename(name, used)
                dest.write_bytes(data)
                paths.append(dest)
            result = ingest(paths)
            return project_ingest_result(result)
    except Exception as exc:
        return adapter_failure_snapshot(exc)


def adapter_failure_snapshot(exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "roles": {},
        "sources": [],
        "issues": [],
        "periods": [],
        "dst": {},
        "error": {
            "code": "ADAPTER_FAILURE",
            "message": "The files could not be checked.",
            "exception_type": type(exc).__name__,
        },
    }


def _issue_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        payload = dict(item.to_dict())
    elif isinstance(item, Mapping):
        payload = dict(item)
    else:
        return {"severity": "fatal", "code": "UNKNOWN", "message": str(item)}
    details = payload.get("details")
    if isinstance(details, Mapping) and details.get("path"):
        updated = dict(details)
        updated["path"] = Path(str(updated["path"])).name
        payload["details"] = updated
        message = str(payload.get("message") or "")
        payload["message"] = message.replace(str(details["path"]), updated["path"])
    return payload


def _period_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())
    if isinstance(item, Mapping):
        return dict(item)
    return {}


def _source_dict(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    path = payload.get("path")
    if path:
        payload["path"] = Path(str(path)).name
    return payload


def _role_dict(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "register": meta.get("register"),
        "unit": meta.get("unit"),
        "n_rows": int(meta.get("n_rows") or 0),
    }


def project_ingest_result(result: Any) -> dict[str, Any]:
    """Serialisable UI snapshot. No DataFrame and no temporary paths."""
    issues_obj = getattr(result, "issues", ())
    raw_items = getattr(issues_obj, "items", issues_obj)
    issues = [_issue_dict(item) for item in list(raw_items or ())]
    sources = [_source_dict(item) for item in list(getattr(result, "sources", None) or ())]
    roles_in = getattr(result, "roles", None) or {}
    roles = {
        str(name): _role_dict(meta)
        for name, meta in dict(roles_in).items()
        if isinstance(meta, Mapping)
    }
    periods = [_period_dict(item) for item in list(getattr(result, "periods", None) or ())]
    dst = dict(getattr(result, "dst", None) or {})
    fatals = [item for item in issues if item.get("severity") == "fatal"]
    ok = bool(getattr(result, "ok", not fatals)) and not fatals
    missing = [role for role in ROLE_ORDER if role not in roles]
    ready = ok and not missing
    return {
        "ok": ready,
        "roles": roles,
        "sources": sources,
        "issues": issues,
        "periods": periods,
        "dst": dst,
        "error": None,
    }


def snapshot_is_ready(snapshot: Mapping[str, Any] | None) -> bool:
    if not snapshot or snapshot.get("error"):
        return False
    if not snapshot.get("ok"):
        return False
    roles = snapshot.get("roles") or {}
    return all(role in roles for role in ROLE_ORDER)


def format_row_count(value: int) -> str:
    return f"{int(value):,}"


def live_role_rows(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    roles = snapshot.get("roles") or {}
    rows: list[dict[str, str]] = []
    for role in ROLE_ORDER:
        meta = roles.get(role) or {}
        rows.append(
            {
                "Role": ROLE_LABELS[role],
                "Detected register": str(meta.get("register") or ""),
                "Unit": str(meta.get("unit") or ""),
                "Rows": format_row_count(int(meta.get("n_rows") or 0)),
            }
        )
    return rows


def blocking_panels(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    """One panel per distinct structured problem, original order, no message parsing for logic."""
    panels: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(title: str, body: str) -> None:
        key = (title, body)
        if key in seen:
            return
        seen.add(key)
        panels.append(key)

    error = snapshot.get("error")
    if isinstance(error, Mapping) and error:
        add(
            "The files could not be checked",
            str(error.get("message") or "An unexpected error stopped Fluvius inspection."),
        )
        return panels

    issues = [item for item in (snapshot.get("issues") or []) if item.get("severity") == "fatal"]
    roles = snapshot.get("roles") or {}
    covered_roles: set[str] = set()

    for issue in issues:
        code = str(issue.get("code") or "")
        details = issue.get("details") or {}
        role = str(details.get("role") or "")
        label = ROLE_LABELS.get(role, role)
        register = str(details.get("register") or ROLE_REGISTERS.get(role, ""))
        filename = Path(str(details.get("path") or "")).name
        if code == "UNREADABLE_FILE":
            add(f"{filename or 'A file'} could not be read", "The CSV export is unreadable.")
        elif code == "MISSING_COLUMNS":
            add(
                f"{filename or 'A file'} is missing required columns",
                "This file is not a Fluvius export or omits required columns.",
            )
        elif code == "MISSING_REGISTER":
            covered_roles.add(role)
            add(
                f"The {label} role is missing",
                f"None of these files contains {register or ROLE_REGISTERS.get(role, 'the required register')}.",
            )
        elif code == "AMBIGUOUS_REGISTER":
            covered_roles.add(role)
            add(
                f"The {label} role is duplicated",
                f"More than one series uses {register}.",
            )
        elif code == "UNEXPECTED_UNIT":
            units = details.get("units") or []
            found = ", ".join(str(item) for item in units) if units else "an unexpected unit"
            add(
                f"The {label} series uses an unexpected unit",
                f"Required unit is kWh. Found {found}.",
            )
        else:
            add(
                "These files cannot be used",
                str(issue.get("message") or "A fatal validation issue blocked these files."),
            )

    for role in ROLE_ORDER:
        if role in roles or role in covered_roles:
            continue
        add(
            f"The {ROLE_LABELS[role]} role is missing",
            f"None of these files contains {ROLE_REGISTERS[role]}.",
        )
    return panels
