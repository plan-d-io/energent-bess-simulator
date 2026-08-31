from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from ui.services.compare_downloads import (
    LARGE_DISPATCH_FILES,
    archive_name,
    audit_zip_filename,
    build_audit_zip,
    contained_file,
    existing_audit_files,
    inventory_rows,
    zip_has_only_safe_names,
)
from ui.services.saved_example import compare_artifact_dir


def test_zip_excludes_dispatch_and_stays_in_root(tmp_path: Path) -> None:
    folder = tmp_path / "result"
    folder.mkdir()
    (folder / "comparison_summary.json").write_text("{}", encoding="utf-8")
    (folder / "monthly_summary.csv").write_text("month\n", encoding="utf-8")
    (folder / "comparison_dispatch.parquet").write_bytes(b"parquet")
    (folder / "comparison_dispatch.csv").write_text("ts\n", encoding="utf-8")
    plots = folder / "plots"
    plots.mkdir()
    (plots / "week.png").write_bytes(b"png")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    payload = build_audit_zip(folder)
    assert zip_has_only_safe_names(payload)
    with ZipFile(BytesIO(payload), "r") as archive:
        names = set(archive.namelist())
    assert "comparison_summary.json" in names
    assert "plots/week.png" in names
    assert "comparison_dispatch.parquet" not in names
    assert "comparison_dispatch.csv" not in names
    assert "secret.txt" not in names
    assert not any(".." in name or name.startswith("/") for name in names)
    assert not list(folder.glob("*.zip"))


def test_archive_names_cannot_escape() -> None:
    assert archive_name("monthly_summary.csv") == "monthly_summary.csv"
    assert archive_name("../secret.json") is None
    assert archive_name("C:/Windows/secret.json") is None
    folder = Path(".").resolve()
    assert contained_file(folder, "..") is None
    assert contained_file(folder, "../secret.json") is None


def test_inventory_lists_only_existing_files() -> None:
    folder = compare_artifact_dir()
    names = {name for name, _purpose, _size in existing_audit_files(folder)}
    assert "comparison_summary.json" in names
    assert "comparison_dispatch.parquet" in names
    rows = inventory_rows(folder)
    listed = {row["File"] for row in rows}
    assert listed == names
    assert "comparison_dispatch.csv" not in listed


def test_download_filename_has_no_brief_number() -> None:
    name = audit_zip_filename(site="Ganda Cars", period_id="2024")
    assert name == "Ganda_Cars_2024_compare_audit.zip"
    assert "brief" not in name.lower()
    assert "07a" not in name.lower()
    assert "14a" not in name.lower()


def test_large_dispatch_constant_matches_excluded_set() -> None:
    assert LARGE_DISPATCH_FILES == {"comparison_dispatch.csv", "comparison_dispatch.parquet"}
