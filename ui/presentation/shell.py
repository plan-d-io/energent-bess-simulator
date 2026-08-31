"""Application shell: identity, presentational stepper, content width."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator, MutableMapping

import streamlit as st

from btm_sim import __version__ as SIMULATOR_VERSION

from ui.flow import navigate_to_step
from ui.presentation.tokens import (
    APP_NAME,
    DEMO_MODE_LABEL,
    FORM_WIDTH_PX,
    LOGO_DISPLAY_PX,
    PAGE_BG,
    STEPS,
    WIDE_WIDTH_PX,
    AnalysisMode,
    StepStatus,
    WidthVariant,
)
from ui.version import UI_VERSION

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_LOGO_PATH = _ASSETS / "Energent.png"


@dataclass(frozen=True)
class StepItem:
    number: int
    name: str
    label: str
    status: StepStatus


def validate_step_window(current: int, max_available: int) -> None:
    if current not in range(1, len(STEPS) + 1):
        raise ValueError(f"current step must be 1–{len(STEPS)}, got {current}")
    if max_available not in range(1, len(STEPS) + 1):
        raise ValueError(
            f"max_available must be 1–{len(STEPS)}, got {max_available}"
        )


def step_label(number: int, mode: AnalysisMode | None = None) -> str:
    del mode
    return STEPS[number - 1]


def step_status(number: int, current: int, max_available: int) -> StepStatus:
    if number > max_available:
        return "unavailable"
    if number == current:
        return "current"
    if number < current:
        return "complete"
    return "unlocked"


def step_items(
    current: int,
    max_available: int,
    mode: AnalysisMode | None = None,
) -> list[StepItem]:
    validate_step_window(current, max_available)
    shown_current = min(current, max_available)
    items: list[StepItem] = []
    for index, name in enumerate(STEPS, start=1):
        items.append(
            StepItem(
                number=index,
                name=name,
                label=step_label(index, mode),
                status=step_status(index, shown_current, max_available),
            )
        )
    return items


def content_width_px(width: WidthVariant) -> int:
    if width == "form":
        return FORM_WIDTH_PX
    if width == "wide":
        return WIDE_WIDTH_PX
    raise ValueError(f"width must be 'form' or 'wide', got {width!r}")


def body_container_key(width: WidthVariant) -> str:
    if width == "form":
        return "v2-body-form"
    if width == "wide":
        return "v2-body-wide"
    raise ValueError(f"width must be 'form' or 'wide', got {width!r}")


def escape_html(text: str) -> str:
    return escape(text, quote=True)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


@st.cache_data(show_spinner=False)
def logo_image_bytes() -> bytes:
    """Composite the contained logo onto the V2 page colour.

    Transparent padding is cropped in this cached render so the visible mark
    stays compact beside the title. The source PNG bytes are not modified.
    Streamlit paints transparent PNG pixels black.
    """
    from PIL import Image

    logo = Image.open(_LOGO_PATH).convert("RGBA")
    bbox = logo.getbbox()
    if bbox:
        logo = logo.crop(bbox)
    pad = 6
    padded = Image.new("RGBA", (logo.width + pad * 2, logo.height + pad * 2), (0, 0, 0, 0))
    padded.paste(logo, (pad, pad), logo)
    background = Image.new("RGBA", padded.size, (*_hex_to_rgb(PAGE_BG), 255))
    composed = Image.alpha_composite(background, padded)
    buffer = BytesIO()
    composed.save(buffer, format="PNG")
    return buffer.getvalue()


def simulator_version_label(*, simulator: str | None = None) -> str:
    sim = SIMULATOR_VERSION if simulator is None else simulator
    return f"Simulator {sim}"


def frontend_version_label(*, frontend: str | None = None) -> str:
    fe = UI_VERSION if frontend is None else frontend
    return f"Front-end {fe}"


def identity_html(*, demo: bool, mode: AnalysisMode | None = None) -> str:
    del mode
    extras: list[str] = []
    if demo:
        extras.append(
            '<span class="v2-demo-status" aria-label="'
            + escape_html(DEMO_MODE_LABEL)
            + '">'
            '<span class="v2-demo-dot" aria-hidden="true"></span>'
            f"{escape_html(DEMO_MODE_LABEL)}"
            "</span>"
        )
    extra = "".join(extras)
    return (
        '<div class="v2-identity">'
        '<div class="v2-identity-stack">'
        f'<span class="v2-identity-name">{escape_html(APP_NAME)}</span>'
        '<span class="v2-identity-versions">'
        f'<span class="v2-identity-version">{escape_html(simulator_version_label())}</span>'
        f'<span class="v2-identity-version">{escape_html(frontend_version_label())}</span>'
        "</span>"
        "</div>"
        f"{extra}"
        "</div>"
    )


def stage_button_label(number: int, label: str) -> str:
    return f"{number}  {label}"


def render_stepper(
    current: int,
    max_available: int,
    mode: AnalysisMode | None = None,
    *,
    lock_navigation: bool = False,
) -> int | None:
    """Render keyboard-operable stage controls. Returns a clicked unlocked step."""
    clicked: int | None = None
    with st.container(
        key="v2-stepper",
        horizontal=True,
        gap="small",
        horizontal_alignment="left",
    ):
        for item in step_items(current, max_available, mode):
            selected = _render_step_control(item, lock_navigation=lock_navigation)
            if selected is not None:
                clicked = selected
    return clicked


def _render_step_control(item: StepItem, *, lock_navigation: bool = False) -> int | None:
    label = stage_button_label(item.number, item.label)
    with st.container(key=f"v2-step-{item.status}-{item.number}"):
        enabled = item.status in {"complete", "unlocked"} and not lock_navigation
        if enabled:
            pressed = st.button(
                label,
                type="secondary",
                width="content",
                help=item.label,
                key=f"v2-nav-step-{item.number}",
            )
            return item.number if pressed else None
        st.button(
            label,
            type="secondary",
            width="content",
            help=item.label,
            disabled=True,
            key=f"v2-nav-step-{item.number}",
        )
        return None


def render_identity(*, demo: bool = False, mode: AnalysisMode | None = None) -> None:
    with st.container(key="v2-identity-row", horizontal=False):
        row = st.container(horizontal=True, vertical_alignment="center", gap="small")
        with row:
            st.image(logo_image_bytes(), width=LOGO_DISPLAY_PX)
            st.html(identity_html(demo=demo, mode=mode))


@contextmanager
def app_shell(
    *,
    current_step: int,
    max_available: int,
    width: WidthVariant,
    mode: AnalysisMode | None = None,
    demo: bool = False,
    state: MutableMapping[str, Any] | None = None,
    lock_navigation: bool = False,
) -> Iterator[None]:
    """Product chrome. Stage clicks navigate through the central flow function."""
    validate_step_window(current_step, max_available)
    with st.container(key="v2-shell"):
        with st.container(key=body_container_key(width)):
            with st.container(key="v2-chrome", horizontal=False, gap=None):
                render_identity(demo=demo, mode=mode)
                clicked = render_stepper(
                    current_step,
                    max_available,
                    mode,
                    lock_navigation=lock_navigation,
                )
                if (
                    state is not None
                    and clicked is not None
                    and not lock_navigation
                    and navigate_to_step(state, clicked)
                ):
                    st.rerun()
            st.markdown('<hr class="v2-rule" />', unsafe_allow_html=True)
            yield
