"""Central-default and run-TOML sweep configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from btm_sim.config.defaults import load_central_defaults
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_reusable_settings, resolve_simulation_config
from tests.lp_frames import qh_frame

PROJECT_DEFAULTS = Path(__file__).resolve().parents[1] / "configs" / "defaults.toml"


def _write_parquet(path: Path) -> Path:
    qh_frame(
        [{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}]
    ).to_parquet(path, index=False)
    return path


def test_sweep_defaults_are_required_and_cost_is_shared(tmp_path: Path):
    loaded = load_central_defaults()
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    config, audit = resolve_simulation_config(
        cli={"input": parquet, "output_dir": tmp_path / "run"}
    )
    assert not hasattr(config, "sweep")
    assert config.economics.estimated_battery_cost_eur_per_kwh == 300.0
    assert "estimated_battery_cost_eur_per_kwh" not in config.to_dict().get("battery", {})
    assert audit["sweep"].estimated_battery_cost_eur_per_kwh == 300.0
    assert audit["economics"].estimated_battery_cost_eur_per_kwh == 300.0
    assert audit["resolved"]["economics"]["estimated_battery_cost_eur_per_kwh"] == 300.0
    assert audit["resolved"]["sweep"]["evaluation_period_years"] == 10.0
    assert loaded.economics.estimated_battery_cost_eur_per_kwh == 300.0
    assert loaded.sweep.default_durations_hours == (2.0, 4.0)
    assert audit["value_sources"]["economics"]["estimated_battery_cost_eur_per_kwh"] == "defaults_toml"
    assert audit["value_sources"]["sweep"]["estimated_battery_cost_eur_per_kwh"] == "defaults_toml"


def test_sweep_cli_beats_run_toml_and_defaults(tmp_path: Path):
    defaults = tmp_path / "defaults.toml"
    defaults.write_text(
        PROJECT_DEFAULTS.read_text(encoding="utf-8").replace(
            "estimated_battery_cost_eur_per_kwh = 300.0",
            "estimated_battery_cost_eur_per_kwh = 250.0",
            1,
        ),
        encoding="utf-8",
    )
    run_toml = tmp_path / "run.toml"
    run_toml.write_text(
        """
[sweep]
estimated_battery_cost_eur_per_kwh = 280.0
evaluation_period_years = 8.0
""",
        encoding="utf-8",
    )
    audit = resolve_reusable_settings(
        toml_path=run_toml,
        defaults_path=defaults,
        cli={"estimated_battery_cost_eur_per_kwh": 310.0, "default_durations_hours": [1.0, 6.0]},
        cwd=tmp_path,
    )
    assert audit["sweep"].estimated_battery_cost_eur_per_kwh == 310.0
    assert audit["economics"].estimated_battery_cost_eur_per_kwh == 310.0
    assert audit["sweep"].evaluation_period_years == 8.0
    assert audit["sweep"].default_durations_hours == (1.0, 6.0)
    assert audit["value_sources"]["economics"]["estimated_battery_cost_eur_per_kwh"] == "cli"
    assert audit["value_sources"]["sweep"]["estimated_battery_cost_eur_per_kwh"] == "cli"
    assert audit["value_sources"]["sweep"]["evaluation_period_years"] == "run_toml"
    assert audit["value_sources"]["sweep"]["revenue_capture_threshold_pct"] == "defaults_toml"


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("estimated_battery_cost_eur_per_kwh = 300.0", "estimated_battery_cost_eur_per_kwh = 0.0", "estimated_battery_cost"),
        ("evaluation_period_years = 10.0", "evaluation_period_years = 0.0", "evaluation_period_years"),
        ("default_durations_hours = [2.0, 4.0]", "default_durations_hours = []", "default_durations_hours"),
        ("default_durations_hours = [2.0, 4.0]", "default_durations_hours = [2.0, 2.0]", "unique"),
        ("revenue_capture_threshold_pct = 95.0", "revenue_capture_threshold_pct = 0.0", "revenue_capture_threshold"),
        ("revenue_capture_threshold_pct = 95.0", "revenue_capture_threshold_pct = 101.0", "revenue_capture_threshold"),
    ],
)
def test_invalid_sweep_defaults_are_rejected(tmp_path: Path, key: str, value: str, match: str):
    path = tmp_path / "defaults.toml"
    path.write_text(PROJECT_DEFAULTS.read_text(encoding="utf-8").replace(key, value, 1), encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        load_central_defaults(path)


def test_old_sweep_toml_cost_alias_overrides_defaults(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    run_toml = tmp_path / "run.toml"
    run_toml.write_text(
        """
[sweep]
estimated_battery_cost_eur_per_kwh = 280.0
""",
        encoding="utf-8",
    )
    config, audit = resolve_simulation_config(
        toml_path=run_toml,
        cli={"input": parquet, "output_dir": tmp_path / "run"},
        cwd=tmp_path,
    )
    assert config.economics.estimated_battery_cost_eur_per_kwh == 280.0
    assert audit["sweep"].estimated_battery_cost_eur_per_kwh == 280.0
    assert audit["value_sources"]["economics"]["estimated_battery_cost_eur_per_kwh"] == "run_toml"
    assert audit["value_sources"]["sweep"]["estimated_battery_cost_eur_per_kwh"] == "run_toml"


def test_conflicting_economics_and_sweep_cost_values_are_rejected(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    run_toml = tmp_path / "run.toml"
    run_toml.write_text(
        """
[economics]
estimated_battery_cost_eur_per_kwh = 250.0

[sweep]
estimated_battery_cost_eur_per_kwh = 280.0
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Conflicting estimated_battery_cost_eur_per_kwh"):
        resolve_simulation_config(
            toml_path=run_toml,
            cli={"input": parquet, "output_dir": tmp_path / "run"},
            cwd=tmp_path,
        )


def test_matching_duplicate_cost_values_are_accepted(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    run_toml = tmp_path / "run.toml"
    run_toml.write_text(
        """
[economics]
estimated_battery_cost_eur_per_kwh = 280.0

[sweep]
estimated_battery_cost_eur_per_kwh = 280.0
""",
        encoding="utf-8",
    )
    config, audit = resolve_simulation_config(
        toml_path=run_toml,
        cli={"input": parquet, "output_dir": tmp_path / "run"},
        cwd=tmp_path,
    )
    assert config.economics.estimated_battery_cost_eur_per_kwh == 280.0
    assert audit["value_sources"]["economics"]["estimated_battery_cost_eur_per_kwh"] == "run_toml"


def test_cost_in_central_sweep_section_is_unknown(tmp_path: Path):
    path = tmp_path / "defaults.toml"
    text = PROJECT_DEFAULTS.read_text(encoding="utf-8")
    text = text.replace("[sweep]\n", "[sweep]\nestimated_battery_cost_eur_per_kwh = 300.0\n", 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key"):
        load_central_defaults(path)
