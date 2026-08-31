"""Read-only validation of completed comparison and sweep result folders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from btm_sim.run import load_run_request, serialize_run_request
from btm_sim.sweep import load_sweep_request, serialize_sweep_request

from ui.services.configure import MODE_SIZE, selected_period_record
from ui.services.paths import KIND_COMPARISON, KIND_SWEEP, is_contained
from ui.services.request_intent import mismatches_for_serialized_request
from ui.services.saved_example import (
    EXPECTED_SWEEP_CANDIDATE_COUNT,
    compare_artifact_dir,
    sweep_artifact_dir,
)

SOURCE_LIVE = "live"
SOURCE_DEMO = "demo"
RESULTS_RECORD_VERSION = 1

SCENARIO_ORDER = (
    "no_battery",
    "reference",
    "self_consumption",
    "peak_reduction",
    "revenue",
    "dynamic_injection",
)

COMPARISON_FILES_LIVE = (
    "normalized_input.parquet",
    "validation_report.json",
    "comparison_summary.json",
    "comparison_summary.csv",
    "monthly_summary.csv",
    "monthly_peaks.csv",
    "comparison_dispatch.csv",
    "comparison_dispatch.parquet",
    "run_metadata.json",
    "run_request.json",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "dynamic_injection_prices.parquet",
)

SWEEP_FILES_LIVE = (
    "normalized_input.parquet",
    "validation_report.json",
    "sweep_summary.json",
    "sweep_summary.csv",
    "sweep_summary.parquet",
    "site_analysis.json",
    "sweep_metadata.json",
    "sweep_request.json",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "resolved_config.json",
)

COMPARISON_FILES_DEMO = (
    "normalized_input.parquet",
    "comparison_summary.json",
    "comparison_summary.csv",
    "monthly_summary.csv",
    "monthly_peaks.csv",
    "comparison_dispatch.parquet",
    "run_metadata.json",
    "dynamic_injection_prices.parquet",
    "resolved_config.json",
)

SWEEP_FILES_DEMO = SWEEP_FILES_LIVE


def required_files(kind: str, *, source: str) -> tuple[str, ...]:
    demo = source == SOURCE_DEMO
    if kind == KIND_SWEEP:
        return SWEEP_FILES_DEMO if demo else SWEEP_FILES_LIVE
    return COMPARISON_FILES_DEMO if demo else COMPARISON_FILES_LIVE


def demo_artifact_dir(kind: str, *, root: Path | None = None) -> Path:
    if kind == KIND_SWEEP:
        return sweep_artifact_dir(root)
    return compare_artifact_dir(root)


def _read_mapping(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _same_dir(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _missing_files(folder: Path, names: Sequence[str]) -> list[str]:
    return [f"missing {name}" for name in names if not (folder / name).is_file()]


def _scenario_issues(summary: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    if summary.get("ok") is not True:
        found.append("comparison summary is not successful")
    if not summary.get("artifact_schema_version"):
        found.append("comparison schema version")
    order = list(summary.get("scenario_order") or [])
    if order != list(SCENARIO_ORDER):
        found.append("scenario order")
    return found


def _sweep_summary_issues(
    summary: Mapping[str, Any],
    *,
    intent: Mapping[str, Any] | None,
    source: str,
) -> list[str]:
    found: list[str] = []
    if summary.get("ok") is not True:
        found.append("sweep summary is not successful")
    if not summary.get("sweep_artifact_schema_version"):
        found.append("sweep schema version")
    recommendation = summary.get("recommendation")
    if not isinstance(recommendation, Mapping) or not recommendation.get("recommendation_kind"):
        found.append("sweep recommendation")
    candidates = [item for item in (summary.get("candidates") or []) if isinstance(item, Mapping)]
    n_candidates = summary.get("n_candidates")
    try:
        reported = int(n_candidates) if n_candidates is not None else len(candidates)
    except (TypeError, ValueError):
        found.append("sweep candidate count")
        reported = -1
    if reported >= 0 and reported != len(candidates):
        found.append("sweep candidate count")
    if source == SOURCE_DEMO and reported != EXPECTED_SWEEP_CANDIDATE_COUNT:
        found.append("saved sweep candidate count")
    if intent is not None:
        expected = [
            str(item.get("candidate_id") or "")
            for item in (intent.get("sizing") or {}).get("candidates") or []
            if isinstance(item, Mapping)
        ]
        if expected:
            actual = [str(item.get("candidate_id") or "") for item in candidates]
            if len(expected) != len(actual):
                found.append("sweep candidate count")
            if expected != actual:
                found.append("sweep candidate order")
    return found


def _status_issues(
    folder: Path,
    *,
    job: Mapping[str, Any] | None,
    source: str,
) -> list[str]:
    payload = _read_mapping(folder / "run_status.json")
    if source == SOURCE_DEMO:
        if payload is None:
            return []
        if str(payload.get("state") or "") != "completed":
            return ["run status"]
        return []
    if payload is None:
        return ["run status"]
    found: list[str] = []
    if str(payload.get("state") or "") != "completed":
        found.append("run status")
    if not payload.get("artifact_schema_version"):
        found.append("artifact schema version")
    if job is not None and str(payload.get("job_id") or "") != str(job.get("job_id") or ""):
        found.append("status job id")
    return found


def _request_issues(
    folder: Path,
    *,
    kind: str,
    job: Mapping[str, Any] | None,
    intent: Mapping[str, Any] | None,
    source: str,
) -> list[str]:
    if source == SOURCE_DEMO and kind == KIND_COMPARISON:
        return []
    name = "sweep_request.json" if kind == KIND_SWEEP else "run_request.json"
    path = folder / name
    if not path.is_file():
        return ["canonical request"] if source == SOURCE_LIVE else []
    try:
        if kind == KIND_SWEEP:
            payload = serialize_sweep_request(load_sweep_request(path))
        else:
            payload = serialize_run_request(load_run_request(path))
    except Exception:
        return ["canonical request"]
    found: list[str] = []
    if job is not None:
        if str(payload.get("job_id") or "") != str(job.get("job_id") or ""):
            found.append("request job id")
        reported = payload.get("output_dir")
        stored = job.get("output_dir")
        if not reported or not stored or not _same_dir(str(reported), str(stored)):
            found.append("request output directory")
        if str(payload.get("site_label") or "") != str(job.get("site") or ""):
            found.append("request site")
        if str(payload.get("period_id") or "") != str(job.get("period_id") or ""):
            found.append("request period")
        if not payload.get("request_schema_version") or not payload.get("artifact_schema_version"):
            found.append("request schema")
    if source == SOURCE_LIVE and intent is not None:
        mismatches = mismatches_for_serialized_request(payload, intent)
        if mismatches:
            found.append("request does not match the frozen Review settings")
    return found


def validate_result_folder(
    folder: str | Path,
    *,
    kind: str,
    source: str,
    job: Mapping[str, Any] | None = None,
    intent: Mapping[str, Any] | None = None,
    expected_dir: str | Path | None = None,
) -> list[str]:
    path = Path(folder)
    issues: list[str] = []
    if not path.exists() or not path.is_dir():
        return ["result folder"]
    expected = Path(expected_dir) if expected_dir is not None else None
    if expected is not None and not _same_dir(path, expected):
        issues.append("result folder identity")
    elif source == SOURCE_LIVE and job is not None:
        stored = job.get("output_dir")
        if not stored or not _same_dir(path, str(stored)):
            issues.append("result folder identity")
    elif source == SOURCE_DEMO:
        if not _same_dir(path, demo_artifact_dir(kind)):
            issues.append("result folder identity")
    issues.extend(_missing_files(path, required_files(kind, source=source)))
    issues.extend(_status_issues(path, job=job, source=source))
    issues.extend(_request_issues(path, kind=kind, job=job, intent=intent, source=source))
    if kind == KIND_SWEEP:
        summary = _read_mapping(path / "sweep_summary.json")
        if summary is None:
            issues.append("sweep summary")
        else:
            issues.extend(_sweep_summary_issues(summary, intent=intent if source == SOURCE_LIVE else None, source=source))
    else:
        summary = _read_mapping(path / "comparison_summary.json")
        if summary is None:
            issues.append("comparison summary")
        else:
            issues.extend(_scenario_issues(summary))
    return issues


def result_record(
    *,
    kind: str,
    folder: str | Path,
    source: str,
    job_id: str | None = None,
    site: str = "",
    period_id: str = "",
    period_label: str = "",
) -> dict[str, Any]:
    return {
        "version": RESULTS_RECORD_VERSION,
        "kind": kind,
        "result_dir": str(Path(folder)),
        "source": source,
        "demo": source == SOURCE_DEMO,
        "job_id": job_id,
        "validated": True,
        "site": site,
        "period_id": period_id,
        "period_label": period_label,
    }


def results_are_valid(results: Mapping[str, Any] | None) -> bool:
    if not isinstance(results, Mapping):
        return False
    return bool(results.get("validated")) and bool(results.get("result_dir"))


def open_demo_results(state: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    from ui.services.review import snapshot_block_reason, stored_snapshot

    reason = snapshot_block_reason(state)
    if reason is not None:
        return {"ok": False, "error": reason}
    snapshot = stored_snapshot(state) or {}
    kind = KIND_SWEEP if str(snapshot.get("analysis_mode") or "") == MODE_SIZE else KIND_COMPARISON
    folder = demo_artifact_dir(kind, root=root)
    issues = validate_result_folder(
        folder,
        kind=kind,
        source=SOURCE_DEMO,
        expected_dir=folder,
    )
    if issues:
        return {"ok": False, "error": "The saved demonstration results could not be opened."}
    review = state.get("review") if isinstance(state.get("review"), Mapping) else {}
    intent = review.get("intent") if isinstance(review.get("intent"), Mapping) else {}
    selected = selected_period_record(state)
    period_label = ""
    if isinstance(selected, Mapping) and selected.get("label"):
        period_label = str(selected["label"])
    state["results"] = result_record(
        kind=kind,
        folder=folder,
        source=SOURCE_DEMO,
        site=str(intent.get("site_label") or snapshot.get("site_name") or ""),
        period_id=str(intent.get("period_id") or snapshot.get("period_id") or ""),
        period_label=period_label or str(intent.get("period_id") or snapshot.get("period_id") or ""),
    )
    state.pop("job", None)
    state.pop("launch_error", None)
    return {"ok": True, "issues": []}


def cleanup_staging_dir(staging_dir: str | Path | None, *, staging_root: Path) -> bool:
    if not staging_dir:
        return False
    path = Path(staging_dir)
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return False
    if not is_contained(resolved, staging_root):
        return False
    if resolved == staging_root.resolve():
        return False
    import shutil

    shutil.rmtree(resolved, ignore_errors=True)
    return True
