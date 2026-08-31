"""Semantic design tokens for the Battery Simulator V2 interface."""

from __future__ import annotations

from typing import Literal

AnalysisMode = Literal["one-battery", "size"]
StepStatus = Literal["complete", "current", "unlocked", "unavailable"]
WidthVariant = Literal["form", "wide"]
StatusTone = Literal["success", "warning", "danger", "info"]

STEPS: tuple[str, ...] = (
    "Upload data",
    "Data verification",
    "Simulation period",
    "Configure options",
    "Review and run",
    "Results",
)

APP_NAME = "Battery simulator"
DEMO_MODE_LABEL = "Demo mode"
SAVED_EXAMPLE_LABEL = "Saved example"
MODE_ONE_BATTERY_LABEL = "Evaluate one battery"
MODE_SIZE_LABEL = "Find a battery size"

# Energent logo mid-teal (opaque turbine / wordmark pigment #009898).
PRIMARY = "#009898"
PRIMARY_HOVER = "#007a7a"
PRIMARY_FOCUS = "#009898"

PAGE_BG = "#f4f6f7"
SURFACE = "#ffffff"
SURFACE_MUTED = "#eef1f3"

TEXT = "#1b1d21"
TEXT_SECONDARY = "#5c6570"
TEXT_MUTED = "#7a828c"

BORDER = "#d6dbe1"

SUCCESS = "#1f7a4d"
SUCCESS_BG = "#e7f5ee"
WARNING = "#9a6700"
WARNING_BG = "#fff6e5"
DANGER = "#b42318"
DANGER_BG = "#fdecea"
INFO = "#175cd3"
INFO_BG = "#eff4ff"

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 20
SPACE_XXL = 28

RADIUS_PX = 6
FORM_WIDTH_PX = 1120
WIDE_WIDTH_PX = 1120
NARROW_BREAKPOINT_PX = 900
TAP_MIN_PX = 36
LOGO_DISPLAY_PX = 72

TABULAR_NUMS = "tabular-nums"

# Chart series: teal, muted blue and neutral gray. Status colours are reserved.
CHART_NO_BATTERY = "#3d4450"
CHART_REFERENCE = "#8a9199"
CHART_SELF_CONSUMPTION = "#009898"
CHART_PEAK_REDUCTION = "#2a6f97"
CHART_REVENUE = "#1b6b93"
CHART_DYNAMIC = "#4aa3a8"
CHART_GRID = BORDER
CHART_PAPER = SURFACE
CHART_AXIS = TEXT_SECONDARY
CHART_SCENARIO = {
    "no_battery": CHART_NO_BATTERY,
    "reference": CHART_REFERENCE,
    "self_consumption": CHART_SELF_CONSUMPTION,
    "peak_reduction": CHART_PEAK_REDUCTION,
    "revenue": CHART_REVENUE,
    "dynamic_injection": CHART_DYNAMIC,
}
CHART_SERIES = {
    "PV production": "#5c6570",
    "Site use": "#1b1d21",
    "Site load": "#1b1d21",
    "Useful PV - no battery": CHART_NO_BATTERY,
    "Useful PV - battery": CHART_SELF_CONSUMPTION,
    "Grid import - no battery": CHART_NO_BATTERY,
    "Grid import - battery": CHART_PEAK_REDUCTION,
    "PV injection - no battery": CHART_REFERENCE,
    "PV injection - battery": CHART_DYNAMIC,
    "Charging": CHART_SELF_CONSUMPTION,
    "Discharging": CHART_PEAK_REDUCTION,
    "Discharge to customer": CHART_PEAK_REDUCTION,
    "Discharge to grid": CHART_DYNAMIC,
    "Stored energy": CHART_SELF_CONSUMPTION,
    "Day-ahead price": CHART_REVENUE,
    "Increase vs no battery": CHART_REVENUE,
    "PV sold directly": CHART_SELF_CONSUMPTION,
    "PV sold through battery": CHART_PEAK_REDUCTION,
    "Peak-period PV injection": CHART_REVENUE,
    "Off-peak PV injection": CHART_DYNAMIC,
    "Dynamic grid-injection revenue": CHART_DYNAMIC,
}

# Overlapping energy traces need more hue separation than the teal/blue
# scenario palette. Used by PV and grid energy and by the data explorer.
CHART_EXPLORER = {
    "PV production": "#c9892b",
    "Site use": "#1b1d21",
    "Useful PV - no battery": "#3d4450",
    "Useful PV - battery": "#009898",
    "Grid import - no battery": "#3d4450",
    "Grid import - battery": "#009898",
    "PV injection - no battery": "#6b8cae",
    "PV injection - battery": "#e07a1a",
    "Charging": "#009898",
    "Discharging": "#c45c26",
    "Discharge to customer": "#1b4f8a",
    "Discharge to grid": "#c45c26",
    "Stored energy": "#2a6f97",
    "Day-ahead price": "#6b3fa0",
}
