from __future__ import annotations

from streamlit.testing.v1 import AppTest

from ui.presentation.tokens import MODE_ONE_BATTERY_LABEL, MODE_SIZE_LABEL


def _foundation_preview() -> None:
    from ui.presentation.styles import inject_styles
    from ui.views.foundation_preview import render_foundation_preview

    inject_styles()
    render_foundation_preview()


def _button(at: AppTest, label: str):
    matches = [item for item in at.button if item.label == label]
    assert matches, f"missing button {label!r}"
    return matches[0]


def test_foundation_preview_still_renders() -> None:
    at = AppTest.from_function(_foundation_preview, default_timeout=12)
    at.run()
    assert not at.exception
    headers = [item.value for item in at.header]
    markdown = " ".join(str(item.value) for item in at.markdown)
    captions = " ".join(str(item.value) for item in at.caption)
    combined = " ".join(headers + [markdown, captions])
    assert "V2 foundation preview" in combined
    assert "Configure options" in headers


def test_preview_compositions_run_without_exception() -> None:
    at = AppTest.from_function(_foundation_preview, default_timeout=12)
    at.run()
    assert not at.exception
    for option in ("Wide shell", "Component states", "Demo treatment"):
        at.radio[0].set_value(option)
        at.run()
        assert not at.exception, option
    assert len(at.info) >= 1


def test_wide_shell_keeps_full_period_metric() -> None:
    at = AppTest.from_function(_foundation_preview, default_timeout=12)
    at.run()
    at.radio[0].set_value("Wide shell")
    at.run()
    assert not at.exception
    values = [item.value for item in at.metric]
    assert "Calendar year 2024" in values
    assert "Ganda Cars" in values


def test_live_choice_cards_return_clicked_option() -> None:
    at = AppTest.from_function(_foundation_preview, default_timeout=12)
    at.run()
    _button(at, MODE_SIZE_LABEL).click()
    at.run()
    assert not at.exception
    at.run()
    assert not at.exception
    chosen = _button(at, MODE_SIZE_LABEL)
    idle = _button(at, MODE_ONE_BATTERY_LABEL)
    assert chosen.proto.type == "primary"
    assert idle.proto.type == "secondary"


def test_demo_choice_cards_are_disabled() -> None:
    at = AppTest.from_function(_foundation_preview, default_timeout=12)
    at.run()
    at.radio[0].set_value("Demo treatment")
    at.run()
    assert not at.exception
    live = _button(at, MODE_SIZE_LABEL)
    other = _button(at, MODE_ONE_BATTERY_LABEL)
    assert live.proto.disabled
    assert other.proto.disabled
    other.click()
    at.run()
    assert _button(at, MODE_SIZE_LABEL).proto.type == "primary"
    assert _button(at, MODE_ONE_BATTERY_LABEL).proto.type == "secondary"
