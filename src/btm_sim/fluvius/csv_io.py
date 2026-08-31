"""Read semicolon-separated Fluvius CSV exports with comma decimals."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from btm_sim.fluvius.constants import OPTIONAL_METADATA_COLUMNS, REQUIRED_COLUMNS
from btm_sim.fluvius.issues import IssueLog


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_volume(value: object) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return float("nan")
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none"}:
        return float("nan")
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def parse_ean(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text.startswith("="):
        text = text[1:]
    text = text.strip().strip('"').strip("'")
    return text or None


def read_fluvius_csv(path: Path, issues: IssueLog) -> pd.DataFrame | None:
    """Return a string-typed Fluvius frame, or None if required columns are missing."""
    path = Path(path)
    try:
        frame = pd.read_csv(
            path,
            sep=";",
            dtype=str,
            encoding="utf-8-sig",
            keep_default_na=True,
        )
    except OSError as exc:
        issues.fatal("UNREADABLE_FILE", f"Cannot read {path}: {exc}", path=str(path))
        return None

    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        issues.fatal(
            "MISSING_COLUMNS",
            f"{path.name} is missing required columns: {', '.join(missing)}",
            path=str(path),
            missing=missing,
        )
        return None

    keep = [column for column in (*REQUIRED_COLUMNS, *OPTIONAL_METADATA_COLUMNS) if column in frame.columns]
    frame = frame.loc[:, keep].copy()
    frame["source_path"] = str(path)
    frame["source_row"] = range(1, len(frame) + 1)
    for column in REQUIRED_COLUMNS:
        frame[column] = frame[column].where(frame[column].notna(), "").astype(str).str.strip()
        frame.loc[frame[column].isin({"", "nan", "None"}), column] = ""
    return frame
