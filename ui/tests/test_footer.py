from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui.presentation.footer import (
    ENERGENT_HREF,
    FOOTER_COPY,
    FOOTER_KEY,
    PLAN_D_HREF,
    footer_html,
)
from ui.presentation.styles import stylesheet
from ui.presentation.tokens import (
    BORDER,
    NARROW_BREAKPOINT_PX,
    PRIMARY_FOCUS,
    SPACE_MD,
    SPACE_XXL,
    TEXT_MUTED,
    WIDE_WIDTH_PX,
)
from ui.tests.test_app import APP


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _visible_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _html_bodies(at: AppTest) -> list[str]:
    return [str(item.proto.body) for item in at.get("html")]


def _app_html(at: AppTest) -> str:
    return " ".join(_html_bodies(at))


def test_footer_copy_is_the_exact_visible_sentence() -> None:
    markup = footer_html()
    assert _visible_text(markup) == FOOTER_COPY
    assert FOOTER_COPY == "Made by plan-d.io, for Energent cvba"
    assert "Joannes Laveyne" not in markup


def test_footer_links_open_exact_https_targets() -> None:
    markup = footer_html()
    assert f'href="{PLAN_D_HREF}"' in markup
    assert f'href="{ENERGENT_HREF}"' in markup
    assert PLAN_D_HREF == "https://www.plan-d.io/"
    assert ENERGENT_HREF == "https://energent.be/"
    assert markup.count('target="_blank"') == 2
    assert markup.count('rel="noopener noreferrer"') == 2
    assert 'href="mailto:' not in markup
    assert "©" not in markup


def test_footer_does_not_render_the_plan_d_logo() -> None:
    markup = footer_html()
    assert "<img" not in markup
    assert "data:image/png" not in markup
    css = stylesheet()
    assert ".app-footer-logo" not in css


def test_app_calls_render_footer_once_after_routed_content() -> None:
    source = Path("ui/app.py").read_text(encoding="utf-8")
    assert source.index("inject_styles()") < source.index("render_app()")
    assert source.index("render_app()") < source.index("render_footer()")
    assert source.count("render_footer()") == 1
    views = Path("ui/views")
    offenders = [
        str(path.relative_to(views))
        for path in views.rglob("*.py")
        if "render_footer" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_app_renders_one_footer_on_representative_reruns() -> None:
    at = AppTest.from_file(str(APP), default_timeout=12)
    at.run()
    assert not at.exception
    first = _app_html(at)
    assert first.count('class="app-footer"') == 1
    footer_markup = re.search(r'<div class="app-footer">.*?</div>', first, flags=re.DOTALL)
    assert footer_markup is not None
    assert _visible_text(footer_markup.group(0)) == FOOTER_COPY
    at.run()
    assert not at.exception
    second = _app_html(at)
    assert second.count('class="app-footer"') == 1
    assert second.count(PLAN_D_HREF) == 1
    assert second.count(ENERGENT_HREF) == 1


def test_footer_css_reuses_frame_border_caption_and_spacing_tokens() -> None:
    css = stylesheet()
    footer = css[css.index(".st-key-app-footer") :]
    assert FOOTER_KEY in css
    assert f"max-width: {WIDE_WIDTH_PX}px" in footer
    assert f"border-top: 1px solid {BORDER}" in footer
    assert f"color: {TEXT_MUTED}" in footer
    assert "font-size: 0.85rem" in footer
    assert f"margin-top: {SPACE_XXL}px" in footer
    assert f"padding-top: {SPACE_MD}px" in footer
    assert f"padding-bottom: {SPACE_MD}px" in footer
    assert "text-align: right" in footer
    assert "text-align: left" not in footer.split("@media")[0]
    assert f"outline: 2px solid {PRIMARY_FOCUS}" in footer
    assert "position: fixed" not in footer
    assert "position: sticky" not in footer
    assert "box-shadow" not in footer.split("@media")[0]


def test_footer_narrow_css_does_not_force_horizontal_overflow() -> None:
    css = stylesheet()
    media = css[css.index(f"@media (max-width: {NARROW_BREAKPOINT_PX}px)") :]
    footer_media = media[media.index(".st-key-app-footer") :]
    assert "max-width: 100%" in footer_media
    assert "overflow-x: scroll" not in footer_media
    assert "min-width:" not in footer_media
    assert "position: fixed" not in footer_media
