"""Step 1 Upload data. Live upload and Demo mode."""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

import streamlit as st

from ui.flow import (
    DEMO_CHECKBOX_KEY,
    ROUTE_LIVE,
    ROUTE_SAVED,
    SAVED_SITE_NAME,
    SITE_WIDGET_KEY,
    analysis_mode_or_none,
    apply_route_change,
    apply_site_name,
    apply_widget_upload_change,
    clear_route_change_widget_keys,
    continue_to_step2,
    displayed_site_name,
    is_saved_example,
    store_inspection,
    transferred_uploads_hold,
    upload_widget_key,
)
from ui.services.day_ahead import STANDARD_BASENAME, day_ahead_filename
from ui.services.saved_example import load_saved_example, load_saved_snapshot
from ui.services.uploads import (
    blocking_panels,
    file_signature,
    inspect_fluvius_payloads,
    live_role_rows,
    snapshot_is_ready,
)
from ui.presentation.components import render_action_row, render_display_table, render_page_header, render_status_panel
from ui.presentation.shell import app_shell

LiveKind = Literal["empty", "wrong_count", "checking", "invalid", "ready"]

_LEAD = "Upload the Fluvius offtake, injection and PV production CSV files"
_CHECKING_TITLE = "Checking Fluvius files"
_CHECKING_BODY = (
    "Detecting offtake, injection and PV production from active-energy registers."
)
_READY_TITLE = "Three Fluvius files were identified"
_READY_BODY = "Roles come from Afname Actief, Injectie Actief and Productie Actief."
_LIVE_CAPTION = "After Review, Run freezes these inputs and starts a live worker."
_DEMO_REMINDER = "Saved 2024 validation and results. No simulation runs."
_PRICE_UNAVAILABLE = "The standard day-ahead price file could not be resolved."

REASON_SITE = "Enter a site or project name."
REASON_UPLOAD = "Upload the three Fluvius CSV files."
REASON_EXACT = "Upload exactly three Fluvius CSV files."
REASON_WAIT = "Wait until the files have been checked."
REASON_RESOLVE = "Resolve the file errors above."
REASON_DEMO = "Restore the demo files."


def live_view_kind(
    *,
    file_count: int,
    inspecting: bool,
    snapshot: Mapping[str, Any] | None,
) -> LiveKind:
    if file_count == 0:
        return "empty"
    if file_count != 3:
        return "wrong_count"
    if snapshot is None:
        return "checking"
    if snapshot.get("error") or not snapshot_is_ready(snapshot):
        return "invalid"
    return "ready"


def step1_disabled_reason(
    *,
    demo: bool,
    site_name: str,
    file_count: int,
    kind: str,
    demo_ok: bool,
) -> str | None:
    if demo:
        return None if demo_ok else REASON_DEMO
    if not str(site_name or "").strip():
        return REASON_SITE
    if file_count == 0:
        return REASON_UPLOAD
    if file_count != 3:
        return REASON_EXACT
    if kind == "checking":
        return REASON_WAIT
    if kind == "invalid":
        return REASON_RESOLVE
    return None


def render_provide_data(state: dict[str, Any]) -> None:
    demo = is_saved_example(state)
    with app_shell(
        current_step=1,
        max_available=int(state.get("max_step") or 1),
        width="form",
        demo=demo,
        mode=analysis_mode_or_none(state),
        state=state,
    ):
        render_page_header("Step 1 of 6", "Upload data", _LEAD)
        _render_site_field(state, demo=demo)
        files = st.file_uploader(
            "Fluvius CSV exports",
            type=["csv"],
            accept_multiple_files=True,
            disabled=demo,
            key=upload_widget_key(int(state.get("upload_generation") or 0)),
        )
        if DEMO_CHECKBOX_KEY not in st.session_state:
            st.session_state[DEMO_CHECKBOX_KEY] = demo
        st.checkbox("Demo mode", key=DEMO_CHECKBOX_KEY)
        wanted = ROUTE_SAVED if bool(st.session_state.get(DEMO_CHECKBOX_KEY)) else ROUTE_LIVE
        if wanted != state.get("data_route"):
            apply_route_change(state, wanted)
            clear_route_change_widget_keys(st.session_state)
            st.rerun()
        with st.container(key="v2-upload-followup"):
            if demo:
                example = _render_demo_body(state)
                reason = step1_disabled_reason(
                    demo=True,
                    site_name=SAVED_SITE_NAME,
                    file_count=3,
                    kind="ready" if example.ok else "invalid",
                    demo_ok=example.ok,
                )
                can_continue = example.ok
                inspected = False
            else:
                kind, inspected, file_count = _render_live_body(state, files)
                reason = step1_disabled_reason(
                    demo=False,
                    site_name=str(state.get("site_name") or ""),
                    file_count=file_count,
                    kind=kind,
                    demo_ok=False,
                )
                can_continue = reason is None
        _render_price_expander()
        events = render_action_row(
            primary="Continue",
            primary_disabled=not can_continue,
            caption=_LIVE_CAPTION if can_continue and not demo else None,
            disabled_reason=reason,
            key="v2-provide-actions",
        )
        if events.primary and can_continue:
            continue_to_step2(state)
            st.rerun()
        if inspected:
            st.rerun()


