"""Safe job-scoped paths under the project outputs directory."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ui.services.saved_example import project_root

STAGING_FOLDER = "_ui_staging"
STAGING_FILENAMES = ("fluvius_1.csv", "fluvius_2.csv", "fluvius_3.csv")
WORKER_CONSOLE_FILENAME = "worker_stdout.log"
RUN_REQUEST_FILENAME = "run_request.json"
SWEEP_REQUEST_FILENAME = "sweep_request.json"
KIND_COMPARISON = "comparison"
KIND_SWEEP = "sweep"


def default_outputs_root() -> Path:
    return project_root() / "outputs"


def default_staging_root(outputs_root: Path | None = None) -> Path:
    return (outputs_root or default_outputs_root()) / STAGING_FOLDER


def src_dir() -> Path:
    return project_root() / "src"


def safe_slug(label: str, *, max_length: int = 40) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(label or "").strip())
    cleaned = cleaned.strip("._-")[:max_length].strip("._-")
    return cleaned or "site"


def is_contained(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except OSError:
        return False
    if resolved == root_resolved:
        return False
    return root_resolved in resolved.parents


def staging_dir_for(job_id: str, *, staging_root: Path) -> Path:
    slug = safe_slug(job_id, max_length=80)
    path = (staging_root / slug).resolve()
    if not is_contained(path, staging_root):
        raise ValueError("staging")
    return path


def output_dir_for(
    job_id: str,
    *,
    site: str,
    period_id: str,
    kind: str,
    outputs_root: Path,
) -> Path:
    parts = [safe_slug(site), safe_slug(period_id)]
    if kind == KIND_SWEEP:
        parts.append("sweep")
    parts.append(safe_slug(job_id, max_length=80))
    path = (outputs_root / "_".join(parts)).resolve()
    if not is_contained(path, outputs_root):
        raise ValueError("output")
    if is_contained(path, default_staging_root(outputs_root)) or path == default_staging_root(
        outputs_root
    ).resolve():
        raise ValueError("output")
    return path


def recorded_path(value: Any) -> Path | None:
    """Return a path only when the job record stored a non-empty value."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def request_filename(kind: str) -> str:
    if kind == KIND_SWEEP:
        return SWEEP_REQUEST_FILENAME
    return RUN_REQUEST_FILENAME
