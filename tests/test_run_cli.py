"""CLI, successful folder layout, and documented failure exit codes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from btm_sim.optimizer.exceptions import OptimizerError
from btm_sim.run.cli import main
from btm_sim.run.request import build_run_request, write_run_request
from btm_sim.run.workflow import run_end_to_end
from tests.helpers import balanced_site, qh_range, write_site

UTC = timezone.utc


def _site(tmp_path: Path, n: int = 4):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), n)
    imp, exp, pv = balanced_site(starts, import_kwh=1.0, export_kwh=2.0, pv_kwh=2.5)
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def _price_table(timestamps, prices) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime_utc": timestamps,
            "da_price_eur_mwh": prices,
            "native_resolution": "PT15M",
            "upsampled_from_hourly": False,
            "source_file": "fixture.csv",
        }
    )


def test_cli_success_writes_one_parquet_and_no_raw_fluvius(tmp_path: Path, capsys):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "run"
    code = main(
        [
            str(offtake),
            str(injection),
            str(pv),
            "--period",
            "common",
            "--output-dir",
            str(out),
            "--no-seasonal-plots",
            "--site-label",
            "Synthetic",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Reading and checking the three Fluvius files" in captured.out
    assert "Run completed" in captured.out
    summary = json.loads((out / "comparison_summary.json").read_text(encoding="utf-8"))
    assert summary["artifact_schema_version"] == 2
    assert list(summary["scenario_order"])[-1] == "dynamic_injection"
    assert (out / "normalized_input.parquet").exists()
    assert (out / "validation_report.json").exists()
    assert (out / "dynamic_injection_prices.parquet").exists()
    assert (out / "run_request.json").exists()
    assert (out / "run_status.json").exists()
    assert len(list(out.glob("normalized_input*.parquet"))) == 1
    assert not (out / offtake.name).exists()
    assert not (out / injection.name).exists()
    assert not (out / pv.name).exists()
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"


def test_cli_frozen_request_path(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "from_request",
        cli={"seasonal_plots": False},
    )
    frozen = write_run_request(request, tmp_path / "run_request.json")
    code = main(["--request", str(frozen)])
    assert code == 0
    assert (tmp_path / "from_request" / "comparison_summary.json").exists()


def test_missing_input_exit_2_leaves_job_files(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "failed"
    code = main(
        [
            str(offtake),
            str(injection),
            str(tmp_path / "missing.csv"),
            "--period",
            "common",
            "--output-dir",
            str(out),
        ]
    )
    assert code == 2
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_category"]
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "FAILED" in log or "not found" in log.lower() or "ERROR" in log


def test_invalid_period_exit_2(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "period"
    code = main(
        [
            str(offtake),
            str(injection),
            str(pv),
            "--period",
            "1999",
            "--output-dir",
            str(out),
            "--no-seasonal-plots",
        ]
    )
    assert code == 2
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_category"] == "invalid_period"
    assert (out / "run_request.json").exists()
    assert (out / "run.log").exists()


def test_price_coverage_exit_2(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    table = _price_table(pd.DatetimeIndex(starts[:3], tz="UTC"), [10.0, 11.0, 12.0])
    prices = tmp_path / "prices.parquet"
    table.to_parquet(prices, index=False)
    out = tmp_path / "prices_fail"
    code = main(
        [
            str(offtake),
            str(injection),
            str(pv),
            "--period",
            "common",
            "--output-dir",
            str(out),
            "--dynamic-injection-prices",
            str(prices),
            "--no-seasonal-plots",
        ]
    )
    assert code == 2
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_category"] == "price_coverage"


def test_optimizer_failure_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    offtake, injection, pv = _site(tmp_path)

    def boom(*_args, **_kwargs):
        raise OptimizerError("forced solver failure", status="INTERRUPTED")

    monkeypatch.setattr("btm_sim.compare.runner.optimize_self_consumption", boom)
    out = tmp_path / "opt_fail"
    code = main(
        [
            str(offtake),
            str(injection),
            str(pv),
            "--period",
            "common",
            "--output-dir",
            str(out),
            "--no-seasonal-plots",
        ]
    )
    assert code == 1
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_category"] == "optimizer"
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "forced solver failure" in log or "INTERRUPTED" in log or "ERROR" in log


def test_artifact_write_failure_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    offtake, injection, pv = _site(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("btm_sim.compare.runner.write_run_directory", boom)
    out = tmp_path / "write_fail"
    code = main(
        [
            str(offtake),
            str(injection),
            str(pv),
            "--period",
            "common",
            "--output-dir",
            str(out),
            "--no-seasonal-plots",
        ]
    )
    assert code == 1
    status = json.loads((out / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"


def test_detailed_solver_output_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    offtake, injection, pv = _site(tmp_path)
    flags: list[int] = []
    real = None

    def wrap(frame, config, **kwargs):
        flags.append(int(kwargs.get("output_flag", 0)))
        return real(frame, config, **kwargs)

    import btm_sim.compare.runner as runner

    real = runner.optimize_self_consumption
    monkeypatch.setattr(runner, "optimize_self_consumption", wrap)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "flag0",
        cli={"seasonal_plots": False},
    )
    run_end_to_end(request)
    assert flags == [0]

    flags.clear()
    request2 = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "flag1",
        detailed_solver_output=True,
        cli={"seasonal_plots": False},
    )
    run_end_to_end(request2)
    assert flags == [1]


def test_cli_rejects_mixed_request_and_files(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "x",
    )
    frozen = write_run_request(request, tmp_path / "req.json")
    code = main(["--request", str(frozen), str(offtake), str(injection), str(pv)])
    assert code == 2
