"""Keep short synthetic tests from treating 400 annual cycles as a few-QH budget."""

from __future__ import annotations

import pytest

from btm_sim.battery.config import BatteryConfig

HIGH_CYCLE_LIMIT = 1_000_000.0


@pytest.fixture(autouse=True)
def _unconstrained_cycles_on_short_synthetic_frames(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Annual 400-cycle default is for real selected periods, not 2–20 interval fixtures.

    Ganda and other slow full-year tests keep the product default of 400.
    """
    if request.node.get_closest_marker("slow") is not None:
        return
    if request.node.name.startswith("test_low_cycle_limit") or request.node.name.startswith(
        "test_rule_based_controller_never_exceeds"
    ):
        return
    original_init = BatteryConfig.__init__

    def patched_init(self, *args, **kwargs):
        if "max_equivalent_full_cycles_per_year" not in kwargs and len(args) < 7:
            kwargs["max_equivalent_full_cycles_per_year"] = HIGH_CYCLE_LIMIT
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(BatteryConfig, "__init__", patched_init)
