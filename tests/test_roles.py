from pathlib import Path

from btm_sim.fluvius.csv_io import read_fluvius_csv
from btm_sim.fluvius.issues import IssueLog
from btm_sim.fluvius.roles import detect_roles
from tests.helpers import write_fluvius_csv, write_role_export, write_site
from datetime import datetime, timezone

UTC = timezone.utc
STARTS = [datetime(2024, 1, 1, 23, 0, tzinfo=UTC), datetime(2024, 1, 1, 23, 15, tzinfo=UTC)]


def test_roles_come_from_register_not_filename(tmp_path: Path):
    write_role_export(tmp_path / "Historiek_afname.csv", "injection", STARTS, [0.0, 0.0], ean="200")
    write_role_export(tmp_path / "injection.csv", "offtake", STARTS, [1.0, 1.0], ean="100")
    write_role_export(tmp_path / "pv.csv", "pv", STARTS, [0.5, 0.5], ean="300")
    issues = IssueLog()
    frames = [
        read_fluvius_csv(tmp_path / "Historiek_afname.csv", issues),
        read_fluvius_csv(tmp_path / "injection.csv", issues),
        read_fluvius_csv(tmp_path / "pv.csv", issues),
    ]
    roles = detect_roles([frame for frame in frames if frame is not None], issues)
    assert issues.ok
    assert roles["offtake"].register == "Afname Actief"
    assert roles["injection"].register == "Injectie Actief"
    assert roles["pv"].register == "Productie Actief"
    assert roles["offtake"].ean == "100"


def test_unrelated_registers_are_ignored(tmp_path: Path):
    paths = write_site(
        tmp_path,
        STARTS,
        import_kwh=[1.0, 1.0],
        export_kwh=[0.0, 0.0],
        pv_kwh=[0.4, 0.4],
    )
    issues = IssueLog()
    frames = [read_fluvius_csv(path, issues) for path in paths]
    roles = detect_roles(frames, issues)
    assert "pv" in roles
    unused = [item for item in issues.warnings if item.code == "UNUSED_REGISTERS"]
    assert unused
    registers = {entry["register"] for entry in unused[0].details["unused"]}
    assert "Hulpverbruik Actief" in registers
    assert "Afname Reactief" in registers


def test_ambiguous_second_pv_series_is_fatal(tmp_path: Path):
    from tests.helpers import rows_for_series

    extra = rows_for_series(
        STARTS,
        [9.0, 9.0],
        ["Gevalideerd", "Gevalideerd"],
        register="Productie Actief",
        ean="999",
    )
    write_role_export(tmp_path / "offtake.csv", "offtake", STARTS, [1.0, 1.0], ean="100")
    write_role_export(tmp_path / "injection.csv", "injection", STARTS, [0.0, 0.0], ean="200")
    write_role_export(
        tmp_path / "pv.csv",
        "pv",
        STARTS,
        [0.4, 0.4],
        ean="300",
        extra_rows=extra,
    )
    issues = IssueLog()
    frames = [
        read_fluvius_csv(tmp_path / "offtake.csv", issues),
        read_fluvius_csv(tmp_path / "injection.csv", issues),
        read_fluvius_csv(tmp_path / "pv.csv", issues),
    ]
    detect_roles(frames, issues)
    codes = [item.code for item in issues.fatals]
    assert "AMBIGUOUS_REGISTER" in codes


def test_wrong_unit_is_fatal(tmp_path: Path):
    from tests.helpers import rows_for_series, write_fluvius_csv

    rows = rows_for_series(STARTS, [1.0, 1.0], ["Gevalideerd"] * 2, register="Afname Actief")
    rows[0]["Eenheid"] = "kW"
    rows[1]["Eenheid"] = "kW"
    write_fluvius_csv(tmp_path / "offtake.csv", rows)
    write_role_export(tmp_path / "injection.csv", "injection", STARTS, [0.0, 0.0])
    write_role_export(tmp_path / "pv.csv", "pv", STARTS, [0.0, 0.0])
    issues = IssueLog()
    frames = [read_fluvius_csv(path, issues) for path in tmp_path.glob("*.csv")]
    detect_roles(frames, issues)
    assert any(item.code == "UNEXPECTED_UNIT" for item in issues.fatals)


def test_missing_required_column_is_fatal(tmp_path: Path):
    path = tmp_path / "bad.csv"
    path.write_text("Van (datum);Volume\n01-01-2024;1,000\n", encoding="utf-8")
    issues = IssueLog()
    assert read_fluvius_csv(path, issues) is None
    assert any(item.code == "MISSING_COLUMNS" for item in issues.fatals)
