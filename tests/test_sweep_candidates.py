"""Automatic, manual-range, and explicit candidate modes."""

from __future__ import annotations

import pytest

from btm_sim.sweep.candidates import MAX_CANDIDATES, build_candidates
from btm_sim.sweep.exceptions import SweepRequestError
from btm_sim.sweep.site import analyse_site
from tests.test_sweep_site import _example_frame


def test_automatic_manual_and_explicit_modes():
    analysis = analyse_site(_example_frame(), [2.0, 4.0])
    automatic = build_candidates(
        mode="automatic",
        durations_hours=[2.0, 4.0],
        automatic_candidates=analysis.automatic_candidates,
        site_p95_daily_import_kwh=analysis.p95_daily_import_kwh,
        site_p95_daily_surplus_kwh=analysis.p95_daily_surplus_kwh,
    )
    assert len(automatic.candidates) == 8
    assert automatic.candidates[0].usable_energy_kwh == 10.0
    assert automatic.candidates[0].duration_hours == 2.0

    manual = build_candidates(
        mode="manual_range",
        durations_hours=[2.0],
        automatic_candidates=(),
        site_p95_daily_import_kwh=3.0,
        site_p95_daily_surplus_kwh=4.0,
        min_power_kw=10.0,
        max_power_kw=30.0,
        power_increment_kw=10.0,
    )
    assert [(item.power_kw, item.usable_energy_kwh) for item in manual.candidates] == [
        (10.0, 20.0),
        (20.0, 40.0),
        (30.0, 60.0),
    ]

    explicit = build_candidates(
        mode="explicit",
        durations_hours=[2.0],
        automatic_candidates=(),
        site_p95_daily_import_kwh=3.0,
        site_p95_daily_surplus_kwh=4.0,
        explicit_pairs=[(12.5, 37.5), (12.5, 37.5)],
    )
    assert len(explicit.candidates) == 1
    assert explicit.candidates[0].duration_hours == pytest.approx(3.0)
    assert explicit.removed_duplicates


def test_automatic_fails_when_there_is_no_shifting_opportunity():
    with pytest.raises(SweepRequestError, match="no_revenue_shifting_opportunity"):
        build_candidates(
            mode="automatic",
            durations_hours=[2.0],
            automatic_candidates=(),
            site_p95_daily_import_kwh=None,
            site_p95_daily_surplus_kwh=None,
            no_revenue_shifting_opportunity=True,
        )


def test_candidate_count_limit():
    with pytest.raises(SweepRequestError, match="100"):
        build_candidates(
            mode="manual_range",
            durations_hours=[2.0],
            automatic_candidates=(),
            site_p95_daily_import_kwh=1.0,
            site_p95_daily_surplus_kwh=1.0,
            min_power_kw=5.0,
            max_power_kw=5.0 * (MAX_CANDIDATES + 1),
            power_increment_kw=5.0,
        )


def test_automatic_lower_range_still_respects_100_candidate_limit():
    from btm_sim.sweep.candidates import SweepCandidate
    from btm_sim.sweep.site import generate_power_grid

    powers, _step, _rounded = generate_power_grid(207.9)
    durations = [round(1.0 + 0.1 * index, 1) for index in range(12)]
    automatic = []
    index = 1
    for duration in durations:
        for power in powers:
            energy = power * duration
            automatic.append(
                SweepCandidate(
                    candidate_id=f"c{index:03d}",
                    power_kw=power,
                    usable_energy_kwh=energy,
                    duration_hours=duration,
                    exceeds_p95_daily_pv_surplus=False,
                    exceeds_p95_daily_import=False,
                    source="automatic",
                )
            )
            index += 1
    assert len(automatic) > MAX_CANDIDATES
    with pytest.raises(SweepRequestError, match="100"):
        build_candidates(
            mode="automatic",
            durations_hours=durations,
            automatic_candidates=automatic,
            site_p95_daily_import_kwh=1.0,
            site_p95_daily_surplus_kwh=1.0,
        )


def test_explicit_candidates_remain_available_without_automatic_sizes():
    built = build_candidates(
        mode="explicit",
        durations_hours=[2.0],
        automatic_candidates=(),
        site_p95_daily_import_kwh=None,
        site_p95_daily_surplus_kwh=None,
        explicit_pairs=[(10.0, 20.0)],
        no_revenue_shifting_opportunity=True,
    )
    assert len(built.candidates) == 1
