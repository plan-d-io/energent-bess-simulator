"""In-memory audit ZIP and file inventory for full-comparison Results."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from ui.services.paths import safe_slug

ZIP_MAX_FILE_BYTES = 5_000_000
ZIP_MAX_TOTAL_BYTES = 40_000_000
LARGE_DISPATCH_FILES = frozenset(
    {"comparison_dispatch.csv", "comparison_dispatch.parquet"}
)
AUDIT_FILES = (
    ("normalized_input.parquet", "Canonical selected-period data used by the run."),
    ("comparison_summary.csv", "Scenario totals for spreadsheet reuse."),
    ("comparison_summary.json", "Machine-readable scenario totals."),
    ("monthly_summary.csv", "Monthly energy, peak, battery and revenue results."),
    ("monthly_peaks.csv", "Highest 15-minute grid-import peak in each local month."),
    ("comparison_dispatch.parquet", "Compressed quarter-hour dispatch for fast filtering."),
    ("comparison_dispatch.csv", "Quarter-hour dispatch, tariffs, energy flows and revenue ledger."),
    ("run_metadata.json", "Provenance, data quality, software version and solver checks."),
    ("resolved_config.json", "Every effective input value and its source."),
    ("source_config.toml", "Copy of the expert-supplied TOML when one was used."),
    ("source_defaults.toml", "Copy of the central defaults used for this run."),
    ("dynamic_injection_prices.parquet", "Selected-period day-ahead prices used by Dynamic injection tariff."),
    ("validation_report.json", "Normalization issues, acknowledgements, roles and periods."),
)


def contained_file(folder: Path, relative: str) -> Path | None:
    if not relative or relative.startswith("/") or relative.startswith("\\"):
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = folder.resolve()
    try:
        path = (folder / candidate).resolve()
    except OSError:
        return None
    if path == root or root not in path.parents:
        return None
    if not path.is_file():
        return None
    return path


def archive_name(relative: str) -> str | None:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate.as_posix()


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
    plot_dir = root / "plots"
    try:
        if plot_dir.is_dir():
            images = sorted(path for path in plot_dir.glob("*.png") if contained_file(root, f"plots/{path.name}"))
            if images:
                rows.append(
                    (
                        "plots/*.png",
                        f"Fixed seasonal traces ({len(images)} image(s)).",
                        sum(int(path.stat().st_size) for path in images),
                    )
                )
    except OSError:
        pass
    return rows


def inventory_rows(folder: Path | str) -> list[dict[str, str]]:
    rows = []
    for name, purpose, size in existing_audit_files(folder):
        rows.append({"File": name, "Purpose": purpose, "Size": _size_label(size)})
    return rows


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
        if name in LARGE_DISPATCH_FILES:
            continue
        if name == "plots/*.png":
            plot_dir = root / "plots"
            try:
                for image in sorted(plot_dir.glob("*.png")):
                    if contained_file(root, f"plots/{image.name}") is not None:
                        stamps.append((image.name, int(image.stat().st_mtime_ns), int(image.stat().st_size)))
            except OSError:
                continue
            continue
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
            if name in LARGE_DISPATCH_FILES:
                continue
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
        plot_dir = root / "plots"
        try:
            if plot_dir.is_dir():
                for image in sorted(plot_dir.glob("*.png")):
                    relative = f"plots/{image.name}"
                    path = contained_file(root, relative)
                    member = archive_name(relative)
                    if path is None or member is None:
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if size >= ZIP_MAX_FILE_BYTES or total + size > ZIP_MAX_TOTAL_BYTES:
                        continue
                    archive.write(path, arcname=member)
                    total += size
        except OSError:
            pass
    return buffer.getvalue()


def zip_has_only_safe_names(payload: bytes) -> bool:
    with ZipFile(BytesIO(payload), "r") as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
                return False
    return True


def audit_zip_filename(*, site: str, period_id: str) -> str:
    return f"{safe_slug(site or 'site')}_{safe_slug(period_id or 'period')}_compare_audit.zip"


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
