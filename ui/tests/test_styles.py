from __future__ import annotations

from ui.presentation.styles import stylesheet
from ui.presentation.tokens import (
    FORM_WIDTH_PX,
    PRIMARY,
    PRIMARY_FOCUS,
    PRIMARY_HOVER,
    WIDE_WIDTH_PX,
)


def test_stylesheet_uses_central_tokens() -> None:
    css = stylesheet()
    assert PRIMARY in css
    assert PRIMARY_HOVER in css
    assert str(FORM_WIDTH_PX) in css
    assert str(WIDE_WIDTH_PX) in css


def test_stylesheet_centres_chrome_and_form_body_separately() -> None:
    css = stylesheet()
    assert ".st-key-v2-chrome" in css
    assert ".st-key-v2-body-form" in css
    assert ".st-key-v2-body-wide" in css
    assert f"max-width: {WIDE_WIDTH_PX}px" in css
    assert f"max-width: {FORM_WIDTH_PX}px" in css
    assert "margin-left: auto" in css
    assert "margin-right: auto" in css


def test_stylesheet_selected_controls_use_primary_token() -> None:
    css = stylesheet()
    assert f"accent-color: {PRIMARY}" in css
    radio_circle = (
        '[data-testid="stRadioOption"][data-selected="true"] > div > div > '
        'div:not([data-testid="stMarkdownContainer"])'
    )
    assert radio_circle in css
    assert ':not([data-testid="stMarkdownContainer"])' in css
    radio_start = css.index(radio_circle)
    radio_block = css[radio_start : radio_start + len(radio_circle) + 160]
    assert f"background-color: {PRIMARY}" in radio_block
    assert '[data-testid="stCheckbox"] label[data-selected="true"] > span + div' in css
    radio_keyboard = (
        '[data-testid="stRadioOption"][data-focus-visible] > div > div > '
        'div:not([data-testid="stMarkdownContainer"])'
    )
    assert radio_keyboard in css
    checkbox_keyboard = (
        '[data-testid="stCheckbox"] label[data-focus-visible] > span + div'
    )
    assert checkbox_keyboard in css
    assert f"outline: 2px solid {PRIMARY_FOCUS}" in css
    assert f"--primary-color: {PRIMARY}" in css
    assert '[data-testid="stTextInput"]:focus-within div' in css
    assert f"border-color: {PRIMARY} !important" in css
    assert ":has(input:focus-visible)" not in css
    assert ":has(input:focus)" not in css
    assert '[data-testid="stRadioOption"]:has(:focus-visible) {' not in css
    assert '[data-testid="stRadioOption"]:focus-visible' not in css
    assert '[data-testid="stCheckbox"] label:has(:focus-visible) {' not in css
    assert "st-emotion-cache" not in css


def test_stylesheet_metrics_do_not_force_ellipsis() -> None:
    css = stylesheet()
    assert ".st-key-v2-metrics" in css
    assert "text-overflow: clip" in css
    assert "white-space: normal" in css
    assert "text-overflow: ellipsis" not in css


def test_stylesheet_demo_status_is_not_a_pill() -> None:
    css = stylesheet()
    assert ".v2-demo-status" in css
    assert ".v2-demo-dot" in css
    start = css.index(".v2-demo-status")
    end = css.index(".v2-pill")
    demo_block = css[start:end]
    assert "border:" not in demo_block
    assert "min-height" not in demo_block
    assert "border-radius: 999px" not in demo_block


def test_stylesheet_identity_and_continue_reason() -> None:
    css = stylesheet()
    assert ".v2-identity-name" in css
    assert "font-size: 1.18rem" in css
    assert ".v2-identity-versions" in css
    assert ".v2-continue-reason" in css
    assert "text-align: right" in css


def test_stylesheet_form_labels_use_stable_widget_selectors() -> None:
    css = stylesheet()
    assert '[data-testid="stTextInput"] [data-testid="stWidgetLabel"] p' in css
    assert '[data-testid="stFileUploader"] [data-testid="stWidgetLabel"] p' in css
    assert "font-size: 0.9rem" in css
    assert "font-weight: 500" in css
    form_label_start = css.index('[data-testid="stTextInput"] [data-testid="stWidgetLabel"] p')
    form_label_block = css[form_label_start : form_label_start + 1800]
    assert '[data-testid="stCheckbox"]' not in form_label_block
    assert '[data-testid="stRadio"]' not in form_label_block
    assert '[data-testid="stCaptionContainer"]' not in form_label_block
    assert '[data-testid="stMetricLabel"]' not in form_label_block


