"""Top-level V2 view routing. Foundation preview is not the normal entry."""

from __future__ import annotations

from typing import Any, MutableMapping

import streamlit as st

from ui.flow import (
    CONFIGURE_WIDGET_PREFIX,
    DEMO_CHECKBOX_KEY,
    PERIOD_SELECT_KEY,
    REVIEW_WIDGET_PREFIX,
    SESSION_KEY,
    SITE_BOUNDARY_ACK_WIDGET_KEY,
    SITE_WIDGET_KEY,
    UNVALIDATED_ACK_WIDGET_KEY,
    UPLOAD_WIDGET_PREFIX,
    default_state,
    state_is_compatible,
)
from ui.views.check_files import render_check_files
from ui.views.choose_period import render_choose_period
from ui.views.configure import render_configure
from ui.views.execution import render_execution
from ui.views.provide_data import render_provide_data
from ui.views.review import render_review


def wipe_v2_widgets(session: MutableMapping[str, Any]) -> None:
    session.pop(SITE_WIDGET_KEY, None)
    session.pop(DEMO_CHECKBOX_KEY, None)
    session.pop(PERIOD_SELECT_KEY, None)
    session.pop(UNVALIDATED_ACK_WIDGET_KEY, None)
    session.pop(SITE_BOUNDARY_ACK_WIDGET_KEY, None)
    for key in list(session.keys()):
        if (
            str(key).startswith(UPLOAD_WIDGET_PREFIX)
            or str(key).startswith(CONFIGURE_WIDGET_PREFIX)
            or str(key).startswith(REVIEW_WIDGET_PREFIX)
        ):
            session.pop(key, None)


def bind_state() -> dict[str, Any]:
    current = st.session_state.get(SESSION_KEY)
    if not state_is_compatible(current):
        wipe_v2_widgets(st.session_state)
        st.session_state[SESSION_KEY] = default_state()
    return st.session_state[SESSION_KEY]


def render_app() -> None:
    state = bind_state()
    step = int(state.get("step") or 1)
    max_step = int(state.get("max_step") or 1)
    if step > 6:
        state["step"] = min(max_step, 6)
        step = int(state["step"])
    if step == 6:
        render_execution(state)
        return
    if step == 5:
        render_review(state)
        return
    if step == 4:
        render_configure(state)
        return
    if step == 3:
        render_choose_period(state)
        return
    if step == 2:
        render_check_files(state)
        return
    if step != 1:
        state["step"] = 1
        state["max_step"] = min(int(state.get("max_step") or 1), 1)
    render_provide_data(state)