def _render_site_field(state: dict[str, Any], *, demo: bool) -> None:
    if demo:
        st.text_input(
            "Site or project name",
            value=displayed_site_name(state) or SAVED_SITE_NAME,
            disabled=True,
        )
        return
    if SITE_WIDGET_KEY not in st.session_state:
        st.session_state[SITE_WIDGET_KEY] = str(state.get("site_name") or "")
    st.text_input("Site or project name", key=SITE_WIDGET_KEY)
    apply_site_name(state, str(st.session_state.get(SITE_WIDGET_KEY) or ""))


def _render_live_body(
    state: dict[str, Any], files: Sequence[Any] | None
) -> tuple[LiveKind, bool, int]:
    widget_payloads = _payloads_from_files(files)
    signature = file_signature(widget_payloads)
    if tuple(state.get("upload_signature") or ()) != signature:
        apply_widget_upload_change(state, signature=signature, payloads=widget_payloads)
    if transferred_uploads_hold(state, len(widget_payloads)):
        payloads = tuple(state.get("upload_payloads") or ())
    else:
        payloads = widget_payloads

    kind = live_view_kind(
        file_count=len(payloads),
        inspecting=bool(state.get("inspecting")),
        snapshot=state.get("ingest_snapshot"),
    )
    inspected = False
    if kind == "checking":
        with st.spinner("Checking Fluvius files"):
            render_status_panel("info", _CHECKING_TITLE, _CHECKING_BODY)
            if state.get("ingest_snapshot") is None:
                snapshot = inspect_fluvius_payloads(payloads)
                store_inspection(state, snapshot, ready=snapshot_is_ready(snapshot))
                inspected = True
    elif kind == "wrong_count":
        render_status_panel(
            "danger",
            "Upload exactly three Fluvius CSV exports",
            f"You selected {len(payloads)}.",
        )
    elif kind == "invalid":
        snapshot = state.get("ingest_snapshot") or {}
        for title, body in blocking_panels(snapshot):
            render_status_panel("danger", title, body)
    elif kind == "ready":
        snapshot = state.get("ingest_snapshot") or {}
        render_status_panel("success", _READY_TITLE, _READY_BODY)
        render_display_table(live_role_rows(snapshot))
    return kind, inspected, len(payloads)


def _payloads_from_files(files: Sequence[Any] | None) -> tuple[tuple[str, bytes], ...]:
    if not files:
        return ()
    return tuple((str(item.name), bytes(item.getvalue())) for item in files)


def _render_demo_body(state: dict[str, Any]):
    example = load_saved_example()
    if example.ok:
        st.caption(_DEMO_REMINDER)
        render_display_table(list(example.rows))
        store_inspection(state, load_saved_snapshot(), ready=True)
    else:
        render_status_panel(
            "danger",
            "The saved demo is not available",
            example.error or "The required source metadata is missing.",
        )
        store_inspection(
            state,
            {
                "ok": False,
                "roles": {},
                "sources": [],
                "issues": [],
                "periods": [],
                "dst": {},
                "error": {"code": "SAVED_EXAMPLE_UNAVAILABLE", "message": example.error},
            },
            ready=False,
        )
    return example


def _render_price_expander() -> None:
    resolved_name = day_ahead_filename()
    filename = resolved_name or STANDARD_BASENAME
    with st.expander("Day-ahead injection prices"):
        if resolved_name:
            st.write(f"Standard project dataset · {filename}")
        else:
            st.write(_PRICE_UNAVAILABLE)
