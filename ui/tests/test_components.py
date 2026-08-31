from __future__ import annotations

from ui.presentation.components import (
    ActionRowEvent,
    action_row_alignment,
    render_display_table,
    render_status_panel,
    render_text_table,
    resolve_choice_selection,
)
from ui.presentation.tokens import StatusTone


def test_status_tones_are_the_four_semantic_states() -> None:
    allowed: tuple[StatusTone, ...] = ("success", "warning", "danger", "info")
    assert set(allowed) == {"success", "warning", "danger", "info"}
    assert callable(render_status_panel)
    assert callable(render_display_table)
    assert callable(render_text_table)


def test_choice_selection_returns_clicked_option() -> None:
    ids = ("one-battery", "size")
    assert (
        resolve_choice_selection(
            selected="one-battery",
            clicked="size",
            disabled=False,
            option_ids=ids,
        )
        == "size"
    )


def test_choice_selection_respects_disabled() -> None:
    ids = ("one-battery", "size")
    assert (
        resolve_choice_selection(
            selected="size",
            clicked="one-battery",
            disabled=True,
            option_ids=ids,
        )
        == "size"
    )


def test_choice_selection_ignores_unknown_click() -> None:
    ids = ("one-battery", "size")
    assert (
        resolve_choice_selection(
            selected="one-battery",
            clicked="other",
            disabled=False,
            option_ids=ids,
        )
        == "one-battery"
    )


def test_action_row_event_exposes_back_and_primary() -> None:
    event = ActionRowEvent(back=True, primary=False)
    assert event.back is True
    assert event.primary is False


def test_action_row_keeps_primary_on_the_right() -> None:
    assert action_row_alignment(has_back=True) == "distribute"
    assert action_row_alignment(has_back=False) == "right"


def test_continue_reason_only_when_primary_disabled() -> None:
    from ui.presentation.components import continue_reason_text

    assert continue_reason_text("Enter a site or project name.", primary_disabled=True) == (
        "To continue: Enter a site or project name."
    )
    assert continue_reason_text("Enter a site or project name.", primary_disabled=False) is None
    assert continue_reason_text(None, primary_disabled=True) is None
    assert continue_reason_text("", primary_disabled=True) is None
