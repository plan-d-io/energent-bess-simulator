"""Global attribution footer. No session, navigation or core knowledge."""

from __future__ import annotations

import streamlit as st

from ui.presentation.shell import escape_html

FOOTER_KEY = "app-footer"
FOOTER_COPY = "Made by plan-d.io, for Energent cvba"
PLAN_D_LINK_TEXT = "plan-d.io"
PLAN_D_HREF = "https://www.plan-d.io/"
ENERGENT_LINK_TEXT = "Energent cvba"
ENERGENT_HREF = "https://energent.be/"


def _external_link(label: str, href: str) -> str:
    return (
        f'<a href="{escape_html(href)}" target="_blank" rel="noopener noreferrer">'
        f"{escape_html(label)}</a>"
    )


def footer_html() -> str:
    copy = escape_html(FOOTER_COPY)
    copy = copy.replace(
        escape_html(PLAN_D_LINK_TEXT),
        _external_link(PLAN_D_LINK_TEXT, PLAN_D_HREF),
        1,
    )
    copy = copy.replace(
        escape_html(ENERGENT_LINK_TEXT),
        _external_link(ENERGENT_LINK_TEXT, ENERGENT_HREF),
        1,
    )
    return (
        '<div class="app-footer">'
        '<hr class="app-footer-rule" />'
        f'<p class="v2-caption app-footer-copy">{copy}</p>'
        "</div>"
    )


def render_footer() -> None:
    with st.container(key=FOOTER_KEY):
        st.html(footer_html())
