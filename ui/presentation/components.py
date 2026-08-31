"""Reusable V2 presentation components. No session, core or artifact knowledge."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, NamedTuple

import streamlit as st

from ui.presentation.shell import escape_html
from ui.presentation.tokens import StatusTone


def render_page_header(kicker: str, title: str, lead: str | None = None) -> None:
    with st.container(key="v2-page-header", horizontal=False):
        st.html(f'<div class="v2-kicker">{escape_html(kicker)}</div>')
        st.header(title)
        if lead:
            st.markdown(
                f'<p class="v2-page-lead">{escape_html(lead)}</p>',
                unsafe_allow_html=True,
            )


def render_section_heading(title: str, lead: str | None = None) -> None:
    st.subheader(title)
    if lead:
        st.markdown(
            f'<p class="v2-section-lead">{escape_html(lead)}</p>',
            unsafe_allow_html=True,
        )


@contextmanager
def render_status_detail_group(key: str) -> Iterator[None]:
    """Opt-in spacing for a status panel and its related expander or detail."""
    with st.container(key=f"v2-status-group-{key}"):
        yield


def resolve_choice_selection(
    *,
    selected: str,
    clicked: str | None,
    disabled: bool,
    option_ids: Sequence[str],
) -> str:
    """Return the next selected option id. Caller owns persistent state."""
    if disabled or clicked is None or clicked not in option_ids:
        return selected
    return clicked


def render_choice_cards(
    options: Sequence[tuple[str, str, str]],
    *,
    selected: str,
    key: str,
    disabled: bool = False,
) -> str:
    """Keyboard-operable two-option control. Returns the selected option id."""
    option_ids = [option_id for option_id, _, _ in options]
    clicked: str | None = None
    cols = st.columns(2, gap="medium")
    for column, (option_id, title, body) in zip(cols, options, strict=True):
        kind = "v2-choice-selected" if option_id == selected else "v2-choice-idle"
        with column.container(key=f"{key}-{kind}-{option_id}", border=True):
            pressed = st.button(
                title,
                key=f"{key}-btn-{option_id}",
                type="primary" if option_id == selected else "secondary",
                disabled=disabled,
                width="stretch",
            )
            lines = str(body).split("\n")
            html_body = "<br>".join(
                escape_html(line) if line else "&nbsp;" for line in lines
            )
            st.markdown(
                f'<p class="v2-choice-body">{html_body}</p>',
                unsafe_allow_html=True,
            )
        if pressed:
            clicked = option_id
    return resolve_choice_selection(
        selected=selected,
        clicked=clicked,
        disabled=disabled,
        option_ids=option_ids,
    )


def render_status_panel(tone: StatusTone, title: str, body: str) -> None:
    if tone == "success":
        st.success(f"**{title}**\n\n{body}")
    elif tone == "warning":
        st.warning(f"**{title}**\n\n{body}")
    elif tone == "danger":
        st.error(f"**{title}**\n\n{body}")
    else:
        st.info(f"**{title}**\n\n{body}")


def render_acknowledgement_panel(
    *,
    title: str,
    facts: Sequence[str],
    checkbox_label: str,
    checked: bool = False,
    detail: str | None = None,
    disabled: bool = False,
    key: str | None = None,
) -> bool:
    if key:
        if key not in st.session_state:
            st.session_state[key] = bool(checked)
        elif disabled:
            st.session_state[key] = bool(checked)
    st.warning(f"**{title}**")
    for fact in facts:
        st.write(fact)
    if detail:
        with st.expander("Detail"):
            for part in str(detail).split("\n\n"):
                text = part.strip()
                if text:
                    st.write(text)
    if key:
        st.checkbox(checkbox_label, disabled=disabled, key=key)
        return bool(st.session_state.get(key))
    st.checkbox(checkbox_label, value=checked, disabled=disabled)
    return bool(checked)


def render_metric_group(items: Sequence[tuple[str, str]], *, key: str = "v2-metrics") -> None:
    with st.container(key=key):
        cols = st.columns(len(items) or 1)
        for column, (label, value) in zip(cols, items, strict=True):
            column.metric(label, value)


def render_display_table(
    data: Mapping[str, Sequence[Any]] | Sequence[Any] | Any,
    *,
    hide_index: bool = True,
) -> None:
    """Small lookup table: size to rows, wrap long values, no inner scrollbars."""
    st.table(data, width="stretch", height="content", hide_index=hide_index)


def render_text_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str],
) -> None:
    """HTML table whose last column uses remaining width and wraps on words."""
    header = "".join(f"<th>{escape_html(str(column))}</th>" for column in columns)
    body_rows: list[str] = []
    for row in rows:
        cells = "".join(
            f"<td>{escape_html(str(row.get(column, '')))}</td>" for column in columns
        )
        body_rows.append(f"<tr>{cells}</tr>")
    st.markdown(
        "<table class=\"v2-text-table\">"
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>",
        unsafe_allow_html=True,
    )


def render_table_frame(
    *,
    title: str,
    caption: str,
    data: Mapping[str, Sequence[Any]] | Any,
    hide_index: bool = True,
) -> None:
    with st.container(key="v2-table-frame"):
        st.markdown(f"**{title}**")
        render_display_table(data, hide_index=hide_index)
        st.caption(caption)


def render_chart_frame(
    *,
    title: str,
    x_label: str,
    y_label: str,
    caption: str,
    data: Any,
) -> None:
    with st.container(key="v2-chart-frame"):
        st.markdown(f"**{title}**")
        st.html(
            f'<p class="v2-axis-label">{escape_html(y_label)} versus {escape_html(x_label)}</p>'
        )
        st.line_chart(data)
        st.caption(caption)


def render_expander(title: str, body: str, *, expanded: bool = False) -> None:
    with st.expander(title, expanded=expanded):
        st.write(body)


class ActionRowEvent(NamedTuple):
    back: bool
    primary: bool


def action_row_alignment(*, has_back: bool) -> str:
    """Keep Continue on the right whether or not Back is present."""
    return "distribute" if has_back else "right"


def continue_reason_text(reason: str | None, *, primary_disabled: bool) -> str | None:
    """One muted line, only when Continue is disabled."""
    if not primary_disabled or not reason:
        return None
    return f"To continue: {reason}"


def render_action_row(
    *,
    primary: str,
    back: str | None = None,
    primary_disabled: bool = False,
    caption: str | None = None,
    disabled_reason: str | None = None,
    key: str = "v2-actions",
) -> ActionRowEvent:
    back_clicked = False
    primary_clicked = False
    shown_reason = continue_reason_text(disabled_reason, primary_disabled=primary_disabled)
    with st.container(key="v2-action-row"):
        if shown_reason:
            st.markdown(
                f'<p class="v2-continue-reason" role="status">{escape_html(shown_reason)}</p>',
                unsafe_allow_html=True,
            )
        row = st.container(
            horizontal=True,
            horizontal_alignment=action_row_alignment(has_back=bool(back)),
        )
        with row:
            if back:
                back_clicked = bool(
                    st.button(
                        back,
                        type="secondary",
                        width="content",
                        key=f"{key}-back",
                    )
                )
            primary_clicked = bool(
                st.button(
                    primary,
                    type="primary",
                    disabled=primary_disabled,
                    width="content",
                    key=f"{key}-primary",
                )
            )
        if caption:
            st.caption(caption)
    return ActionRowEvent(back=back_clicked, primary=primary_clicked)


def render_empty_state(message: str) -> None:
    st.html(f'<div class="v2-empty">{escape_html(message)}</div>')


def render_loading_state(message: str) -> None:
    render_status_panel("info", "Checking files", message)


def render_blocked_state(title: str, body: str) -> None:
    render_status_panel("danger", title, body)
