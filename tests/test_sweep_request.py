"""Frozen sweep request round-trip, hashes, and cwd-independent paths."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from btm_sim.config.defaults import standard_defaults_path
from btm_sim.sweep.exceptions import SweepRequestError
from btm_sim.sweep.request import (
    build_sweep_request,
    load_sweep_request,
    serialize_sweep_request,
    write_sweep_request,
)
from tests.helpers import qh_range, write_site

UTC = timezone.utc


def _site(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp = [2.0, 0.0, 1.0, 0.0]
    exp = [0.0, 3.0, 0.0, 1.0]
    pv = [0.0, 3.0, 0.0, 1.0]
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def test_build_serialize_reload_round_trip(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "sweep",
        site_label="Unit test site",
        mode="explicit",
        explicit_pairs=[(5.0, 10.0), (10.0, 20.0)],
    )
    payload = serialize_sweep_request(request)
    assert payload["request_schema_version"] == 1
    assert payload["artifact_schema_version"] == 1
    assert payload["period_id"] == "common"
    assert len(payload["candidates"]) == 2
    assert payload["candidates"][0]["power_kw"] == 5.0
    assert payload["sweep"]["estimated_battery_cost_eur_per_kwh"] == 300.0
    assert payload["economics"]["estimated_battery_cost_eur_per_kwh"] == 300.0
    path = write_sweep_request(request, tmp_path / "frozen.json")
    loaded = load_sweep_request(path)
    assert loaded.job_id == request.job_id
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0
    assert [item.candidate_id for item in loaded.candidates] == [item.candidate_id for item in request.candidates]
    assert loaded.fluvius_inputs[0].sha256 == request.fluvius_inputs[0].sha256
    assert loaded.sweep.evaluation_period_years == 10.0
    assert loaded.site_analysis.quantile_method == "linear"


def test_frozen_request_keeps_its_existing_candidate_list(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "sweep",
        mode="automatic",
    )
    payload = serialize_sweep_request(request)
    old_candidates = [
        {
            "candidate_id": "c001_50kW_100kWh",
            "power_kw": 50.0,
            "usable_energy_kwh": 100.0,
            "duration_hours": 2.0,
            "exceeds_p95_daily_pv_surplus": False,
            "exceeds_p95_daily_import": False,
            "source": "automatic",
        },
        {
            "candidate_id": "c002_100kW_200kWh",
            "power_kw": 100.0,
            "usable_energy_kwh": 200.0,
            "duration_hours": 2.0,
            "exceeds_p95_daily_pv_surplus": False,
            "exceeds_p95_daily_import": False,
            "source": "automatic",
        },
    ]
    payload["candidates"] = old_candidates
    payload["site_analysis"].pop("candidate_generation_method", None)
    path = tmp_path / "old_sweep_request.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    loaded = load_sweep_request(path)
    assert [item.candidate_id for item in loaded.candidates] == [
        "c001_50kW_100kWh",
        "c002_100kW_200kWh",
    ]
    assert [item.power_kw for item in loaded.candidates] == [50.0, 100.0]


def test_old_sweep_request_without_economics_loads_from_sweep_cost(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "sweep",
        mode="explicit",
        explicit_pairs=[(5.0, 10.0)],
    )
    payload = serialize_sweep_request(request)
    payload.pop("economics")
    path = tmp_path / "legacy_sweep.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    loaded = load_sweep_request(path)
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == loaded.sweep.estimated_battery_cost_eur_per_kwh
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0


def test_cli_overrides_beat_run_toml_for_sweep_cost(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    run_toml = tmp_path / "run.toml"
    run_toml.write_text(
        """
[sweep]
estimated_battery_cost_eur_per_kwh = 280.0
""",
        encoding="utf-8",
    )
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
        run_toml_path=run_toml,
        mode="explicit",
        explicit_pairs=[(5.0, 10.0)],
        cli={"estimated_battery_cost_eur_per_kwh": 330.0},
    )
    assert request.sweep.estimated_battery_cost_eur_per_kwh == 330.0
    assert request.economics.estimated_battery_cost_eur_per_kwh == 330.0
    assert request.value_sources["economics"]["estimated_battery_cost_eur_per_kwh"] == "cli"
    assert request.value_sources["sweep"]["estimated_battery_cost_eur_per_kwh"] == "cli"
    assert request.defaults_path == standard_defaults_path()


def test_changed_fluvius_hash_is_rejected(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_sweep_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
        mode="explicit",
        explicit_pairs=[(5.0, 10.0)],
    )
    offtake.write_text(offtake.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    from btm_sim.sweep.request import validate_frozen_sweep_inputs

    with pytest.raises(SweepRequestError, match="changed after the request was frozen"):
        validate_frozen_sweep_inputs(request)


def test_relative_paths_resolve_against_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    offtake, injection, pv = _site(tmp_path)
    monkeypatch.chdir(tmp_path)
    request = build_sweep_request(
        fluvius_paths=[offtake.name, injection.name, pv.name],
        period_id="common",
        output_dir="sweep_out",
        mode="explicit",
        explicit_pairs=[(5.0, 10.0)],
        cwd=tmp_path,
    )
    assert request.output_dir == (tmp_path / "sweep_out").resolve()
    assert request.fluvius_inputs[0].path == offtake.resolve()
