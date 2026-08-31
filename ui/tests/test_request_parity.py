from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from btm_sim.config import standard_defaults_path
from btm_sim.market import standard_day_ahead_prices_path
from btm_sim.run import build_run_request, serialize_run_request
from btm_sim.sweep import build_sweep_request, serialize_sweep_request
from tests.helpers import balanced_site, qh_range, write_site

from ui.services.request_intent import (
    builder_kwargs_from_intent,
    mismatches_for_serialized_request,
    ordered_candidate_mappings,
    request_matches_intent,
)
from ui.services.review import build_request_intent
from ui.tests.test_review import freeze_one, freeze_size, ready_review_state

UTC = timezone.utc


def _site(tmp_path: Path) -> tuple[Path, Path, Path]:
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    imp, exp, pv = balanced_site(starts, import_kwh=1.0, export_kwh=2.0, pv_kwh=2.5)
    return write_site(tmp_path, starts, import_kwh=imp, export_kwh=exp, pv_kwh=pv)


def _one_intent() -> dict:
    state = freeze_one(ready_review_state())
    intent = build_request_intent(state)
    intent["period_id"] = "common"
    return intent


def _size_intent() -> dict:
    state = freeze_size(ready_review_state())
    intent = build_request_intent(state)
    intent["period_id"] = "common"
    return intent


def test_one_battery_public_request_matches_intent(tmp_path: Path) -> None:
    paths = _site(tmp_path)
    intent = _one_intent()
    kwargs = builder_kwargs_from_intent(intent)
    request = build_run_request(
        fluvius_paths=paths,
        output_dir=tmp_path / "run",
        job_id="v2-test-run",
        defaults_path=standard_defaults_path(),
        dynamic_injection_prices=standard_day_ahead_prices_path(),
        **kwargs,
    )
    payload = serialize_run_request(request)
    assert mismatches_for_serialized_request(payload, intent) == []
    assert request_matches_intent(payload, intent)


def test_sweep_public_request_matches_intent(tmp_path: Path) -> None:
    paths = _site(tmp_path)
    intent = _size_intent()
    kwargs = builder_kwargs_from_intent(intent)
    request = build_sweep_request(
        fluvius_paths=paths,
        output_dir=tmp_path / "sweep",
        job_id="v2-test-sweep",
        defaults_path=standard_defaults_path(),
        **kwargs,
    )
    payload = serialize_sweep_request(request)
    intent["sizing"]["candidates"] = ordered_candidate_mappings(payload["candidates"])
    assert mismatches_for_serialized_request(payload, intent) == []
    assert request_matches_intent(payload, intent)
    assert len(payload["candidates"]) == len(intent["sizing"]["candidates"])
    assert [item["candidate_id"] for item in payload["candidates"]] == [
        item["candidate_id"] for item in intent["sizing"]["candidates"]
    ]


def test_explicit_sweep_candidates_follow_intent(tmp_path: Path) -> None:
    paths = _site(tmp_path)
    intent = _size_intent()
    intent["sizing"]["core_mode"] = "explicit"
    intent["sizing"]["explicit_text"] = "5, 10\n10, 20"
    kwargs = builder_kwargs_from_intent(intent)
    request = build_sweep_request(
        fluvius_paths=paths,
        output_dir=tmp_path / "sweep",
        job_id="v2-test-explicit",
        defaults_path=standard_defaults_path(),
        **kwargs,
    )
    payload = serialize_sweep_request(request)
    intent["sizing"]["candidates"] = ordered_candidate_mappings(payload["candidates"])
    assert payload["mode"] == "explicit"
    assert payload["explicit_pairs"] == [[5.0, 10.0], [10.0, 20.0]]
    assert mismatches_for_serialized_request(payload, intent) == []


def test_family_changes_are_reported_as_mismatches(tmp_path: Path) -> None:
    paths = _site(tmp_path)
    intent = _one_intent()
    kwargs = builder_kwargs_from_intent(intent)
    payload = serialize_run_request(
        build_run_request(
            fluvius_paths=paths,
            output_dir=tmp_path / "run",
            job_id="v2-test-mismatch",
            defaults_path=standard_defaults_path(),
            dynamic_injection_prices=standard_day_ahead_prices_path(),
            **kwargs,
        )
    )
    cases = [
        ("usable capacity", lambda item: item["one_battery"].__setitem__("usable_kwh", 12.0)),
        ("timezone", lambda item: item["shared"].__setitem__("timezone", "UTC")),
        ("winter iso week", lambda item: item["shared"].__setitem__("winter_iso_week", 99)),
        ("unvalidated flag", lambda item: item.__setitem__("allow_unvalidated", False)),
        ("detailed solver output", lambda item: item.__setitem__("detailed_solver_output", True)),
        ("estimated battery cost", lambda item: item["shared"].__setitem__("cost_eur_per_kwh", 1.0)),
        ("customer PV-sale tariff", lambda item: item["shared"].__setitem__("customer_sale_eur_per_mwh", 1.0)),
    ]
    for label, mutate in cases:
        changed = deepcopy(intent)
        mutate(changed)
        found = mismatches_for_serialized_request(payload, changed)
        assert label in found, found

    size_intent = _size_intent()
    size_kwargs = builder_kwargs_from_intent(size_intent)
    size_payload = serialize_sweep_request(
        build_sweep_request(
            fluvius_paths=paths,
            output_dir=tmp_path / "sweep-mismatch",
            job_id="v2-test-sweep-mismatch",
            defaults_path=standard_defaults_path(),
            **size_kwargs,
        )
    )
    size_intent["sizing"]["candidates"] = ordered_candidate_mappings(size_payload["candidates"])
    reversed_candidates = list(reversed(size_intent["sizing"]["candidates"]))
    changed_size = deepcopy(size_intent)
    changed_size["sizing"]["candidates"] = reversed_candidates
    found_size = mismatches_for_serialized_request(size_payload, changed_size)
    if reversed_candidates != size_intent["sizing"]["candidates"]:
        assert "candidate order" in found_size
    changed_years = deepcopy(size_intent)
    changed_years["sizing"]["evaluation_years"] = 1.0
    assert "evaluation period" in mismatches_for_serialized_request(size_payload, changed_years)
