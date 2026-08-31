from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from ui.services.compare_downloads import contained_file
from ui.services.saved_example import sweep_artifact_dir
from ui.services.sweep_display import load_sweep_display
from ui.services.sweep_downloads import (
    audit_zip_filename,
    build_audit_zip,
    grouped_inventory,
    inventory_rows,
    zip_has_only_safe_names,
)

GANDA = sweep_artifact_dir()


def test_inventory_includes_only_existing_in_root_files() -> None:
    names = {row["File"] for row in inventory_rows(GANDA)}
    assert "sweep_summary.json" in names
    assert "sweep_summary.csv" in names
    assert "sweep_request.json" in names
    groups = dict(grouped_inventory(GANDA))
    assert "Results" in groups
    assert "Audit" in groups
    for row in inventory_rows(GANDA):
        assert contained_file(GANDA, row["File"]) is not None


def test_zip_stays_in_root_and_rejects_traversal(tmp_path: Path) -> None:
    folder = tmp_path / "result"
    folder.mkdir()
    (folder / "sweep_summary.json").write_text("{}", encoding="utf-8")
    (folder / "sweep_metadata.json").write_text("{}", encoding="utf-8")
    (folder / "run.log").write_text("log", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    payload = build_audit_zip(folder)
    assert zip_has_only_safe_names(payload)
    with ZipFile(BytesIO(payload), "r") as archive:
        names = set(archive.namelist())
    assert "sweep_summary.json" in names
    assert "secret.txt" not in names
    assert ".." not in "".join(names)
    assert contained_file(folder, "../secret.txt") is None
    assert contained_file(folder, "/tmp/secret.txt") is None
    assert audit_zip_filename(site="Ganda Cars", period_id="2024").endswith("_sweep_audit.zip")


def test_display_csv_is_in_memory() -> None:
    model = load_sweep_display(GANDA)
    payload = model.sizes["csv_bytes"]
    assert isinstance(payload, bytes)
    text = payload.decode("utf-8")
    assert "Power (kW)" in text
    assert "candidate_id" not in text
    assert "estimated_value" not in text
    assert "c001_5kW_10kWh" not in text
