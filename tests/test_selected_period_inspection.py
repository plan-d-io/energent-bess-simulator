"""Structured selected-period inspection for site-boundary preflight."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from btm_sim.fluvius.pipeline import normalize_fluvius
from btm_sim.fluvius.validate import requires_site_boundary_acknowledgement
from btm_sim.sweep import inspect_selected_period, preflight_sweep_candidates
from btm_sim.sweep.exceptions import SweepRequestError
from btm_sim.sweep.site import SelectedPeriodInspection, SiteAnalysis
from tests.helpers import qh_range, write_site

UTC = timezone.utc


def _negative_load_site(tmp_path: Path, *, statuses: str | list[str] = "Gevalideerd"):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    return write_site(
        tmp_path,
        starts,
        import_kwh=[0.0, 0.0],
        export_kwh=[2.0, 2.0],
        pv_kwh=[0.0, 0.0],
        statuses=statuses,
    )


def _boundary_with_shifting(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    return write_site(
        tmp_path,
        starts,
        import_kwh=[2.0, 0.0],
        export_kwh=[0.0, 2.0],
        pv_kwh=[0.0, 0.5],
    )


def _export_exceeds_site(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 2)
    return write_site(
        tmp_path,
        starts,
        import_kwh=[1.5, 1.5],
        export_kwh=[2.0, 2.0],
        pv_kwh=[1.0, 1.0],
    )


def _balanced_site(tmp_path: Path):
    starts = qh_range(datetime(2024, 6, 1, 10, 0, tzinfo=UTC), 4)
    return write_site(
        tmp_path,
        starts,
        import_kwh=[2.0, 0.0, 1.0, 0.0],
        export_kwh=[0.0, 3.0, 0.0, 1.0],
        pv_kwh=[0.0, 3.0, 0.0, 1.0],
    )


def test_requires_ack_only_for_site_boundary_codes():
    assert requires_site_boundary_acknowledgement(["NEGATIVE_LOAD"]) is True
    assert requires_site_boundary_acknowledgement(["EXPORT_EXCEEDS_PV"]) is True
    assert requires_site_boundary_acknowledgement(["NEGATIVE_LOAD", "EXPORT_EXCEEDS_PV"]) is True
    assert requires_site_boundary_acknowledgement(["UNVALIDATED_NOT_ALLOWED"]) is False
    assert requires_site_boundary_acknowledgement(["NEGATIVE_LOAD", "UNVALIDATED_NOT_ALLOWED"]) is False
    assert requires_site_boundary_acknowledgement([]) is False


def test_negative_load_inspection_is_structured_and_json_serializable(tmp_path: Path):
    paths = _negative_load_site(tmp_path)
    inspection = inspect_selected_period(paths, "common")
    assert isinstance(inspection, SelectedPeriodInspection)
    assert inspection.ok is False
    assert inspection.requires_site_boundary_acknowledgement is True
    assert inspection.site_analysis is None
    assert inspection.automatic_candidates == ()
    codes = [item["code"] for item in inspection.fatal]
    assert "NEGATIVE_LOAD" in codes
    negative = next(item for item in inspection.fatal if item["code"] == "NEGATIVE_LOAD")
    details = negative["details"]
    assert details["count"] == 2
    assert details["min_kwh"] == pytest.approx(-2.0)
    assert details["total_negative_load_kwh"] == pytest.approx(4.0)
    assert details["first_local_timestamp"]
    assert details["last_local_timestamp"]
    assert details["affected_local_dates"]
    assert details["examples"]
    payload = json.loads(json.dumps(inspection.to_dict()))
    assert payload["requires_site_boundary_acknowledgement"] is True
    assert payload["site_analysis"] is None


def test_acknowledgement_returns_candidates_and_keeps_raw_values(tmp_path: Path):
    paths = _boundary_with_shifting(tmp_path)
    blocked = inspect_selected_period(paths, "common")
    assert blocked.ok is False
    assert blocked.requires_site_boundary_acknowledgement is True
    acked = inspect_selected_period(paths, "common", acknowledge_site_boundary=True)
    assert acked.ok is True
    assert acked.requires_site_boundary_acknowledgement is False
    assert acked.site_analysis is not None
    assert acked.automatic_candidates
    codes = [item["code"] for item in acked.warnings]
    assert "NEGATIVE_LOAD" in codes
    warning = next(item for item in acked.warnings if item["code"] == "NEGATIVE_LOAD")
    assert warning["details"]["acknowledged_site_boundary"] is True
    result = normalize_fluvius(paths, period="common", acknowledge_site_boundary=True)
    assert result.frame["grid_import_baseline_kwh"].tolist() == pytest.approx([2.0, 0.0])
    assert result.frame["grid_export_baseline_kwh"].tolist() == pytest.approx([0.0, 2.0])
    assert result.frame["pv_production_kwh"].tolist() == pytest.approx([0.0, 0.5])
    assert result.frame["site_load_kwh"].tolist() == pytest.approx([2.0, -1.5])


def test_unrelated_fatal_is_not_acknowledgeable(tmp_path: Path):
    paths = _balanced_site(tmp_path)
    inspection = inspect_selected_period(paths, "not-a-period")
    assert inspection.ok is False
    assert inspection.requires_site_boundary_acknowledgement is False
    assert [item["code"] for item in inspection.fatal] == ["UNKNOWN_PERIOD"]


def test_mixed_unvalidated_and_site_boundary_is_not_acknowledgeable(tmp_path: Path):
    paths = _negative_load_site(
        tmp_path,
        statuses=["Gevalideerd", "Ongevalideerd"],
    )
    inspection = inspect_selected_period(paths, "common")
    codes = {item["code"] for item in inspection.fatal}
    assert "NEGATIVE_LOAD" in codes
    assert "UNVALIDATED_NOT_ALLOWED" in codes
    assert inspection.requires_site_boundary_acknowledgement is False


def test_preflight_callers_still_receive_site_analysis(tmp_path: Path):
    paths = _balanced_site(tmp_path)
    analysis = preflight_sweep_candidates(paths, "common", durations_hours=[2.0, 4.0])
    assert isinstance(analysis, SiteAnalysis)
    assert analysis.automatic_candidates


def test_preflight_exception_preserves_structured_issues(tmp_path: Path):
    paths = _export_exceeds_site(tmp_path)
    with pytest.raises(SweepRequestError) as caught:
        preflight_sweep_candidates(paths, "common")
    error = caught.value
    assert error.details["requires_site_boundary_acknowledgement"] is True
    assert any(item["code"] == "EXPORT_EXCEEDS_PV" for item in error.issues)
    export = next(item for item in error.issues if item["code"] == "EXPORT_EXCEEDS_PV")
    assert export["details"]["count"] == 2
    assert export["details"]["threshold_kwh"] == pytest.approx(0.05)
    assert "inspection" in error.details
    json.dumps(error.details["inspection"])
