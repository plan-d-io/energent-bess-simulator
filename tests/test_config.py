"""TOML/CLI configuration parsing, precedence, and audit output."""

import json
from pathlib import Path

import pytest

from btm_sim.compare.cli import main as compare_main
from btm_sim.config.exceptions import ConfigError
from btm_sim.config.resolve import resolve_simulation_config
from btm_sim.config.schema import TariffConfig
from tests.lp_frames import qh_frame


def _write_parquet(path: Path) -> Path:
    qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 1.0, "exp": 0.0, "pv": 0.0}]).to_parquet(path, index=False)
    return path


def test_unknown_toml_key_is_rejected(tmp_path: Path):
    path = tmp_path / "bad.toml"
    path.write_text("[battery]\nusable_energy_kwh = 10\nunknown = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key"):
        resolve_simulation_config(toml_path=path, cli={"output_dir": tmp_path / "out"})


def test_missing_required_and_both_output_modes(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    path = tmp_path / "cfg.toml"
    path.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"

[output]
root = "out"
directory = "exact"

[battery]
usable_energy_kwh = 10
charge_power_kw = 8
discharge_power_kw = 8
charge_efficiency = 1.0
discharge_efficiency = 1.0
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="only one"):
        resolve_simulation_config(toml_path=path)


def test_toml_relative_paths_resolve_from_file(tmp_path: Path):
    nested = tmp_path / "conf"
    nested.mkdir()
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    toml_path = nested / "site.toml"
    toml_path.write_text(
        """
[input]
normalized_parquet = "../normalized_input.parquet"

[output]
directory = "../run"

[battery]
usable_energy_kwh = 10
charge_power_kw = 8
discharge_power_kw = 8
charge_efficiency = 1.0
discharge_efficiency = 1.0
""",
        encoding="utf-8",
    )
    config, _audit = resolve_simulation_config(toml_path=toml_path)
    assert config.input_parquet.resolve() == parquet.resolve()
    assert config.output_dir.resolve() == (tmp_path / "run").resolve()


def test_cli_overrides_toml_and_defaults(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    toml_path = tmp_path / "site.toml"
    toml_path.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"

[output]
root = "unused"

[battery]
usable_energy_kwh = 100
charge_power_kw = 50
discharge_power_kw = 50
charge_efficiency = 0.9
discharge_efficiency = 0.9
initial_charge_kwh = 0.0

[tariffs]
customer_sale_eur_per_mwh = 200
""",
        encoding="utf-8",
    )
    other = _write_parquet(tmp_path / "other.parquet")
    config, audit = resolve_simulation_config(
        toml_path=toml_path,
        cli={
            "input": other,
            "output_dir": tmp_path / "exact",
            "e_usable": 12.0,
            "customer_rate": 150.0,
        },
        cwd=tmp_path,
    )
    assert config.input_parquet.resolve() == other.resolve()
    assert config.output_dir == tmp_path / "exact"
    assert config.output_root is None
    assert config.battery.e_usable_kwh == 12.0
    assert config.tariffs.customer_sale_eur_per_mwh == 150.0
    assert config.tariffs.peak_export_eur_per_mwh == 60.0
    assert "e_usable" in audit["cli_overrides"]
    assert audit["value_sources"]["battery"]["usable_energy_kwh"] == "cli"
    assert audit["value_sources"]["tariffs"]["customer_sale_eur_per_mwh"] == "cli"
    assert audit["value_sources"]["tariffs"]["peak_export_eur_per_mwh"] == "defaults_toml"
    assert audit["value_sources"]["battery"]["charge_efficiency"] == "run_toml"
    assert audit["defaults_path"]
    assert audit["defaults_sha256"]


def test_nonzero_comparison_soc_is_rejected(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    with pytest.raises(ConfigError, match="initial_charge_kwh = 0"):
        resolve_simulation_config(
            cli={
                "input": parquet,
                "output_dir": tmp_path / "run",
                "e_usable": 10,
                "power": 8,
                "eta_charge": 1,
                "eta_discharge": 1,
                "soc_initial": 5.0,
            }
        )


def test_invalid_timezone_and_nonfinite(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    with pytest.raises(ConfigError, match="timezone"):
        resolve_simulation_config(
            cli={
                "input": parquet,
                "output_dir": tmp_path / "run",
                "e_usable": 10,
                "power": 8,
                "eta_charge": 1,
                "eta_discharge": 1,
                "timezone": "Not/A_Zone",
            }
        )
    with pytest.raises(ConfigError, match="finite"):
        resolve_simulation_config(
            cli={
                "input": parquet,
                "output_dir": tmp_path / "run",
                "e_usable": float("nan"),
                "power": 8,
                "eta_charge": 1,
                "eta_discharge": 1,
            }
        )


def test_compare_cli_config_writes_resolved_and_source(tmp_path: Path):
    parquet = _write_parquet(tmp_path / "normalized_input.parquet")
    toml_path = tmp_path / "site.toml"
    toml_path.write_text(
        f"""
[input]
normalized_parquet = "{parquet.as_posix()}"

[output]
directory = "{(tmp_path / "run").as_posix()}"

[battery]
usable_energy_kwh = 10
charge_power_kw = 8
discharge_power_kw = 8
charge_efficiency = 1.0
discharge_efficiency = 1.0
initial_charge_kwh = 0.0
""",
        encoding="utf-8",
    )
    code = compare_main(["--config", str(toml_path)])
    assert code == 0
    assert (tmp_path / "run" / "resolved_config.json").exists()
    assert (tmp_path / "run" / "source_config.toml").exists()
    assert (tmp_path / "run" / "source_defaults.toml").exists()
    assert (tmp_path / "run" / "comparison_summary.json").exists()
    resolved = json.loads((tmp_path / "run" / "resolved_config.json").read_text(encoding="utf-8"))
    assert resolved["source"]["defaults_path"]
    assert resolved["source"]["defaults_sha256"]
    assert resolved["source"]["run_toml_path"]
    assert resolved["value_sources"]["battery"]["usable_energy_kwh"] == "run_toml"
    assert resolved["value_sources"]["tariffs"]["customer_sale_eur_per_mwh"] == "defaults_toml"
    meta = json.loads((tmp_path / "run" / "run_metadata.json").read_text(encoding="utf-8"))
    assert "source_defaults.toml" in meta["filenames"]
    assert "source_config.toml" in meta["filenames"]


def test_default_tariff_config_matches_docs():
    tariffs = TariffConfig()
    assert tariffs.customer_sale_eur_per_mwh == 130.0
    assert tariffs.peak_export_eur_per_mwh == 60.0
    assert tariffs.offpeak_export_eur_per_mwh == 30.0
    assert tariffs.weekends_offpeak is True
    assert tariffs.timezone == "Europe/Brussels"