def test_stylesheet_page_lead_and_status_groups() -> None:
    from ui.presentation.tokens import SPACE_MD, SPACE_SM

    css = stylesheet()
    assert ".v2-page-lead" in css
    lead_start = css.index(".v2-page-lead")
    lead_block = css[lead_start : lead_start + 280]
    assert f"margin: 0 0 {SPACE_MD}px" in lead_block
    assert f"padding-bottom: {SPACE_SM}px" in lead_block
    assert ".st-key-v2-page-header" in css
    header_start = css.index(".st-key-v2-page-header")
    header_block = css[header_start : header_start + 180]
    assert f"margin-bottom: {SPACE_MD}px" in header_block
    kicker_start = css.index(".v2-kicker")
    kicker_block = css[kicker_start : kicker_start + 320]
    assert f"padding-bottom: {SPACE_SM}px" in kicker_block
    assert "0.15rem" not in kicker_block
    section_start = css.index(".v2-section-lead")
    section_block = css[section_start : section_start + 220]
    assert f"margin: 0 0 {SPACE_MD}px" in section_block
    assert "overflow: visible" in section_block
    assert ".v2-section-lead" in css
    assert "st-key-v2-status-group-" in css
    assert "<br" not in css.lower()


def test_stylesheet_stepper_stays_compact() -> None:
    css = stylesheet()
    stepper_start = css.index(".st-key-v2-stepper {")
    stepper_block = css[stepper_start : stepper_start + 420]
    assert "flex-wrap: nowrap" in stepper_block
    assert "justify-content: flex-start" in stepper_block
    assert "flex: 0 0 auto" in css[css.index("[class*=\"st-key-v2-step-\"]") :][:280]
    assert "st-key-v2-step-current-" in css


def test_stylesheet_chrome_stacks_identity_above_stages() -> None:
    from ui.presentation.tokens import SPACE_SM

    css = stylesheet()
    chrome_rule = css.split(".st-key-v2-body-form")[0]
    assert "flex-direction: column" in chrome_rule
    assert ".st-key-v2-identity-row" in css
    identity_start = css.index(".st-key-v2-identity-row")
    identity_block = css[identity_start : identity_start + 200]
    assert "width: 100%" in identity_block
    assert f"margin-bottom: {SPACE_SM}px" in identity_block
    assert ".st-key-v2-chrome [data-testid=\"stVerticalBlock\"]" not in css


def test_stylesheet_choice_cards_share_column_height() -> None:
    css = stylesheet()
    assert ".v2-choice-body" in css
    body_start = css.index(".v2-choice-body")
    body_block = css[body_start : body_start + 220]
    assert "min-height: 2.8em" in body_block
    assert "grid-template-columns" not in css
    assert ".st-key-v2-chrome [data-testid=\"stVerticalBlock\"]" not in css
    from ui.presentation.tokens import FORM_WIDTH_PX, WIDE_WIDTH_PX

    assert FORM_WIDTH_PX == WIDE_WIDTH_PX == 1120
    assert f".st-key-v2-body-form {{\n  max-width: {FORM_WIDTH_PX}px;" in css


def test_stylesheet_form_keeps_label_gap_without_inflating_stack() -> None:
    from ui.presentation.tokens import SPACE_LG, SPACE_MD, SPACE_SM

    css = stylesheet()
    label_start = css.index(
        ".st-key-v2-body-form [data-testid=\"stTextInput\"] [data-testid=\"stWidgetLabel\"],"
    )
    label_block = css[label_start : label_start + 360]
    assert f"margin-bottom: {SPACE_SM}px" in label_block
    reason_start = css.index(".v2-continue-reason")
    reason_block = css[reason_start : reason_start + 220]
    assert f"margin: 0 0 {SPACE_SM}px" in reason_block
    action_start = css.index(".st-key-v2-action-row {")
    action_block = css[action_start : action_start + 80]
    assert f"margin-top: {SPACE_MD}px" in action_block
    assert ".st-key-v2-upload-followup" in css
    follow_start = css.index(".st-key-v2-upload-followup {")
    follow_block = css[follow_start : follow_start + 120]
    assert f"margin: 0 0 {SPACE_LG}px" in follow_block
    checkbox_start = css.index(".st-key-v2-body-form [data-testid=\"stCheckbox\"] {")
    checkbox_block = css[checkbox_start : checkbox_start + 80]
    assert f"margin-bottom: {SPACE_LG}px" in checkbox_block
    widget_stack = css[
        css.index(".st-key-v2-body-form [data-testid=\"stTextInput\"],") :
        css.index(".st-key-v2-body-form [data-testid=\"stTextInput\"],") + 220
    ]
    assert f"margin-bottom: {SPACE_LG}px" in widget_stack


