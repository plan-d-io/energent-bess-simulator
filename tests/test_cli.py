import json
from datetime import datetime, timezone
from pathlib import Path

from btm_sim.cli import build_parser, main
from btm_sim.fluvius.constants import CANONICAL_COLUMNS
from tests.helpers import balanced_site, qh_range, write_site

UTC = timezone.utc


def test_cli_writes_parquet_and_report(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)
    out = tmp_path / "run"
    code = main([str(path) for path in paths] + ["-o", str(out), "--period", "common"])
    assert code == 0
    parquet = out / "normalized_input.parquet"
    report = json.loads((out / "validation_report.json").read_text(encoding="utf-8"))
    assert parquet.exists()
    assert report["ok"] is True
    assert report["selected_period"]["id"] == "common"
    import pandas as pd

    frame = pd.read_parquet(parquet)
    assert list(frame.columns) == list(CANONICAL_COLUMNS)
    assert len(frame) == 4


def test_cli_list_periods_skips_parquet(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts)
    paths = write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)
    out = tmp_path / "run"
    code = main([str(path) for path in paths] + ["-o", str(out), "--list-periods"])
    assert code == 0
    assert not (out / "normalized_input.parquet").exists()
    report = json.loads((out / "validation_report.json").read_text(encoding="utf-8"))
    assert any(period["id"] == "common" for period in report["periods"])


def test_site_boundary_help_does_not_require_simultaneous_ack():
    help_text = " ".join(build_parser().format_help().split())
    assert "--acknowledge-site-boundary" in help_text
    assert "simultaneous import and export" in help_text
    assert "does not require this flag" in help_text
