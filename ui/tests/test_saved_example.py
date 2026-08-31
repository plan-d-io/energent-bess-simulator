from __future__ import annotations

import json
from pathlib import Path

from ui.services.saved_example import (
    EXPECTED_PERIOD_ID,
    EXPECTED_UNVALIDATED_COUNT,
    EXPECTED_UNVALIDATED_DATE,
    load_saved_configure_context,
    load_saved_example,
    load_saved_period_context,
    load_saved_snapshot,
    project_validation_report,
)


def _write_valid_example(folder: Path) -> tuple[Path, Path]:
    samples = folder / "samples"
    samples.mkdir()
    names = {
        "offtake": ("Afname Actief", "offtake.csv"),
        "injection": ("Injectie Actief", "injection.csv"),
        "pv": ("Productie Actief", "pv.csv"),
    }
    sources = []
    roles = {}
    for role, (register, filename) in names.items():
        path = samples / filename
        path.write_text("csv", encoding="utf-8")
        sources.append({"path": str(path), "registers": [register]})
        roles[role] = {"register": register, "unit": "kWh", "n_rows": 4}
    report = folder / "validation_report.json"
    report.write_text(json.dumps({"ok": True, "roles": roles, "sources": sources}), encoding="utf-8")
    return report, samples


def test_adapter_resolves_ganda_metadata_from_report(tmp_path: Path) -> None:
    report, samples = _write_valid_example(tmp_path)
    example = load_saved_example(report_path=report, sample_dir=samples)
    assert example.ok
    assert example.site_name == "Ganda Cars"
    assert [row["Role"] for row in example.rows] == ["Offtake", "Injection", "PV production"]
    assert example.rows[0]["File"] == "offtake.csv"
    assert example.rows[0]["Detected register"] == "Afname Actief"
    assert example.rows[2]["Unit"] == "kWh"


def test_saved_snapshot_projects_report_without_ean_or_selected_period(tmp_path: Path) -> None:
    report, samples = _write_valid_example(tmp_path)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["roles"]["offtake"]["ean"] = "541448860020928494"
    payload["periods"] = [
        {
            "id": "common",
            "kind": "common_overlap",
            "label": "Continuous common measured overlap",
            "n_intervals": 8,
            "n_unvalidated": 1,
        }
    ]
    payload["dst"] = {"transitions": [{"date_local": "2024-03-31", "kind": "spring_forward"}]}
    payload["selected_period"] = {"id": "2024"}
    payload["warnings"] = [{"severity": "warning", "code": "EAN_MISMATCH", "details": {"eans": {"offtake": "1"}}}]
    payload["fatal"] = []
    report.write_text(json.dumps(payload), encoding="utf-8")
    snapshot = load_saved_snapshot(report_path=report, sample_dir=samples)
    assert snapshot["ok"] is True
    assert snapshot["error"] is None
    assert "ean" not in snapshot["roles"]["offtake"]
    assert "selected_period" not in snapshot
    assert snapshot["periods"][0]["n_intervals"] == 8
    projected = project_validation_report(payload)
    assert projected["sources"][0]["path"] == Path(payload["sources"][0]["path"]).name


def test_malformed_saved_periods_are_blocked(tmp_path: Path) -> None:
    report, samples = _write_valid_example(tmp_path)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["periods"] = {"id": "2024"}
    report.write_text(json.dumps(payload), encoding="utf-8")
    snapshot = load_saved_snapshot(report_path=report, sample_dir=samples)
    assert snapshot["ok"] is False
    assert snapshot["error"]["code"] == "SAVED_EXAMPLE_UNAVAILABLE"


def test_missing_metadata_is_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    example = load_saved_example(report_path=missing, sample_dir=tmp_path)
    assert example.ok is False
    assert example.rows == ()
    assert example.error


def test_missing_source_files_are_blocked(tmp_path: Path) -> None:
    report, samples = _write_valid_example(tmp_path)
    (samples / "pv.csv").unlink()
    example = load_saved_example(report_path=report, sample_dir=samples)
    assert example.ok is False


def test_bundled_example_uses_verified_artifacts_without_private_source_files() -> None:
    example = load_saved_example()
    assert example.ok is True
    assert len(example.rows) == 3


def test_incomplete_roles_are_blocked(tmp_path: Path) -> None:
    report, samples = _write_valid_example(tmp_path)
    payload = json.loads(report.read_text(encoding="utf-8"))
    del payload["roles"]["pv"]
    report.write_text(json.dumps(payload), encoding="utf-8")
    example = load_saved_example(report_path=report, sample_dir=samples)
    assert example.ok is False


def test_saved_period_context_uses_ganda_2024_artifact_fields() -> None:
    context = load_saved_period_context()
    assert context["ok"] is True
    assert context["period_id"] == EXPECTED_PERIOD_ID
    assert context["unvalidated_ack"] is True
    assert context["site_boundary_ack"] is False
    period = context["selected_period"]
    assert period["id"] == "2024"
    assert period["n_unvalidated"] == EXPECTED_UNVALIDATED_COUNT
    assert EXPECTED_UNVALIDATED_DATE in context["unvalidated_dates"]
    inspection = context["period_inspection"]
    assert inspection["ok"] is True
    assert inspection["requires_site_boundary_acknowledgement"] is False
    assert inspection["site_analysis"]["n_intervals"] == period["n_intervals"]
    coverage = context["price_coverage"]
    assert coverage["covered"] is True
    assert coverage["selected_row_count"] == period["n_intervals"]
    assert coverage["source_basename"] == "da_prices_qh.parquet"
    assert ":\\" not in json.dumps(context["period_inspection"])
    assert ":\\" not in json.dumps(context["price_coverage"])


def test_inconsistent_saved_period_fields_are_blocked(tmp_path: Path) -> None:
    context = load_saved_period_context(report_path=tmp_path / "missing.json")
    assert context["ok"] is False
    assert context["period_inspection"] is None
    assert context["error"]["code"] == "SAVED_EXAMPLE_UNAVAILABLE"


def test_saved_configure_uses_compare_and_sweep_artifacts() -> None:
    context = load_saved_configure_context()
    assert context["ok"] is True
    configure = context["configure"]
    json.dumps(configure)
    assert configure["source"] == "saved"
    assert configure["one_battery"]["usable_kwh"] == 100.0
    assert configure["one_battery"]["charge_kw"] == 50.0
    assert configure["shared"]["cost_eur_per_kwh"] == 300.0
    assert configure["candidates"]["count"] == 18
    assert configure["candidates"]["items"][0]["candidate_id"] == "c001_5kW_10kWh"
    assert configure["candidates"]["items"][-1]["candidate_id"] == "c018_300kW_1200kWh"
    assert configure["sizing"]["duration_2h"] is True
    assert configure["sizing"]["duration_4h"] is True
    assert configure["sizing"]["power_mode"] == "suggested"


def test_inconsistent_saved_configure_is_blocked(tmp_path: Path) -> None:
    context = load_saved_configure_context(resolved_config_path=tmp_path / "missing.json")
    assert context["ok"] is False
    assert context["configure"] is None