def test_stylesheet_display_tables_wrap_without_scroll_chrome() -> None:
    from ui.presentation.tokens import SPACE_MD, SURFACE, SURFACE_MUTED

    css = stylesheet()
    assert ".st-key-v2-body-form [data-testid=\"stTable\"] table" in css
    table_start = css.index(".st-key-v2-body-form [data-testid=\"stTable\"],")
    table_block = css[table_start : table_start + 220]
    assert "overflow: visible" in table_block
    assert f"margin-bottom: {SPACE_MD}px" in table_block
    wrap_start = css.index(".st-key-v2-body-form [data-testid=\"stTable\"] th,\n.st-key-v2-body-form [data-testid=\"stTable\"] td,")
    wrap_block = css[wrap_start : wrap_start + 320]
    assert "overflow-wrap: anywhere" in wrap_block
    assert "white-space: normal" in wrap_block
    alert_start = css.index("[class*=\"st-key-v2-status-group-\"] [data-testid=\"stAlert\"]")
    alert_block = css[alert_start : alert_start + 120]
    assert f"margin-bottom: {SPACE_MD}px" in alert_block
    header_rule = (
        ".st-key-v2-body-form [data-testid=\"stTable\"] th,\n"
        ".st-key-v2-body-wide [data-testid=\"stTable\"] th"
    )
    header_start = css.index(header_rule)
    header_block = css[header_start : header_start + 220]
    assert SURFACE_MUTED in header_block
    body_rule = (
        ".st-key-v2-body-form [data-testid=\"stTable\"] td,\n"
        ".st-key-v2-body-wide [data-testid=\"stTable\"] td"
    )
    body_start = css.index(body_rule)
    body_block = css[body_start : body_start + 180]
    assert SURFACE in body_block
    assert ".st-key-v2-review" in css
    assert ".st-key-v2-review [data-testid=\"stTable\"]" in css
    assert "overflow: visible" in css[css.index(".st-key-v2-review [data-testid=\"stTable\"]") :][:120]
    text_table = css[css.index(".st-key-v2-review .v2-text-table {") :]
    assert "max-width: 100%" in text_table[:180]
    assert "overflow-wrap: break-word" in text_table[:900]
    assert "word-break: normal" in text_table[:900]


def test_stylesheet_compare_results_value_columns_do_not_clip() -> None:
    css = stylesheet()
    start = css.index(".st-key-v2-compare-results [data-testid=\"stTable\"] th,")
    block = css[start : start + 520]
    assert "overflow-wrap: break-word !important" in block
    assert "word-break: normal !important" in block
    assert "min-width: 8.75rem" in block
    assert "th:not(:first-child)" in block
    assert "td:not(:first-child)" in block
    assert "white-space: nowrap !important" in block


def test_stylesheet_sweep_highlights_stretch_without_global_border_override() -> None:
    css = stylesheet()
    assert ".st-key-v2-sweep-highlights" in css
    start = css.index(".st-key-v2-sweep-highlights [data-testid=\"stHorizontalBlock\"]")
    assert "align-items: stretch" in css[start : start + 160]
    assert ".st-key-v2-sweep-highlights [data-testid=\"stVerticalBlockBorderWrapper\"]" in css
    for line in css.splitlines():
        stripped = line.strip()
        if stripped.startswith("[data-testid=\"stVerticalBlockBorderWrapper\"]"):
            raise AssertionError("global bordered-container override")


def test_stylesheet_has_no_forbidden_patterns() -> None:
    css = stylesheet().lower()
    assert "linear-gradient" not in css
    assert "radial-gradient" not in css
    assert "nth-child" not in css
    assert "<script" not in css
    assert "javascript:" not in css
    for line in css.splitlines():
        if "box-shadow" not in line:
            continue
        assert "0 0 0 1px" in line or line.strip() == "box-shadow: none !important;"
