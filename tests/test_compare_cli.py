import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from btm_sim.compare.cli import main
from tests.lp_frames import qh_frame

UTC = timezone.utc


def test_compare_cli_writes_run_directory(tmp_path: Path):
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    parquet = tmp_path / "normalized_input.parquet"
    frame.to_parquet(parquet, index=False)
    out = tmp_path / "run"
    code = main(
        [
            str(parquet),
            "-o",
            str(out),
            "--e-usable",
            "10",
            "--power",
            "8",
            "--eta-charge",
            "1",
            "--eta-discharge",
            "1",
        ]
    )
    assert code == 0
    assert (out / "comparison_summary.json").exists()
    assert (out / "comparison_dispatch.csv").exists()
    assert (out / "comparison_summary.csv").exists()
    assert (out / "monthly_peaks.csv").exists()
    assert (out / "monthly_summary.csv").exists()
    assert (out / "comparison_dispatch.parquet").exists()
    assert (out / "run_metadata.json").exists()
    assert (out / "normalized_input.parquet").exists()
    summary = json.loads((out / "comparison_summary.json").read_text(encoding="utf-8"))
    assert summary["initial_soc_kwh"] == 0.0
    assert summary["ok"] is True
    assert "self_consumption" in summary["scenarios"]
    assert "revenue" in summary["scenarios"]
    assert (out / "resolved_config.json").exists()


def test_compare_cli_timestamped_root(tmp_path: Path, monkeypatch):
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    parquet = tmp_path / "normalized_input.parquet"
    frame.to_parquet(parquet, index=False)
    monkeypatch.setattr(
        "btm_sim.compare.runner._utc_now",
        lambda: datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    )
    code = main(
        [
            str(parquet),
            "--output-root",
            str(tmp_path / "root"),
            "--e-usable",
            "10",
            "--power",
            "8",
            "--eta-charge",
            "1",
            "--eta-discharge",
            "1",
        ]
    )
    assert code == 0
    assert (tmp_path / "root" / "btm_compare_20240601T120000Z" / "comparison_summary.json").exists()
