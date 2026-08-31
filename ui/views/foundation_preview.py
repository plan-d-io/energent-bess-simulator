"""Development-only preview of the V2 shell and shared components.

This is not the simulator workflow. Figures are stored Ganda Cars 2024
example values from the approved visual reference. They are not calculated.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui.presentation.components import (
    render_acknowledgement_panel,
    render_action_row,
    render_blocked_state,
    render_chart_frame,
    render_choice_cards,
    render_empty_state,
    render_expander,
    render_loading_state,
    render_metric_group,
    render_page_header,
    render_section_heading,
    render_status_panel,
    render_table_frame,
)
from ui.presentation.shell import app_shell
from ui.presentation.tokens import (
    MODE_ONE_BATTERY_LABEL,
    MODE_SIZE_LABEL,
    SAVED_EXAMPLE_LABEL,
)

# Stored Ganda Cars 2024 figures from the approved TSX reference.
_SITE = "Ganda Cars"
_PERIOD = "Calendar year 2024"
_BATTERY = "100 kWh / 50 kW"
_PAYBACK = "16.6 years"
_PV_MWH = "262.98"
_LOAD_MWH = "94.93"
_USEFUL_BEFORE = "40.77"
_UNVALIDATED = "96"
_UNVALIDATED_DATE = "2024-10-02"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_PEAK_NONE = [115.5, 90.2, 18.7, 84.7, 122.1, 77.0, 60.5, 143.0, 105.6, 103.4, 206.8, 117.7]
_PEAK_REVENUE = [65.5, 40.2, 3.2, 34.7, 72.1, 27.0, 10.5, 93.0, 60.8, 53.4, 156.8, 81.1]

_PREVIEW_LABELS = (
    "Form-width shell",
    "Wide shell",
    "Component states",
    "Demo treatment",
)

_CHOICES = (
    ("one-battery", MODE_ONE_BATTERY_LABEL, "Simulate one battery size using all dispatch strategies.\n"),
    ("size", MODE_SIZE_LABEL, "Compare a range of sizes using the revenue maximisation dispatch strategy."),
)


def _stored_peak_chart() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "No battery": _PEAK_NONE,
            "Revenue maximisation": _PEAK_REVENUE,
        },
        index=_MONTHS,
    )


_PREVIEW_CHOICE_KEY = "v2_preview_choice"


def _render_dev_preview_selector() -> str:
    with st.container(key="v2-dev-preview"):
        st.caption("Development preview — not the simulator workflow")
        st.markdown("**V2 foundation preview**")
        return st.radio(
            "Composition",
            options=list(_PREVIEW_LABELS),
            index=0,
            horizontal=True,
            key="v2_foundation_preview",
        )


def _render_form_shell() -> None:
    selected = st.session_state.get(_PREVIEW_CHOICE_KEY, "one-battery")
    with app_shell(current_step=4, max_available=4, width="form", mode="one-battery"):
        render_page_header(
            "Step 4 of 6",
            "Configure options",
            "Choose and configure the simulation run",
        )
        render_section_heading("What do you want to do?")
        picked = render_choice_cards(
            _CHOICES,
            selected=selected,
            key="v2_preview_live_choice",
        )
        st.session_state[_PREVIEW_CHOICE_KEY] = picked
        render_action_row(back="Back", primary="Continue")


def _render_wide_shell() -> None:
    with app_shell(current_step=6, max_available=6, width="wide", mode="one-battery"):
        render_page_header("Step 6 of 6", f"{_SITE}: results")
        st.caption("Stored example data. Not recalculated.")
        render_metric_group(
            (
                ("Site", _SITE),
                ("Period", _PERIOD),
                ("Battery", _BATTERY),
                ("Simple payback period", _PAYBACK),
            )
        )
        render_table_frame(
            title="Site totals",
            caption="Stored Ganda Cars 2024 example · complete calendar year",
            data={
                "Metric": [
                    "PV production (MWh)",
                    "Site load (MWh)",
                    "Useful PV supplied to the customer before the battery (MWh)",
                ],
                "Value": [_PV_MWH, _LOAD_MWH, _USEFUL_BEFORE],
            },
        )
        render_chart_frame(
            title="Highest 15-minute grid import by month (kW)",
            x_label="Month",
            y_label="Highest 15-minute grid import (kW)",
            caption="Stored Ganda Cars 2024 example · complete local months",
            data=_stored_peak_chart(),
        )


def _render_component_states() -> None:
    with app_shell(current_step=2, max_available=2, width="form"):
        render_page_header("Step 2 of 6", "Data verification")
        render_status_panel(
            "success",
            "Files usable",
            "Offtake, injection and PV production share a common quarter-hour coverage.",
        )
        render_acknowledgement_panel(
            title=f"Data contains {_UNVALIDATED} unvalidated quarter-hours",
            facts=[],
            checkbox_label=(
                f"Use {_UNVALIDATED} unvalidated readings on {_UNVALIDATED_DATE}. "
                "Only non-empty readings are used."
            ),
            detail=(
                f"Affected local date: {_UNVALIDATED_DATE}\n\n"
                "Only non-empty readings are used."
            ),
            key="v2_preview_ack",
        )
        render_blocked_state(
            "The PV production role is missing",
            "None of these files contains Productie Actief.",
        )
        render_loading_state(
            "Detecting offtake, injection and PV production from active-energy registers."
        )
        render_empty_state("Select the three Fluvius CSV exports to continue.")
        render_expander(
            "Advanced settings",
            "Charge and discharge power, efficiencies, cycle limit, peak window.",
        )
        render_action_row(
            primary="Continue",
            primary_disabled=True,
            disabled_reason="Identify three valid Fluvius files.",
        )


def _render_demo_treatment() -> None:
    with app_shell(
        current_step=4,
        max_available=4,
        width="form",
        mode="size",
        demo=True,
    ):
        render_page_header("Step 4 of 6", "Configure options")
        st.info(
            f"**{SAVED_EXAMPLE_LABEL}: {_SITE}**\n\n"
            "These settings belong to the saved Ganda Cars demonstration. "
            "Turn off Demo mode to change them."
        )
        render_choice_cards(
            _CHOICES,
            selected="size",
            disabled=True,
            key="v2_preview_demo_choice",
        )
        st.text_input("Estimated battery cost (EUR/kWh usable capacity)", value="300", disabled=True)
        render_action_row(back="Back", primary="Continue")


def render_foundation_preview() -> None:
    choice = _render_dev_preview_selector()
    if choice == "Form-width shell":
        _render_form_shell()
    elif choice == "Wide shell":
        _render_wide_shell()
    elif choice == "Component states":
        _render_component_states()
    else:
        _render_demo_treatment()
