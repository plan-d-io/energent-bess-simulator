import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from btm_sim.battery.cli import main
from btm_sim.fluvius.constants import CANONICAL_COLUMNS, INTERVAL_HOURS

UTC = timezone.utc


def _parquet(path: Path) -> Path:
    start = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
    rows = []
    for index, (imp, exp, pv) in enumerate(((0.0, 1.0, 1.0), (1.0, 0.0, 0.0))):
        ts = pd.Timestamp(start) + pd.Timedelta(minutes=15 * index)
        rows.append(
            {
                "timestamp_utc": ts,
                "timestamp_local": ts.tz_convert("Europe/Brussels"),
                "interval_hours": INTERVAL_HOURS,
                "grid_import_baseline_kwh": imp,
                "grid_export_baseline_kwh": exp,
                "pv_production_kwh": pv,
                "site_load_kwh": pv + imp - exp,
                "offtake_quality": "validated",
                "injection_quality": "validated",
                "pv_quality": "validated",
                "quality_flag": "validated",
                "pv_source": "measured_fluvius",
            }
        )
    frame = pd.DataFrame(rows)
    assert list(frame.columns) == list(CANONICAL_COLUMNS)
    frame.to_parquet(path, index=False)
    return path


def test_reference_cli_writes_dispatch_and_summary(tmp_path: Path):
    parquet = _parquet(tmp_path / "normalized_input.parquet")
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
            "0.9",
            "--eta-discharge",
            "0.8",
        ]
    )
    assert code == 0
    summary = json.loads((out / "reference_summary.json").read_text(encoding="utf-8"))
    assert (out / "reference_dispatch.csv").exists()
    assert summary["ok"] is True
    assert summary["label"] == "diagnostic_reference"
    assert summary["not_upper_bound"] is True
    assert summary["battery"]["p_charge_kw"] == 8
    assert summary["battery"]["p_discharge_kw"] == 8
    assert summary["soc_initial_kwh"] == 0.0
    dispatch = pd.read_csv(out / "reference_dispatch.csv")
    assert "charge_pv_kwh" in dispatch.columns
    assert "grid_import_baseline_kwh" in dispatch.columns
