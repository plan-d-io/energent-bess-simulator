"""Step 6 execution, recovery and Results-ready boundary."""

from __future__ import annotations

import os
from typing import Any, Mapping

import streamlit as st

from ui.flow import analysis_mode_or_none, is_saved_example
from ui.services.job import job_locks_navigation, reconcile_execution, return_to_review
from ui.services.compare_format import DISPLAY_ERROR_BODY, DISPLAY_ERROR_TITLE
from ui.services.paths import KIND_COMPARISON, KIND_SWEEP
from ui.services.results import results_are_valid
from ui.services.status import (
    CLASS_FAILED,
    CLASS_INCOMPLETE,
    CLASS_QUEUED,
    CLASS_READY,
    CLASS_RUNNING,
    CLASS_UNEXPECTED,
    diagnostic_file_paths,
    format_elapsed,
    live_elapsed_seconds,
    read_status,
    safe_error_message,
    stage_pair,
    tail_text,
    trusted_status,
)
from ui.presentation.components import render_action_row, render_metric_group, render_page_header, render_status_panel
from ui.presentation.shell import app_shell
from ui.presentation.tokens import MODE_ONE_BATTERY_LABEL, MODE_SIZE_LABEL, STEPS

_PAGE_TITLE = STEPS[5]
_PARTIAL_NOTE = "Partial results are not opened."
_STAGE_CAPTION = "The bar follows completed stages. It is not a solver percentage."
_REFRESH_CAPTION = "Refreshing the browser reconnects to this run."
_READY_NOTE = "Detailed result views will be added in the next phase."
_LOG_TAIL = 80


def _is_sweep(state: Mapping[str, Any]) -> bool:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else {}
    results = state.get("results") if isinstance(state.get("results"), Mapping) else {}
    kind = str(results.get("kind") or job.get("kind") or "")
    if kind == KIND_SWEEP:
        return True
    return analysis_mode_or_none(state) == "size"


def _running_title(sweep: bool) -> str:
    return "Battery-size comparison running" if sweep else "Simulation running"


def _failed_title(sweep: bool) -> str:
    return "Battery-size comparison failed" if sweep else "Simulation failed"


def _state_label(klass: str) -> str:
    labels = {
        CLASS_QUEUED: "Queued",
        CLASS_RUNNING: "Running",
        CLASS_READY: "Completed",
        CLASS_FAILED: "Failed",
        CLASS_UNEXPECTED: "Stopped",
        CLASS_INCOMPLETE: "Incomplete",
    }
    return labels.get(klass, klass)


def _identity(state: Mapping[str, Any]) -> tuple[str, str, str]:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else {}
    results = state.get("results") if isinstance(state.get("results"), Mapping) else {}
    site = str(results.get("site") or job.get("site") or "")
    period = str(results.get("period_label") or job.get("period_label") or job.get("period_id") or "")
    analysis = MODE_SIZE_LABEL if _is_sweep(state) else MODE_ONE_BATTERY_LABEL
    return site, period, analysis


def _render_diagnostics(
    state: Mapping[str, Any],
    *,
    log_label: str,
    status: Mapping[str, Any] | None,
) -> None:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else {}
    files = diagnostic_file_paths(job)
    log_text = tail_text(files.get("log")) or tail_text(files.get("console"))
    with st.expander(log_label, expanded=False):
        if log_text:
            st.code(log_text, language="text")
        else:
            st.caption("No log lines yet.")
        category = None if status is None else status.get("error_category")
        if category:
            st.caption(f"Error category: {category}")
        issues = job.get("validation_issues") if isinstance(job.get("validation_issues"), list) else []
        if issues:
            st.caption("Validation issues")
            for item in issues:
                st.write(str(item))


def _render_running(state: Mapping[str, Any], klass: str, *, sweep: bool) -> None:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else {}
    raw = read_status(job.get("output_dir"))
    status = trusted_status(job, raw)
    site, period, _analysis = _identity(state)
    elapsed = format_elapsed(
        live_elapsed_seconds(status, launched_at_utc=str(job.get("launch_utc") or ""), now=None)
    )
    if klass == CLASS_QUEUED:
        body = "Waiting for the worker to start."
    else:
        body = "The worker is in progress."
    render_page_header("Step 6 of 6", _PAGE_TITLE)
    render_status_panel("warning", _running_title(sweep), body)
    render_metric_group(
        [
            ("Site", site or "—"),
            ("Period", period or "—"),
            ("State", _state_label(klass)),
            ("Elapsed", elapsed),
        ]
    )
    message = str((status or {}).get("message") or "").strip()
    if message:
        st.write(message)
    pair = stage_pair(status)
    if pair is not None:
        number, total = pair
        st.write(f"Stage {number} of {total}")
        st.progress(0.0 if total == 0 else number / total)
        st.caption(_STAGE_CAPTION)
    st.caption(_REFRESH_CAPTION)
    _render_diagnostics(state, log_label="Run log", status=status)


