"""Automatic site metrics and 1/2/5 × 10^n candidate generation."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from btm_sim.sweep.site import (
    CANDIDATE_GENERATION_METHOD,
    analyse_site,
    ceil_engineering_step,
    generate_power_grid,
    lower_engineering_powers,
)
from tests.lp_frames import qh_frame

UTC = timezone.utc


def _example_frame():
    return qh_frame(
        [
            {"imp": 2.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 3.0, "pv": 3.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
        ],
        start=datetime(2024, 6, 1, 10, 0, tzinfo=UTC),
    )


def test_engineering_step_rounds_up_to_125_with_5kw_floor():
    assert ceil_engineering_step(12.0) == 20.0
    assert ceil_engineering_step(4.0) == 5.0
    assert ceil_engineering_step(6.0) == 10.0
    assert ceil_engineering_step(50.0) == 50.0
    assert ceil_engineering_step(1.993) == 5.0
    assert ceil_engineering_step(5.0) == 5.0


def test_automatic_site_metrics_and_power_grid_are_exact():
    analysis = analyse_site(_example_frame(), [2.0, 4.0])
    assert analysis.quantile_method == "linear"
    assert analysis.has_positive_import is True
    assert analysis.has_positive_surplus is True
    assert analysis.no_revenue_shifting_opportunity is False
    assert analysis.max_import_kw == 8.0
    assert analysis.max_surplus_kw == 12.0
    assert analysis.p995_import_kw == pytest.approx(np.quantile([4.0, 8.0], 0.995, method="linear"))
    assert analysis.p995_surplus_kw == pytest.approx(np.quantile([4.0, 12.0], 0.995, method="linear"))
    assert analysis.total_import_kwh == 3.0
    assert analysis.total_surplus_kwh == 4.0
    assert analysis.n_local_days == 1
    assert analysis.median_daily_import_kwh == 3.0
    assert analysis.p95_daily_import_kwh == 3.0
    assert analysis.median_daily_surplus_kwh == 4.0
    assert analysis.p95_daily_surplus_kwh == 4.0
    assert analysis.reference_power_kw == pytest.approx(max(analysis.p995_import_kw, analysis.p995_surplus_kw))
    assert analysis.power_step_kw == 5.0
    assert analysis.rounded_reference_power_kw == 15.0
    assert analysis.power_grid_kw == (5.0, 10.0, 15.0, 20.0)
    assert analysis.candidate_generation_method == CANDIDATE_GENERATION_METHOD
    assert [item.power_kw for item in analysis.automatic_candidates if item.duration_hours == 2.0] == [
        5.0,
        10.0,
        15.0,
        20.0,
    ]
    energies_2h = [item.usable_energy_kwh for item in analysis.automatic_candidates if item.duration_hours == 2.0]
    assert energies_2h == [10.0, 20.0, 30.0, 40.0]
    oversized = [item for item in analysis.automatic_candidates if item.usable_energy_kwh > 4.0]
    assert oversized
    assert all(item.exceeds_p95_daily_pv_surplus for item in oversized)


def test_lower_engineering_sequence_for_main_steps_5_20_and_50():
    assert lower_engineering_powers(5.0) == (5.0,)
    assert lower_engineering_powers(20.0) == (5.0, 10.0, 20.0)
    assert lower_engineering_powers(50.0) == (5.0, 10.0, 20.0, 50.0)
    assert 1.0 not in lower_engineering_powers(50.0)
    assert 2.0 not in lower_engineering_powers(50.0)


def test_generate_power_grid_adds_lower_range_and_one_extra_step():
    powers, step, rounded = generate_power_grid(47.2)
    assert step == 10.0
    assert rounded == 50.0
    assert powers == (5.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
    assert powers[-1] == rounded + step


def test_generate_power_grid_ganda_like_reference_keeps_50kw_step_and_300kw_guard():
    powers, step, rounded = generate_power_grid(207.9)
    assert step == 50.0
    assert rounded == 250.0
    assert powers == (5.0, 10.0, 20.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0)
    assert powers[-1] == 300.0


def test_power_grid_is_sorted_unique_and_starts_at_5kw():
    powers, _step, _rounded = generate_power_grid(207.9)
    assert powers == tuple(sorted(set(powers)))
    assert powers[0] == 5.0
    assert len(powers) == len(set(powers))


def test_no_import_or_no_surplus_sets_diagnostic():
    no_import = qh_frame([{"imp": 0.0, "exp": 1.0, "pv": 1.0}, {"imp": 0.0, "exp": 2.0, "pv": 2.0}])
    none_in = analyse_site(no_import, [2.0])
    assert none_in.no_revenue_shifting_opportunity is True
    assert none_in.diagnostic == "no_revenue_shifting_opportunity"
    assert none_in.automatic_candidates == ()
    no_surplus = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}, {"imp": 2.0, "exp": 0.0, "pv": 0.0}])
    none_out = analyse_site(no_surplus, [2.0])
    assert none_out.no_revenue_shifting_opportunity is True
    assert none_out.automatic_candidates == ()
