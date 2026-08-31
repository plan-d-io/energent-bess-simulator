import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from btm_sim.fluvius.constants import CANONICAL_COLUMNS, INTERVAL_HOURS
from btm_sim.optimizer.revenue_cli import main

UTC = timezone.utc


def test_revenue_cli_writes_outputs_and_defaults_soc_to_zero(tmp_path: Path):
    start = datetime(2024, 1, 3, 6, 45, tzinfo=UTC)
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
    parquet = tmp_path / "normalized_input.parquet"
    pd.DataFrame(rows).to_parquet(parquet, index=False)
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
    summary = json.loads((out / "revenue_summary.json").read_text(encoding="utf-8"))
    assert (out / "revenue_dispatch.csv").exists()
    assert summary["ok"] is True
    assert summary["case"] == "revenue_first"
    assert summary["battery"]["soc_initial_kwh"] == 0.0
    assert "revenue" in summary
    assert list(pd.read_csv(out / "revenue_dispatch.csv").columns[: len(CANONICAL_COLUMNS)]) == list(CANONICAL_COLUMNS)