def _render_recovery(state: Mapping[str, Any], klass: str, *, sweep: bool) -> None:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else {}
    raw = read_status(job.get("output_dir"))
    status = trusted_status(job, raw)
    if klass == CLASS_UNEXPECTED:
        title = "The worker ended unexpectedly"
        body = "The worker stopped before a completed result was written."
    elif klass == CLASS_INCOMPLETE:
        title = "Results could not be opened"
        body = "The run finished without a complete, compatible result folder."
    else:
        title = _failed_title(sweep)
        body = safe_error_message((status or {}).get("error_message"))
    render_page_header("Step 6 of 6", _PAGE_TITLE)
    render_status_panel("danger", title, body)
    st.write(_PARTIAL_NOTE)
    _render_diagnostics(state, log_label="Diagnostics", status=status)
    events = render_action_row(
        back=None,
        primary="Return to Review",
        key="v2-execution-recover",
    )
    if events.primary:
        return_to_review(state)
        st.rerun()


def _render_display_failure(state: Mapping[str, Any]) -> None:
    render_page_header("Step 6 of 6", _PAGE_TITLE)
    render_status_panel("danger", DISPLAY_ERROR_TITLE, DISPLAY_ERROR_BODY)
    _render_diagnostics(state, log_label="Diagnostics", status=None)
    events = render_action_row(
        back=None,
        primary="Return to Review",
        key="v2-execution-display",
    )
    if events.primary:
        return_to_review(state)
        st.rerun()


def _render_sweep_ready(state: Mapping[str, Any]) -> None:
    site, period, analysis = _identity(state)
    render_page_header("Step 6 of 6", _PAGE_TITLE)
    render_status_panel("success", "Results ready", "The stored result is complete.")
    render_metric_group(
        [
            ("Site", site or "—"),
            ("Period", period or "—"),
            ("Analysis", analysis),
        ]
    )
    st.caption(_READY_NOTE)


def _render_ready(state: Mapping[str, Any]) -> None:
    results = state.get("results") if isinstance(state.get("results"), Mapping) else {}
    kind = str(results.get("kind") or "")
    if kind == KIND_COMPARISON:
        from ui.views.compare_results import render_comparison_results

        if render_comparison_results(state):
            return
        _render_display_failure(state)
        return
    if kind == KIND_SWEEP:
        from ui.views.sweep_results import render_sweep_results

        if render_sweep_results(state):
            return
        _render_display_failure(state)
        return
    _render_sweep_ready(state)


def _render_body(state: dict[str, Any]) -> None:
    klass = reconcile_execution(state)
    sweep = _is_sweep(state)
    if klass == CLASS_READY or results_are_valid(state.get("results") if isinstance(state.get("results"), dict) else None):
        _render_ready(state)
        return
    if klass in {CLASS_QUEUED, CLASS_RUNNING}:
        _render_running(state, klass, sweep=sweep)
        return
    _render_recovery(state, klass, sweep=sweep)


def render_execution(state: dict[str, Any]) -> None:
    demo = is_saved_example(state)
    lock = job_locks_navigation(state)
    with app_shell(
        current_step=6,
        max_available=max(int(state.get("max_step") or 1), 6),
        width="wide",
        demo=demo,
        mode=analysis_mode_or_none(state),
        state=state,
        lock_navigation=lock,
    ):
        if lock and not results_are_valid(state.get("results") if isinstance(state.get("results"), dict) else None):

            interval = None if os.environ.get("PYTEST_CURRENT_TEST") else 1

            @st.fragment(run_every=interval)
            def _poll() -> None:
                locked = job_locks_navigation(state)
                _render_body(state)
                if locked and not job_locks_navigation(state):
                    st.rerun()

            _poll()
            return
        _render_body(state)
