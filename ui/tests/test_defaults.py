from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ui.services.defaults import REASON_DEFAULTS, load_defaults_snapshot


def _loaded(**overrides: object) -> SimpleNamespace:
    payload = {
        "battery": {
            "usable_energy_kwh": 77.0,
            "charge_power_kw": 33.0,
            "discharge_power_kw": 33.0,
            "charge_efficiency": 0.9,
            "discharge_efficiency": 0.8,
            "initial_charge_kwh": 0.0,
            "max_equivalent_full_cycles_per_year": 250.0,
        },
        "tariffs": {
            "customer_sale_eur_per_mwh": 111.0,
            "peak_export_eur_per_mwh": 44.0,
            "offpeak_export_eur_per_mwh": 22.0,
            "peak_start_local": "07:00",
            "peak_end_local": "19:00",
            "weekends_offpeak": True,
            "timezone": "Europe/Brussels",
        },
        "reporting": {
            "seasonal_plots": False,
            "winter_iso_week": 3,
            "spring_iso_week": 19,
            "summer_iso_week": 26,
            "autumn_iso_week": 41,
        },
        "economics": {"estimated_battery_cost_eur_per_kwh": 250.0},
        "sweep": {
            "evaluation_period_years": 8.0,
            "default_durations_hours": [2.0, 4.0],
            "revenue_capture_threshold_pct": 90.0,
        },
    }
    payload.update(overrides)
    return SimpleNamespace(
        payload=lambda: payload,
        path=Path("configs") / "defaults.toml",
        sha256="abc123",
    )


def test_injected_loader_projects_serialisable_mapping() -> None:
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return _loaded()

    snapshot = load_defaults_snapshot(loader=loader)
    assert calls["n"] == 1
    assert snapshot["ok"] is True
    assert snapshot["basename"] == "defaults.toml"
    assert snapshot["signature"] == "defaults.toml:abc123"
    assert snapshot["battery"]["usable_energy_kwh"] == 77.0
    assert snapshot["economics"]["estimated_battery_cost_eur_per_kwh"] == 250.0
    dumped = json.dumps(snapshot)
    assert "abc123" in dumped
    assert ":\\" not in dumped
    assert "configs/defaults.toml" not in dumped or snapshot["basename"] == "defaults.toml"


def test_missing_defaults_block_without_numeric_fallback() -> None:
    from btm_sim.config import ConfigError

    def loader():
        raise ConfigError("missing")

    snapshot = load_defaults_snapshot(loader=loader)
    assert snapshot["ok"] is False
    assert snapshot["error"]["message"] == REASON_DEFAULTS
    assert snapshot["battery"] is None
    json.dumps(snapshot)


def test_adapter_does_not_parse_defaults_file() -> None:
    source = Path("ui/services/defaults.py").read_text(encoding="utf-8")
    assert "100.0" not in source
    assert "EUR 300" not in source
