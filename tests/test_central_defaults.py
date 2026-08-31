"""Central defaults discovery, merge precedence, and audit copies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from btm_sim.compare.cli import build_parser, main as compare_main
from btm_sim.config.defaults import load_central_defaults, standard_defaults_path
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_simulation_config
from tests.lp_frames import qh_frame

PROJECT_DEFAULTS = Path(__file__).resolve().parents[1] / "configs" / "defaults.toml"
EXAMPLE_TOML = Path(__file__).resolve().parents[1] / "configs" / "example.toml"


def _write_parquet(path: Path) -> Path:
    qh_frame(
        [{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}]
    ).to_parquet(path, index=False)
    return path


def _complete_defaults_text() -> str:
    return PROJECT_DEFAULTS.read_text(encoding="utf-8")


def test_standard_defaults_path_is_stable_when_cwd_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    expected = standard_defaults_path()
    assert expected == PROJECT_DEFAULTS.resolve()
    monkeypatch.chdir(tmp_path)
    assert standard_defaults_path() == expected
    loaded = load_central_defaults()
    assert loaded.path == expected
    assert loaded.battery.e_usable_kwh == 100.0
    assert loaded.tariffs.customer_sale_eur_per_mwh == 130.0


def test_explicit_defaults_path_is_supported(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    nested = tmp_path / "alt"
    nested.mkdir()
    defaults = nested / "custom.toml"
    defaults.write_text(
        _complete_defaults_text().replace("usable_energy_kwh = 100.0", "usable_energy_kwh = 150.0", 1),
        encoding="utf-8",
    )
    config, audit = resolve_simulation_config(
        cli={"input": parquet, "output_dir": tmp_path / "run"},
        defaults_path="alt/custom.toml",
        cwd=tmp_path,
    )
    assert config.battery.e_usable_kwh == 150.0
    assert Path(audit["defaults_path"]).resolve() == defaults.resolve()
    assert config.battery.p_charge_kw == 50.0
    assert audit["value_sources"]["battery"]["usable_energy_kwh"] == "defaults_toml"


def test_defaults_reject_input_output_unknown_missing_malformed_and_invalid(tmp_path: Path):
    complete = _complete_defaults_text()
    with_input = tmp_path / "with_input.toml"
    with_input.write_text(complete + "\n[input]\nnormalized_parquet = \"x.parquet\"\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must not define \\[input\\] or \\[output\\]"):
        load_central_defaults(with_input)

    unknown_section = tmp_path / "unknown_section.toml"
    unknown_section.write_text(complete + "\n[other]\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown section \\[other\\]"):
        load_central_defaults(unknown_section)

    unknown_key = tmp_path / "unknown_key.toml"
    unknown_key.write_text(complete.replace("[battery]", "[battery]\nunknown = 1", 1), encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key"):
        load_central_defaults(unknown_key)

    missing = tmp_path / "missing.toml"
    missing.write_text(complete.replace('timezone = "Europe/Brussels"\n', ""), encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required setting"):
        load_central_defaults(missing)

    malformed = tmp_path / "bad.toml"
    malformed.write_text("[[[not toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid TOML in central defaults file"):
        load_central_defaults(malformed)

    invalid = tmp_path / "invalid.toml"
    invalid.write_text(complete.replace("charge_efficiency = 0.95", "charge_efficiency = 0.0"), encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid central default `battery.charge_efficiency`"):
        load_central_defaults(invalid)

    bad_time = tmp_path / "bad_time.toml"
    bad_time.write_text(complete.replace('peak_start_local = "08:00"', 'peak_start_local = "7am"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid central default `tariffs.peak_start_local`: expected HH:MM"):
        load_central_defaults(bad_time)

    missing_file = tmp_path / "nope.toml"
    with pytest.raises(ConfigError, match="Central defaults file not found"):
        load_central_defaults(missing_file)


def test_run_toml_inherits_unspecified_values_and_overrides_one_key(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    run_toml = tmp_path / "site.toml"
    run_toml.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"

[output]
directory = "{(tmp_path / "run").as_posix()}"

[battery]
usable_energy_kwh = 150.0
""",
        encoding="utf-8",
    )
    config, audit = resolve_simulation_config(toml_path=run_toml)
    assert config.battery.e_usable_kwh == 150.0
    assert config.battery.p_charge_kw == 50.0
    assert config.battery.eta_charge == 0.95
    assert config.tariffs.customer_sale_eur_per_mwh == 130.0
    assert config.reporting.winter_iso_week == 3
    assert audit["value_sources"]["battery"]["usable_energy_kwh"] == "run_toml"
    assert audit["value_sources"]["battery"]["charge_power_kw"] == "defaults_toml"
    assert audit["value_sources"]["tariffs"]["peak_export_eur_per_mwh"] == "defaults_toml"


