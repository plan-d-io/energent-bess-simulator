from __future__ import annotations

import pytest

from ui.presentation.shell import (
    body_container_key,
    content_width_px,
    frontend_version_label,
    identity_html,
    simulator_version_label,
    stage_button_label,
    step_items,
)
from ui.presentation.tokens import (
    DEMO_MODE_LABEL,
    FORM_WIDTH_PX,
    LOGO_DISPLAY_PX,
    STEPS,
    WIDE_WIDTH_PX,
)


def test_six_step_labels() -> None:
    assert STEPS == (
        "Upload data",
        "Data verification",
        "Simulation period",
        "Configure options",
        "Review and run",
        "Results",
    )


def test_step_labels_do_not_duplicate_numbers() -> None:
    items = step_items(current=3, max_available=4, mode="one-battery")
    assert items[0].label == "Upload data"
    assert items[1].label == "Data verification"
    for item in items:
        assert not item.label.startswith(f"{item.number}.")
        assert not item.label.startswith(f"{item.number} ")


def test_step_states_complete_current_unlocked_unavailable() -> None:
    items = step_items(current=3, max_available=4, mode="one-battery")
    assert [item.status for item in items] == [
        "complete",
        "complete",
        "current",
        "unlocked",
        "unavailable",
        "unavailable",
    ]
    assert items[0].name == "Upload data"
    assert items[3].label == "Configure options"
    assert items[2].number == 3
    assert items[1].name == "Data verification"


def test_mode_does_not_rename_configure_or_results() -> None:
    one = step_items(current=4, max_available=4, mode="one-battery")
    size = step_items(current=6, max_available=6, mode="size")
    assert one[3].label == "Configure options"
    assert size[3].label == "Configure options"
    assert size[5].label == "Results"
    assert one[5].label == "Results"


def test_rejects_out_of_range_steps() -> None:
    with pytest.raises(ValueError):
        step_items(0, 1)
    with pytest.raises(ValueError):
        step_items(1, 7)
    with pytest.raises(ValueError):
        step_items(7, 6)


def test_current_beyond_max_is_clamped() -> None:
    items = step_items(current=6, max_available=2)
    assert [item.status for item in items] == [
        "complete",
        "current",
        "unavailable",
        "unavailable",
        "unavailable",
        "unavailable",
    ]


def test_width_variants_and_separate_chrome_body_keys() -> None:
    assert content_width_px("form") == FORM_WIDTH_PX == 1120
    assert content_width_px("wide") == WIDE_WIDTH_PX == 1120
    assert LOGO_DISPLAY_PX == 72
    assert body_container_key("form") == "v2-body-form"
    assert body_container_key("wide") == "v2-body-wide"
    with pytest.raises(ValueError):
        content_width_px("full")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        body_container_key("full")  # type: ignore[arg-type]


def test_stage_button_labels_keep_full_name() -> None:
    assert stage_button_label(1, "Upload data") == "1  Upload data"
    assert stage_button_label(2, "Data verification") == "2  Data verification"


def test_identity_shows_labelled_simulator_and_frontend_versions() -> None:
    from btm_sim import __version__ as simulator_version

    from ui.version import UI_VERSION

    markup = identity_html(demo=False, mode=None)
    sim = f"Simulator {simulator_version}"
    front = f"Front-end {UI_VERSION}"
    assert sim == f"Simulator {simulator_version}"
    assert front == f"Front-end {UI_VERSION}"
    assert sim == simulator_version_label()
    assert front == frontend_version_label()
    assert sim in markup
    assert front in markup
    assert " · " not in markup
    assert markup.index(sim) < markup.index(front)
    assert markup.count('class="v2-identity-version"') == 2
    assert 'class="v2-identity-name"' in markup
    assert 'class="v2-identity-versions"' in markup
    assert "pyproject.toml" not in markup


def test_identity_html_escapes_demo_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ui.presentation.shell.DEMO_MODE_LABEL",
        '<img src=x onerror="alert(1)">',
    )
    markup = identity_html(demo=True, mode=None)
    assert "<img" not in markup
    assert "&lt;img" in markup
    assert "Saved example" not in markup


def test_demo_identity_is_quiet_status_not_a_pill() -> None:
    markup = identity_html(demo=True, mode=None)
    assert 'class="v2-demo-status"' in markup
    assert 'class="v2-demo-dot"' in markup
    assert DEMO_MODE_LABEL in markup
    assert "v2-pill" not in markup
    assert "v2-pill-active" not in markup
    assert "Saved example" not in markup


def test_analysis_mode_does_not_add_a_header_pill() -> None:
    markup = identity_html(demo=True, mode="one-battery")
    assert 'class="v2-demo-status"' in markup
    assert "v2-pill" not in markup
    assert "Evaluate one battery" not in markup
    assert "Find a battery size" not in markup


def test_identity_uses_version_module_not_inline_literals() -> None:
    from ui.presentation import shell
    from ui.version import UI_VERSION, read_ui_version

    assert shell.UI_VERSION is UI_VERSION
    assert UI_VERSION == read_ui_version()
