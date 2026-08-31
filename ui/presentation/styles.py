"""Scoped Streamlit CSS for V2. Token values are interpolated from ui.presentation.tokens."""

from __future__ import annotations

import streamlit as st

from ui.presentation import tokens as t


def stylesheet() -> str:
    """Return the V2 stylesheet. Selectors are scoped to the V2 app."""
    return f"""
:root, .stApp {{
  --primary-color: {t.PRIMARY};
}}
.stApp {{
  background: {t.PAGE_BG};
  color: {t.TEXT};
}}
.stApp .block-container {{
  padding-top: 3rem;
  padding-bottom: 2rem;
  max-width: 100%;
}}
.stApp [data-testid="stBaseButton-primary"] {{
  background-color: {t.PRIMARY};
  border-color: {t.PRIMARY};
  color: {t.SURFACE};
}}
.stApp [data-testid="stBaseButton-primary"]:hover:not(:disabled) {{
  background-color: {t.PRIMARY_HOVER};
  border-color: {t.PRIMARY_HOVER};
}}
.stApp [data-testid="stBaseButton-primary"]:focus-visible {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.stApp [data-testid="stBaseButton-primary"]:disabled {{
  opacity: 0.55;
}}
.stApp [data-testid="stBaseButton-primary"],
.stApp [data-testid="stBaseButton-secondary"],
.stApp [data-testid="stBaseButton-tertiary"] {{
  min-height: {t.TAP_MIN_PX}px;
  font-weight: 600;
}}
.stApp [data-testid="stRadio"] input,
.stApp [data-testid="stCheckbox"] input {{
  accent-color: {t.PRIMARY};
}}
.stApp [data-testid="stRadioOption"][data-selected="true"] > div > div > div:not([data-testid="stMarkdownContainer"]) {{
  background-color: {t.PRIMARY} !important;
  border-color: {t.PRIMARY} !important;
}}
.stApp [data-testid="stRadioOption"][data-selected="true"] > div > div > div:not([data-testid="stMarkdownContainer"]) > div {{
  background-color: {t.SURFACE} !important;
}}
.stApp [data-testid="stCheckbox"] label[data-selected="true"] > span + div {{
  background-color: {t.PRIMARY} !important;
  border-color: {t.PRIMARY} !important;
}}
.stApp [data-testid="stRadio"] input:focus,
.stApp [data-testid="stRadio"] input:focus-visible,
.stApp [data-testid="stCheckbox"] input:focus,
.stApp [data-testid="stCheckbox"] input:focus-visible {{
  outline: none;
}}
.stApp [data-testid="stRadioOption"][data-focus-visible] {{
  background-color: transparent !important;
}}
.stApp [data-testid="stRadioOption"][data-focus-visible] > div > div > div:not([data-testid="stMarkdownContainer"]) {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.stApp [data-testid="stCheckbox"] label[data-focus-visible] > span + div {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.stApp [data-testid="stTextInput"]:focus-within div,
.stApp [data-testid="stNumberInput"]:focus-within div,
.stApp [data-testid="stTimeInput"]:focus-within div,
.stApp [data-testid="stDateInput"]:focus-within div,
.stApp [data-testid="stTextArea"]:focus-within div {{
  border-color: {t.PRIMARY} !important;
  box-shadow: none !important;
  outline: none !important;
}}
.stApp [data-testid="stTextArea"] textarea:focus,
.stApp [data-testid="stTextArea"] textarea:focus-visible {{
  border-color: {t.PRIMARY} !important;
  outline: none !important;
}}
.stApp [data-testid="stTextInput"] input:focus,
.stApp [data-testid="stNumberInput"] input:focus,
.stApp [data-testid="stTextInput"] input:focus-visible,
.stApp [data-testid="stNumberInput"] input:focus-visible {{
  caret-color: {t.PRIMARY};
  outline: none !important;
}}
.stApp [data-testid="stRadio"] input:disabled,
.stApp [data-testid="stCheckbox"] input:disabled,
.stApp [data-testid="stRadioOption"][aria-disabled="true"],
.stApp [data-testid="stCheckbox"] label[aria-disabled="true"] {{
  opacity: 0.55;
}}
.stApp [data-testid="stMetricValue"] {{
  font-variant-numeric: {t.TABULAR_NUMS};
}}
.stApp [data-testid="stDataFrame"] {{
  font-variant-numeric: {t.TABULAR_NUMS};
}}
.st-key-v2-shell,
.st-key-v2-chrome {{
  display: flex !important;
  flex-direction: column !important;
  align-items: stretch;
  box-sizing: border-box;
  width: 100%;
}}
.st-key-v2-body-form,
.st-key-v2-body-wide,
.st-key-v2-dev-preview {{
  display: block;
  box-sizing: border-box;
  width: 100%;
}}
.st-key-v2-identity-row {{
  display: block;
  width: 100%;
  flex: 0 0 auto !important;
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-chrome {{
  width: 100%;
}}
.st-key-v2-body-wide,
.st-key-v2-dev-preview {{
  max-width: {t.WIDE_WIDTH_PX}px;
  margin-left: auto;
  margin-right: auto;
}}
.st-key-v2-body-form {{
  max-width: {t.FORM_WIDTH_PX}px;
  margin-left: auto;
  margin-right: auto;
}}
.st-key-v2-dev-preview {{
  margin-bottom: {t.SPACE_LG}px;
  padding: {t.SPACE_MD}px {t.SPACE_LG}px;
  border: 1px dashed {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  background: {t.SURFACE_MUTED};
}}
[class*="st-key-v2-choice-selected"] {{
  border: 2px solid {t.PRIMARY} !important;
  background: {t.SURFACE_MUTED};
  border-radius: {t.RADIUS_PX}px;
}}
[class*="st-key-v2-choice-idle"] {{
  border: 1px solid {t.BORDER} !important;
  background: {t.SURFACE};
  border-radius: {t.RADIUS_PX}px;
}}
.v2-choice-body {{
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  line-height: 1.4;
  margin: {t.SPACE_SM}px 0 0;
  min-height: 2.8em;
}}
.st-key-v2-chart-frame,
.st-key-v2-table-frame {{
  border: 1px solid {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  background: {t.SURFACE};
  padding: {t.SPACE_MD}px;
}}
.st-key-v2-metrics [data-testid="stMetricValue"],
.st-key-v2-metrics [data-testid="stMetricValue"] *,
[class*="st-key-v2-metrics"] [data-testid="stMetricValue"],
[class*="st-key-v2-metrics"] [data-testid="stMetricValue"] * {{
  font-variant-numeric: {t.TABULAR_NUMS};
  font-size: 1.05rem;
  font-weight: 600;
  line-height: 1.3;
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  overflow-wrap: break-word;
}}
.st-key-v2-metrics [data-testid="stMetric"],
[class*="st-key-v2-metrics"] [data-testid="stMetric"] {{
  min-width: 0;
}}
.v2-identity {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: {t.SPACE_SM}px;
  margin-bottom: {t.SPACE_SM}px;
}}
.v2-identity-stack {{
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}}
.v2-identity-name {{
  font-size: 1.18rem;
  font-weight: 600;
  line-height: 1.2;
  color: {t.TEXT};
}}
.v2-identity-versions {{
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: 0.75rem;
  font-weight: 400;
  line-height: 1.3;
  color: {t.TEXT_MUTED};
}}
.v2-identity-version {{
  display: block;
  font-size: inherit;
  font-weight: inherit;
  color: inherit;
  line-height: inherit;
}}
.st-key-v2-action-row {{
  margin-top: {t.SPACE_MD}px;
}}
.st-key-v2-action-row .v2-continue-reason,
.v2-continue-reason {{
  margin: 0 0 {t.SPACE_SM}px;
  text-align: right;
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
}}
.v2-demo-status {{
  display: inline-flex;
  align-items: center;
  gap: {t.SPACE_XS}px;
  font-size: 0.85rem;
  font-weight: 500;
  color: {t.TEXT_SECONDARY};
  pointer-events: none;
}}
.v2-demo-dot {{
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: {t.PRIMARY};
  flex-shrink: 0;
}}
.v2-pill {{
  display: inline-flex;
  align-items: center;
  min-height: {t.TAP_MIN_PX}px;
  padding: 0 {t.SPACE_MD}px;
  border: 1px solid {t.BORDER};
  border-radius: 999px;
  font-size: 0.85rem;
  background: {t.SURFACE};
  color: {t.TEXT};
}}
.v2-pill-active {{
  border-color: {t.PRIMARY};
  background: {t.PRIMARY};
  color: {t.SURFACE};
  font-weight: 600;
}}
.v2-stepper {{
  margin: {t.SPACE_SM}px 0 {t.SPACE_SM}px;
  max-width: 100%;
}}
.st-key-v2-stepper {{
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  justify-content: flex-start !important;
  align-items: center !important;
  gap: {t.SPACE_SM}px !important;
  width: 100%;
  flex: 0 0 auto !important;
}}
.st-key-v2-stepper[data-testid="stHorizontalBlock"],
.st-key-v2-stepper [data-testid="stHorizontalBlock"] {{
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  justify-content: flex-start !important;
  align-items: center !important;
  gap: {t.SPACE_SM}px !important;
  width: 100%;
}}
.st-key-v2-stepper > *,
.st-key-v2-stepper [data-testid="stHorizontalBlock"] > *,
[class*="st-key-v2-step-"] {{
  flex: 0 0 auto !important;
  width: auto !important;
  max-width: max-content !important;
  min-width: fit-content;
}}
[class*="st-key-v2-step-"] [data-testid="stBaseButton-secondary"] {{
  border-radius: 999px;
  min-height: {t.TAP_MIN_PX}px;
  padding: 0 {t.SPACE_MD}px;
  font-size: 0.85rem;
  font-weight: 500;
  white-space: nowrap;
}}
[class*="st-key-v2-step-current-"] [data-testid="stBaseButton-secondary"]:disabled {{
  opacity: 1;
  border-color: {t.PRIMARY};
  border-width: 2px;
  color: {t.TEXT};
  font-weight: 600;
  background: {t.SURFACE};
  pointer-events: none;
}}
[class*="st-key-v2-step-complete-"] [data-testid="stBaseButton-secondary"] {{
  border-color: {t.PRIMARY};
  color: {t.TEXT};
}}
[class*="st-key-v2-step-unlocked-"] [data-testid="stBaseButton-secondary"] {{
  border-color: {t.BORDER};
  background: {t.SURFACE};
  color: {t.TEXT_SECONDARY};
}}
[class*="st-key-v2-step-unavailable-"] [data-testid="stBaseButton-secondary"] {{
  border-style: dashed;
  background: {t.SURFACE_MUTED};
  color: {t.TEXT_MUTED};
}}
.v2-step {{
  display: inline-flex;
  align-items: center;
  gap: {t.SPACE_SM}px;
  min-height: {t.TAP_MIN_PX}px;
  padding: 0 {t.SPACE_MD}px 0 {t.SPACE_SM}px;
  border: 1px solid {t.BORDER};
  border-radius: 999px;
  background: {t.SURFACE};
  color: {t.TEXT};
  font-size: 0.85rem;
  flex: 0 1 auto;
}}
.v2-step-num {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.4rem;
  font-variant-numeric: {t.TABULAR_NUMS};
  font-weight: 600;
}}
.v2-step-current {{
  border-color: {t.PRIMARY};
  border-width: 2px;
  background: {t.SURFACE};
  color: {t.TEXT};
  font-weight: 600;
  pointer-events: none;
}}
.v2-step-current .v2-step-num {{
  color: {t.PRIMARY};
}}
.v2-step-complete {{
  border-color: {t.PRIMARY};
  color: {t.TEXT};
}}
.v2-step-unlocked {{
  border-color: {t.BORDER};
  color: {t.TEXT_SECONDARY};
}}
.v2-step-unavailable {{
  background: {t.SURFACE_MUTED};
  color: {t.TEXT_MUTED};
  border-style: dashed;
}}
.v2-rule {{
  height: 1px;
  background: {t.BORDER};
  margin: 0;
  border: 0;
}}
.v2-kicker {{
  display: block;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: {t.TEXT_MUTED};
  line-height: 1.3;
  margin: 0;
  padding-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-page-header {{
  display: block;
  width: 100%;
  margin-bottom: {t.SPACE_MD}px;
}}
.v2-page-lead {{
  display: block;
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  line-height: 1.4;
  margin: 0 0 {t.SPACE_MD}px;
  padding-bottom: {t.SPACE_SM}px;
}}
.v2-section-lead {{
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  line-height: 1.45;
  margin: 0 0 {t.SPACE_MD}px;
  padding-bottom: {t.SPACE_XS}px;
  overflow: visible;
}}
.st-key-v2-body-form [data-testid="stHeading"],
.st-key-v2-body-wide [data-testid="stHeading"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-body-form [data-testid="stDataFrame"],
.st-key-v2-body-wide [data-testid="stDataFrame"] {{
  margin-top: {t.SPACE_SM}px;
}}
.st-key-v2-body-form [data-testid="stTextInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stTextInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stNumberInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stNumberInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stTextArea"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stTextArea"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stFileUploader"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stFileUploader"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stSelectbox"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stSelectbox"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stMultiSelect"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stMultiSelect"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stDateInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stDateInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stTimeInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stTimeInput"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-form [data-testid="stSlider"] [data-testid="stWidgetLabel"] p,
.st-key-v2-body-wide [data-testid="stSlider"] [data-testid="stWidgetLabel"] p {{
  color: {t.TEXT_SECONDARY};
  font-size: 0.9rem;
  font-weight: 500;
}}
.st-key-v2-body-form [data-testid="stTextInput"] [data-testid="stWidgetLabel"],
.st-key-v2-body-wide [data-testid="stTextInput"] [data-testid="stWidgetLabel"],
.st-key-v2-body-form [data-testid="stFileUploader"] [data-testid="stWidgetLabel"],
.st-key-v2-body-wide [data-testid="stFileUploader"] [data-testid="stWidgetLabel"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-period-select {{
  display: block;
  margin: 0 0 {t.SPACE_LG}px;
}}
.st-key-v2-period-select [data-testid="stSelectbox"] [data-testid="stWidgetLabel"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-period-select [data-testid="stSelectbox"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-period-select [data-testid="stCaptionContainer"] {{
  margin: 0 0 {t.SPACE_SM}px;
}}
.st-key-v2-period-select [data-testid="stExpander"] {{
  margin: 0;
}}
.st-key-v2-period-ack,
.st-key-v2-period-boundary {{
  display: block;
  margin: 0 0 {t.SPACE_LG}px;
}}
.st-key-v2-period-ack [data-testid="stAlert"],
.st-key-v2-period-boundary [data-testid="stAlert"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-period-ack [data-testid="stExpander"],
.st-key-v2-period-boundary [data-testid="stExpander"] {{
  margin: 0 0 {t.SPACE_SM}px;
}}
.st-key-v2-period-ack [data-testid="stCheckbox"],
.st-key-v2-period-boundary [data-testid="stCheckbox"] {{
  margin-bottom: 0;
}}
.st-key-v2-configure {{
  display: block;
}}
.st-key-v2-configure [data-testid="stNumberInput"],
.st-key-v2-configure [data-testid="stTextInput"],
.st-key-v2-configure [data-testid="stTextArea"],
.st-key-v2-configure [data-testid="stCheckbox"],
.st-key-v2-configure [data-testid="stRadio"],
.st-key-v2-configure [data-testid="stTimeInput"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-configure [data-testid="stNumberInput"] [data-testid="stWidgetLabel"],
.st-key-v2-configure [data-testid="stTextInput"] [data-testid="stWidgetLabel"],
.st-key-v2-configure [data-testid="stTextArea"] [data-testid="stWidgetLabel"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-body-form [data-testid="stTextInput"],
.st-key-v2-body-form [data-testid="stFileUploader"] {{
  margin-bottom: {t.SPACE_LG}px;
}}
.st-key-v2-body-form [data-testid="stCheckbox"] {{
  margin-bottom: {t.SPACE_LG}px;
}}
.st-key-v2-configure [data-testid="stCheckbox"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-v2-review {{
  display: block;
}}
.st-key-v2-review [data-testid="stExpander"] {{
  margin: 0 0 {t.SPACE_MD}px;
}}
.st-key-v2-review [data-testid="stAlert"] {{
  margin: 0 0 {t.SPACE_MD}px;
}}
.st-key-v2-review [data-testid="stTable"] {{
  overflow: visible !important;
}}
.st-key-v2-upload-followup {{
  display: block;
  margin: 0 0 {t.SPACE_LG}px;
}}
.st-key-v2-upload-followup [data-testid="stCaptionContainer"] {{
  margin-bottom: {t.SPACE_MD}px;
}}
.st-key-v2-upload-followup [data-testid="stTable"] {{
  margin-top: {t.SPACE_SM}px;
}}
.st-key-v2-body-form [data-testid="stTable"],
.st-key-v2-body-wide [data-testid="stTable"] {{
  overflow: visible !important;
  margin-bottom: {t.SPACE_MD}px;
}}
.st-key-v2-body-form [data-testid="stTable"] table,
.st-key-v2-body-wide [data-testid="stTable"] table {{
  width: 100%;
  table-layout: auto;
  background: {t.SURFACE};
}}
.st-key-v2-body-form [data-testid="stTable"] th,
.st-key-v2-body-wide [data-testid="stTable"] th {{
  background: {t.SURFACE_MUTED} !important;
  color: {t.TEXT};
}}
.st-key-v2-body-form [data-testid="stTable"] td,
.st-key-v2-body-wide [data-testid="stTable"] td {{
  background: {t.SURFACE} !important;
  color: {t.TEXT};
}}
.st-key-v2-body-form [data-testid="stTable"] th,
.st-key-v2-body-form [data-testid="stTable"] td,
.st-key-v2-body-wide [data-testid="stTable"] th,
.st-key-v2-body-wide [data-testid="stTable"] td {{
  white-space: normal !important;
  overflow-wrap: anywhere;
  word-break: break-word;
}}
.st-key-v2-review .v2-text-table {{
  width: 100%;
  max-width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  background: {t.SURFACE};
  margin: 0 0 {t.SPACE_MD}px;
}}
.st-key-v2-review .v2-text-table th {{
  background: {t.SURFACE_MUTED};
  color: {t.TEXT};
  font-weight: 600;
  text-align: left;
}}
.st-key-v2-review .v2-text-table td {{
  background: {t.SURFACE};
  color: {t.TEXT};
}}
.st-key-v2-review .v2-text-table th,
.st-key-v2-review .v2-text-table td {{
  padding: 0.5rem 0.65rem;
  vertical-align: top;
  white-space: normal;
  overflow-wrap: break-word;
  word-break: normal;
  border-bottom: 1px solid {t.BORDER};
}}
.st-key-v2-review .v2-text-table th:first-child,
.st-key-v2-review .v2-text-table td:first-child {{
  width: 13.5rem;
}}
.st-key-v2-compare-results [data-testid="stExpander"] {{
  margin-bottom: {t.SPACE_MD}px;
}}
.st-key-v2-compare-results [data-testid="stTable"] {{
  overflow: visible !important;
}}
.st-key-v2-compare-results [data-testid="stTable"] table {{
  width: 100%;
  table-layout: auto;
}}
.st-key-v2-compare-results [data-testid="stTable"] th,
.st-key-v2-compare-results [data-testid="stTable"] td {{
  overflow-wrap: break-word !important;
  word-break: normal !important;
}}
.st-key-v2-compare-results [data-testid="stTable"] th:not(:first-child),
.st-key-v2-compare-results [data-testid="stTable"] td:not(:first-child) {{
  min-width: 8.75rem;
  white-space: nowrap !important;
}}
.st-key-v2-compare-results [data-testid$="Chart"] {{
  width: 100%;
  max-width: 100%;
  overflow: visible;
}}
.st-key-v2-compare-results [data-testid="stVegaLiteChart"] {{
  min-height: 360px;
}}
.st-key-v2-sweep-results [data-testid="stExpander"] {{
  margin-bottom: {t.SPACE_MD}px;
}}
.st-key-v2-sweep-results [data-testid="stTable"] {{
  overflow: visible !important;
}}
.st-key-v2-sweep-results [data-testid="stTable"] table {{
  width: 100%;
  table-layout: auto;
}}
.st-key-v2-sweep-results [data-testid="stTable"] th,
.st-key-v2-sweep-results [data-testid="stTable"] td {{
  overflow-wrap: break-word !important;
  word-break: normal !important;
}}
.st-key-v2-sweep-results [data-testid="stTable"] th:not(:first-child),
.st-key-v2-sweep-results [data-testid="stTable"] td:not(:first-child) {{
  min-width: 8.75rem;
  white-space: nowrap !important;
}}
.st-key-v2-sweep-results [data-testid$="Chart"] {{
  width: 100%;
  max-width: 100%;
  overflow: visible;
}}
.st-key-v2-sweep-highlights [data-testid="stHorizontalBlock"] {{
  align-items: stretch;
}}
.st-key-v2-sweep-highlights [data-testid="stColumn"] {{
  display: flex;
  flex-direction: column;
}}
.st-key-v2-sweep-highlights [data-testid="stColumn"] > div {{
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  height: 100%;
}}
.st-key-v2-sweep-highlights [data-testid="stVerticalBlockBorderWrapper"],
.st-key-v2-sweep-highlights [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"] {{
  flex: 1 1 auto;
  height: 100%;
}}
[class*="st-key-v2-status-group-"] {{
  display: block;
  margin: 0 0 {t.SPACE_LG}px;
}}
[class*="st-key-v2-status-group-"] [data-testid="stAlert"] {{
  margin-bottom: {t.SPACE_MD}px;
}}
.v2-axis-label {{
  color: {t.TEXT_SECONDARY};
  font-size: 0.9rem;
  margin: 0 0 {t.SPACE_SM}px;
}}
.v2-caption {{
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  margin-top: {t.SPACE_SM}px;
}}
.st-key-app-footer {{
  display: block;
  box-sizing: border-box;
  width: 100%;
  max-width: {t.WIDE_WIDTH_PX}px;
  margin-top: {t.SPACE_XXL}px;
  margin-left: auto;
  margin-right: auto;
  padding-top: {t.SPACE_MD}px;
  padding-bottom: {t.SPACE_MD}px;
}}
.st-key-app-footer .app-footer-rule {{
  display: block;
  width: 100%;
  height: 0;
  margin: 0 0 {t.SPACE_MD}px;
  border: 0;
  border-top: 1px solid {t.BORDER};
  background: none;
}}
.st-key-app-footer .app-footer-copy {{
  margin: 0;
  text-align: right;
  font-weight: 400;
  line-height: 1.4;
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
}}
.st-key-app-footer a {{
  color: inherit;
  text-decoration: underline;
}}
.st-key-app-footer a:hover {{
  color: {t.TEXT_SECONDARY};
}}
.st-key-app-footer a:focus-visible {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.v2-empty {{
  border: 1px dashed {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  padding: {t.SPACE_LG}px;
  color: {t.TEXT_SECONDARY};
  background: {t.SURFACE};
}}
@media (max-width: {t.NARROW_BREAKPOINT_PX}px) {{
  .st-key-v2-stepper,
  .st-key-v2-stepper[data-testid="stHorizontalBlock"],
  .st-key-v2-stepper [data-testid="stHorizontalBlock"] {{
    flex-wrap: wrap !important;
  }}
  .v2-step-name {{
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }}
  .v2-step {{
    padding: 0 {t.SPACE_SM}px;
  }}
  .v2-identity {{
    align-items: flex-start;
  }}
  .st-key-v2-metrics [data-testid="stHorizontalBlock"],
  [class*="st-key-v2-metrics"] [data-testid="stHorizontalBlock"] {{
    flex-wrap: wrap;
  }}
  .st-key-v2-metrics [data-testid="stColumn"],
  [class*="st-key-v2-metrics"] [data-testid="stColumn"] {{
    min-width: min(100%, 220px);
    flex: 1 1 220px;
  }}
  .st-key-v2-sweep-highlights [data-testid="stHorizontalBlock"] {{
    align-items: flex-start;
  }}
  .st-key-v2-sweep-highlights [data-testid="stColumn"] > div,
  .st-key-v2-sweep-highlights [data-testid="stVerticalBlockBorderWrapper"],
  .st-key-v2-sweep-highlights [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"] {{
    height: auto;
    flex: 0 1 auto;
  }}
  .st-key-app-footer {{
    max-width: 100%;
  }}
}}
""".strip()


def inject_styles() -> None:
    """Inject V2 CSS once into the current page."""
    st.markdown(f"<style>\n{stylesheet()}\n</style>", unsafe_allow_html=True)
