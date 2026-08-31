"""Request creation, freeze/reload, and configuration precedence."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from btm_sim.config.defaults import standard_defaults_path
from btm_sim.run.exceptions import RunRequestError
from btm_sim.run.request import (
    build_run_request,
    load_run_request,
    serialize_run_request,
    write_run_request,
)
from tests.helpers import balanced_site, qh_range, write_site

UTC = timezone.utc


def _site(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts, import_kwh=1.0, export_kwh=2.0, pv_kwh=2.5)
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def test_build_serialize_reload_round_trip(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "run",
        site_label="Unit test site",
    )
    payload = serialize_run_request(request)
    assert payload["request_schema_version"] == 1
    assert payload["artifact_schema_version"] == 2
    assert payload["period_id"] == "common"
    assert payload["site_label"] == "Unit test site"
    assert len(payload["fluvius_inputs"]) == 3
    assert payload["battery"]["soc_initial_kwh"] == 0.0
    assert payload["economics"]["estimated_battery_cost_eur_per_kwh"] == 300.0
    path = write_run_request(request, tmp_path / "frozen.json")
    loaded = load_run_request(path)
    assert loaded.job_id == request.job_id
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0
    assert loaded.battery.e_usable_kwh == request.battery.e_usable_kwh
    assert loaded.period_id == "common"
    assert loaded.fluvius_inputs[0].sha256 == request.fluvius_inputs[0].sha256
    assert loaded.output_dir == request.output_dir


def test_cli_overrides_beat_run_toml_and_defaults(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    defaults = standard_defaults_path().read_text(encoding="utf-8")
    local_defaults = tmp_path / "defaults.toml"
    local_defaults.write_text(
        defaults.replace("usable_energy_kwh = 100.0", "usable_energy_kwh = 100.0"),
        encoding="utf-8",
    )
    run_toml = tmp_path / "run.toml"
    run_toml.write_text(
        """
[battery]
usable_energy_kwh = 150.0
""",
        encoding="utf-8",
    )
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
        defaults_path=local_defaults,
        run_toml_path=run_toml,
        cli={"e_usable": 80.0},
    )
    assert request.battery.e_usable_kwh == 80.0
    assert request.value_sources["battery"]["usable_energy_kwh"] == "cli"


def test_frozen_request_ignores_later_defaults_edit(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    defaults_text = standard_defaults_path().read_text(encoding="utf-8")
    local_defaults = tmp_path / "defaults.toml"
    local_defaults.write_text(defaults_text, encoding="utf-8")
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
        defaults_path=local_defaults,
    )
    original = request.battery.e_usable_kwh
    frozen = write_run_request(request, tmp_path / "run_request.json")
    local_defaults.write_text(
        defaults_text.replace(
            f"usable_energy_kwh = {original}",
            "usable_energy_kwh = 999.0",
        ),
        encoding="utf-8",
    )
    loaded = load_run_request(frozen)
    assert loaded.battery.e_usable_kwh == original
    assert loaded.battery.e_usable_kwh != 999.0
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0


def test_frozen_request_keeps_resolved_battery_cost(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    defaults_text = standard_defaults_path().read_text(encoding="utf-8")
    local_defaults = tmp_path / "defaults.toml"
    local_defaults.write_text(defaults_text, encoding="utf-8")
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
        defaults_path=local_defaults,
        cli={"estimated_battery_cost_eur_per_kwh": 275.0},
    )
    assert request.economics.estimated_battery_cost_eur_per_kwh == 275.0
    assert request.value_sources["economics"]["estimated_battery_cost_eur_per_kwh"] == "cli"
    frozen = write_run_request(request, tmp_path / "run_request.json")
    local_defaults.write_text(
        defaults_text.replace(
            "estimated_battery_cost_eur_per_kwh = 300.0",
            "estimated_battery_cost_eur_per_kwh = 999.0",
        ),
        encoding="utf-8",
    )
    loaded = load_run_request(frozen)
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 275.0


def test_old_run_request_without_economics_loads_default_cost(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
    )
    payload = serialize_run_request(request)
    payload.pop("economics")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_run_request(path)
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0


def test_paths_resolve_independently_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    offtake, injection, pv = _site(tmp_path)
    out = tmp_path / "run"
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=out,
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    loaded = load_run_request(write_run_request(request, tmp_path / "req.json"))
    assert loaded.fluvius_inputs[0].path.exists()
    assert loaded.output_dir == out.resolve()
    relative = build_run_request(
        fluvius_paths=[offtake.name, injection.name, pv.name],
        period_id="common",
        output_dir="run2",
        cwd=tmp_path,
    )
    assert relative.fluvius_inputs[0].path == offtake.resolve()
    assert relative.output_dir == (tmp_path / "run2").resolve()


def test_missing_file_and_wrong_count_fail(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    with pytest.raises(RunRequestError, match="exactly three"):
        build_run_request(
            fluvius_paths=[offtake, injection],
            period_id="common",
            output_dir=tmp_path / "out",
        )
    with pytest.raises(RunRequestError, match="not found"):
        build_run_request(
            fluvius_paths=[offtake, injection, tmp_path / "missing.csv"],
            period_id="common",
            output_dir=tmp_path / "out",
        )


def test_reload_rejects_unsupported_schema(tmp_path: Path):
    offtake, injection, pv = _site(tmp_path)
    request = build_run_request(
        fluvius_paths=[offtake, injection, pv],
        period_id="common",
        output_dir=tmp_path / "out",
    )
    payload = serialize_run_request(request)
    payload["request_schema_version"] = 99
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RunRequestError, match="request_schema_version"):
        load_run_request(path)