def test_cli_overrides_both_toml_layers(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    defaults = tmp_path / "defaults.toml"
    defaults.write_text(
        _complete_defaults_text().replace(
            "customer_sale_eur_per_mwh = 130.0", "customer_sale_eur_per_mwh = 110.0", 1
        ),
        encoding="utf-8",
    )
    run_toml = tmp_path / "site.toml"
    run_toml.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"
[output]
directory = "{(tmp_path / "run").as_posix()}"
[tariffs]
customer_sale_eur_per_mwh = 120.0
""",
        encoding="utf-8",
    )
    config, audit = resolve_simulation_config(
        toml_path=run_toml,
        defaults_path=defaults,
        cli={"customer_rate": 140.0},
    )
    assert config.tariffs.customer_sale_eur_per_mwh == 140.0
    assert audit["value_sources"]["tariffs"]["customer_sale_eur_per_mwh"] == "cli"
    assert config.tariffs.peak_export_eur_per_mwh == 60.0


def test_symmetric_power_respects_cli_specificity(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    run_toml = tmp_path / "site.toml"
    run_toml.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"
[output]
directory = "{(tmp_path / "run").as_posix()}"
[battery]
charge_power_kw = 40.0
discharge_power_kw = 60.0
""",
        encoding="utf-8",
    )
    with_power, audit_power = resolve_simulation_config(toml_path=run_toml, cli={"power": 75.0})
    assert with_power.battery.p_charge_kw == 75.0
    assert with_power.battery.p_discharge_kw == 75.0
    assert audit_power["value_sources"]["battery"]["charge_power_kw"] == "cli"

    specific, audit_specific = resolve_simulation_config(
        toml_path=run_toml,
        cli={"power": 75.0, "p_charge": 80.0},
    )
    assert specific.battery.p_charge_kw == 80.0
    assert specific.battery.p_discharge_kw == 75.0
    assert audit_specific["value_sources"]["battery"]["charge_power_kw"] == "cli"
    assert audit_specific["value_sources"]["battery"]["discharge_power_kw"] == "cli"


def test_direct_compare_needs_only_input_and_output(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    out = tmp_path / "run"
    code = compare_main([str(parquet), "-o", str(out)])
    assert code == 0
    summary = json.loads((out / "comparison_summary.json").read_text(encoding="utf-8"))
    assert summary["battery"]["e_usable_kwh"] == 100.0
    assert summary["battery"]["p_charge_kw"] == 50.0
    assert summary["tariffs"]["customer_sale_eur_per_mwh"] == 130.0
    assert (out / "source_defaults.toml").exists()
    assert not (out / "source_config.toml").exists()
    meta = json.loads((out / "run_metadata.json").read_text(encoding="utf-8"))
    assert "source_defaults.toml" in meta["filenames"]
    help_text = " ".join(build_parser().format_help().split())
    assert "Starting value comes from the selected central defaults file" in help_text
    assert "default: 130" not in help_text


def test_changing_central_tariff_changes_resolved_config_not_python(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    defaults = tmp_path / "defaults.toml"
    defaults.write_text(
        _complete_defaults_text()
        .replace("customer_sale_eur_per_mwh = 130.0", "customer_sale_eur_per_mwh = 99.0", 1)
        .replace('peak_start_local = "08:00"', 'peak_start_local = "09:00"', 1),
        encoding="utf-8",
    )
    config, _audit = resolve_simulation_config(
        cli={"input": parquet, "output_dir": tmp_path / "run"},
        defaults_path=defaults,
    )
    assert config.tariffs.customer_sale_eur_per_mwh == 99.0
    assert config.tariffs.peak_start_local.hour == 9
    standard = load_central_defaults()
    assert standard.tariffs.customer_sale_eur_per_mwh == 130.0


def test_nonzero_initial_charge_from_any_layer_is_rejected(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    defaults = tmp_path / "defaults.toml"
    defaults.write_text(
        _complete_defaults_text().replace("initial_charge_kwh = 0.0", "initial_charge_kwh = 5.0", 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="initial_charge_kwh = 0"):
        resolve_simulation_config(
            cli={"input": parquet, "output_dir": tmp_path / "run"},
            defaults_path=defaults,
        )


def test_current_defaults_keep_established_numeric_starting_values():
    loaded = load_central_defaults()
    assert loaded.battery.e_usable_kwh == 100.0
    assert loaded.battery.p_charge_kw == 50.0
    assert loaded.battery.p_discharge_kw == 50.0
    assert loaded.battery.eta_charge == 0.95
    assert loaded.battery.eta_discharge == 0.95
    assert loaded.battery.soc_initial_kwh == 0.0
    assert loaded.battery.max_equivalent_full_cycles_per_year == 400.0
    assert loaded.tariffs.customer_sale_eur_per_mwh == 130.0
    assert loaded.tariffs.peak_export_eur_per_mwh == 60.0
    assert loaded.tariffs.offpeak_export_eur_per_mwh == 30.0
    assert loaded.tariffs.weekends_offpeak is True
    assert loaded.reporting.winter_iso_week == 3
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0
    assert loaded.sweep.estimated_battery_cost_eur_per_kwh == 300.0
    assert loaded.sweep.evaluation_period_years == 10.0
    assert loaded.sweep.default_durations_hours == (2.0, 4.0)
    assert loaded.sweep.revenue_capture_threshold_pct == 95.0


def test_simplified_example_toml_remains_runnable(tmp_path: Path):
    local_config_dir = tmp_path / "configs"
    local_config_dir.mkdir()
    local_example = local_config_dir / "example.toml"
    local_example.write_text(EXAMPLE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
    local_input = tmp_path / "outputs" / "example_run" / "normalized_input.parquet"
    local_input.parent.mkdir(parents=True)
    _write_parquet(local_input)

    config, audit = resolve_simulation_config(toml_path=local_example)
    assert config.input_parquet.resolve() == local_input.resolve()
    assert config.battery.e_usable_kwh == 100.0
    assert config.battery.p_charge_kw == 50.0
    assert all(source == "defaults_toml" for source in audit["value_sources"]["battery"].values())
    assert all(source == "defaults_toml" for source in audit["value_sources"]["tariffs"].values())
    assert audit["value_sources"]["input"]["normalized_parquet"] == "run_toml"
    text = EXAMPLE_TOML.read_text(encoding="utf-8")
    assert "usable_energy_kwh = 100.0" not in text
    assert "customer_sale_eur_per_mwh = 130.0" not in text
