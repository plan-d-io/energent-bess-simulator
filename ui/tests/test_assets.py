from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
LOGO = ASSETS / "Energent.png"
PLAN_D = ASSETS / "Plan D-small-transparent.png"


def test_contained_logo_exists() -> None:
    assert LOGO.is_file()
    data = LOGO.read_bytes()
    assert data.startswith(b"\x89PNG")
    assert len(data) > 1000


def test_plan_d_asset_exists() -> None:
    assert PLAN_D.is_file()
    data = PLAN_D.read_bytes()
    assert data.startswith(b"\x89PNG")
    assert len(data) > 1000
