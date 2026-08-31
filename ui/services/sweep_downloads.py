"""In-memory audit ZIP and grouped inventory for battery-size Results."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from ui.services.compare_downloads import (
    ZIP_MAX_FILE_BYTES,
    ZIP_MAX_TOTAL_BYTES,
    archive_name,
    contained_file,
)
from ui.services.paths import safe_slug

DOWNLOAD_GROUPS = (
    (
        "Results",
        (
            ("sweep_summary.csv", "Candidate results for spreadsheet reuse."),
            ("sweep_summary.parquet", "Candidate results in Parquet form."),
            ("sweep_summary.json", "Candidate results and screening summary."),
            ("sweep_metadata.json", "Sweep provenance."),
        ),
    ),
    (
        "Audit",
        (
            ("sweep_request.json", "Frozen sweep request."),
            ("run_status.json", "Worker status."),
            ("run_events.jsonl", "Progress events."),
            ("run.log", "Complete run log."),
            ("resolved_config.json", "Every effective input value and its source."),
            ("source_defaults.toml", "Copy of the central defaults used for this sweep."),
            ("site_analysis.json", "Site analysis used to suggest sizes."),
        ),
    ),
    (
        "Input and validation",
        (
            ("normalized_input.parquet", "Canonical selected-period data used by the sweep."),
            ("validation_report.json", "Normalization issues and period metadata."),
        ),
    ),
)

AUDIT_FILES = tuple(
    (name, purpose) for _heading, files in DOWNLOAD_GROUPS for name, purpose in files
)


def existing_audit_files(folder: Path | str) -> list[tuple[str, str, int]]:
    root = Path(folder)
    rows: list[tuple[str, str, int]] = []
    for name, purpose in AUDIT_FILES:
        path = contained_file(root, name)
        if path is None:
            continue
        try:
            size = int(path.stat().st_size)
        except OSError:
            continue
        rows.append((name, purpose, size))
    return rows


def inventory_rows(folder: Path | str) -> list[dict[str, str]]:
    rows = []
    for name, purpose, size in existing_audit_files(folder):
        rows.append({"File": name, "Purpose": purpose, "Size": _size_label(size)})
    return rows


def grouped_inventory(folder: Path | str) -> list[tuple[str, list[dict[str, str]]]]:
    root = Path(folder)
    groups: list[tuple[str, list[dict[str, str]]]] = []
    for heading, files in DOWNLOAD_GROUPS:
        rows: list[dict[str, str]] = []
        for name, purpose in files:
            path = contained_file(root, name)
            if path is None:
                continue
            try:
                size = int(path.stat().st_size)
            except OSError:
                continue
            rows.append({"File": name, "Purpose": purpose, "Size": _size_label(size)})
        if rows:
            groups.append((heading, rows))
    return groups


def _size_label(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} kB"
    return f"{size / (1024 * 1024):.1f} MB"


def zip_identity(folder: Path | str) -> tuple[Any, ...]:
    root = Path(folder)
    stamps: list[Any] = [str(root.resolve())]
    for name, _purpose, _size in existing_audit_files(root):
        path = contained_file(root, name)
        if path is None:
            stamps.append((name, None, None))
        else:
            stat = path.stat()
            stamps.append((name, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(stamps)


def build_audit_zip(folder: Path | str) -> bytes:
    root = Path(folder).resolve()
    buffer = BytesIO()
    total = 0
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, _purpose in AUDIT_FILES:
            path = contained_file(root, name)
            if path is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size >= ZIP_MAX_FILE_BYTES or total + size > ZIP_MAX_TOTAL_BYTES:
                continue
            member = archive_name(name)
            if member is None:
                continue
            archive.write(path, arcname=member)
            total += size
    return buffer.getvalue()


def zip_has_only_safe_names(payload: bytes) -> bool:
    with ZipFile(BytesIO(payload), "r") as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
                return False
    return True


def audit_zip_filename(*, site: str, period_id: str) -> str:
    return f"{safe_slug(site or 'site')}_{safe_slug(period_id or 'period')}_sweep_audit.zip"


def file_size(folder: Path | str, name: str) -> int | None:
    path = contained_file(Path(folder), name)
    if path is None:
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def read_contained_bytes(folder: Path | str, name: str) -> bytes | None:
    path = contained_file(Path(folder), name)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None
