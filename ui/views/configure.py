"""Step 4 Configure: Evaluate one battery and Find a battery size."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

import streamlit as st

from ui.flow import (
    CONFIGURE_WIDGET_PREFIX,
    analysis_mode_or_none,
    apply_analysis_mode,
    back_to_step3,
    continue_to_step5,
    is_saved_example,
)
from ui.services.candidates import resolve_live_candidates
from ui.services.configure import (
    CAPEX_CAPTION,
    CONFIGURE_LEAD,
    DEMO_READONLY,
    EXPLICIT_HELP,
    MODE_CARD_OPTIONS,
    MODE_ONE,
    MODE_SIZE,
    PARTIAL_PERIOD_NOTE,
    POWER_EXPLICIT,
    POWER_LABEL_TO_MODE,
    POWER_MANUAL,
    POWER_MODE_LABELS,
    POWER_SUGGESTED,
    PRICE_UNAVAILABLE_BODY,
    PRICE_UNAVAILABLE_TITLE,
    REASON_DEFAULTS,
    REASON_DEMO,
    REASON_STALE,
    RESTORE_HELP,
    SWEEP_COST_CAPTION,
    apply_configure_fields,
    apply_split_power,
    configure_entry_reason,
    continue_reason,
    estimated_capex_eur,
    format_eur,
    format_hhmm,
    format_percent,
    format_power_range,
    parse_hhmm,
    period_is_partial,
    prices_cover_period,
    restore_recommended_defaults,
    round_trip_percent,
    seed_manual_range_from_site,
    store_frozen_snapshot,
    ensure_configure_initialized,
    ensure_demo_configure,
)
from ui.services.defaults import load_defaults_snapshot
from ui.services.saved_example import load_saved_configure_context
from ui.presentation.components import (
    render_action_row,
    render_choice_cards,
    render_display_table,
    render_page_header,
    render_status_panel,
)
from ui.presentation.shell import app_shell
from ui.presentation.tokens import STEPS

_RESTORE_LABEL = "Restore recommended defaults"
_PAGE_TITLE = STEPS[3]


def _wipe_configure_widgets() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith(CONFIGURE_WIDGET_PREFIX):
            st.session_state.pop(key, None)


def _init(key: str, value: Any) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _eta_percent(value: float) -> float:
    return float(value) * 100.0


def _eta_fraction(percent: float) -> float:
    return float(percent) / 100.0


def render_configure(state: dict[str, Any]) -> None:
    demo = is_saved_example(state)
    with app_shell(
        current_step=4,
        max_available=max(int(state.get("max_step") or 1), 4),
        width="form",
        demo=demo,
        mode=analysis_mode_or_none(state),
        state=state,
    ):
        stale = configure_entry_reason(state, demo=demo)
        if stale:
            render_page_header("Step 4 of 6", _PAGE_TITLE)
            render_status_panel(
                "danger",
                "Selected period is no longer valid",
                "Return to Simulation period and confirm the selected period.",
            )
            events = render_action_row(
                back="Back",
                primary="Continue",
                primary_disabled=True,
                disabled_reason=REASON_STALE,
                key="v2-configure-actions",
            )
            if events.back:
                back_to_step3(state)
                st.rerun()
            return
        defaults_ok = True
        saved_ok = True
        if demo:
            context = load_saved_configure_context()
            if not context.get("ok"):
                saved_ok = False
                render_page_header("Step 4 of 6", _PAGE_TITLE)
                render_status_panel(
                    "danger",
                    "Saved example unavailable",
                    "The saved demo is not available.",
                )
                events = render_action_row(
                    back="Back",
                    primary="Continue",
                    primary_disabled=True,
                    disabled_reason=REASON_DEMO,
                    key="v2-configure-actions",
                )
                if events.back:
                    back_to_step3(state)
                    st.rerun()
                return
            ensure_demo_configure(
                state,
                context,
                price_covered=prices_cover_period(state.get("price_coverage")),
            )
        else:
            defaults = load_defaults_snapshot()
            defaults_ok = bool(defaults.get("ok"))
            if not defaults_ok:
                render_page_header("Step 4 of 6", _PAGE_TITLE)
                render_status_panel(
                    "danger",
                    "Central defaults unavailable",
                    REASON_DEFAULTS,
                )
                events = render_action_row(
                    back="Back",
                    primary="Continue",
                    primary_disabled=True,
                    disabled_reason=REASON_DEFAULTS,
                    key="v2-configure-actions",
                )
                if events.back:
                    back_to_step3(state)
                    st.rerun()
                return
            ensure_configure_initialized(
                state,
                defaults,
                price_covered=prices_cover_period(state.get("price_coverage")),
            )
        render_page_header("Step 4 of 6", _PAGE_TITLE, CONFIGURE_LEAD)
        with st.container(key="v2-configure"):
            _render_body(state, demo=demo, defaults_ok=defaults_ok, saved_ok=saved_ok)


def _render_body(
    state: dict[str, Any],
    *,
    demo: bool,
    defaults_ok: bool,
    saved_ok: bool,
) -> None:
    selected = str(state.get("analysis_mode") or MODE_ONE)
    chosen = render_choice_cards(
        MODE_CARD_OPTIONS,
        selected=selected,
        key="v2-analysis-mode",
        disabled=False,
    )
    if chosen != selected:
        _wipe_configure_widgets()
        apply_analysis_mode(state, chosen)
        st.rerun()
    _absorb_widget_state(state)
    if demo:
        st.caption(DEMO_READONLY)
    covered = prices_cover_period(state.get("price_coverage"))
    if selected == MODE_ONE and not covered:
        render_status_panel("warning", PRICE_UNAVAILABLE_TITLE, PRICE_UNAVAILABLE_BODY)
    configure = state["configure"]
    if selected == MODE_SIZE and not demo:
        site = (state.get("period_inspection") or {}).get("site_analysis")
        seed_manual_range_from_site(configure["sizing"], site)
        resolved = resolve_live_candidates(state)
        if configure.get("candidates") != resolved:
            apply_configure_fields(state, candidates=resolved)
            configure = state["configure"]
    if selected == MODE_ONE:
        _render_one_battery(state, disabled=demo)
    else:
        _render_sizing(state, disabled=demo)
    if not demo:
        restore = st.button(
            _RESTORE_LABEL,
            type="secondary",
            width="content",
            help=RESTORE_HELP,
            key="v2-configure-restore",
        )
        if restore:
            defaults = load_defaults_snapshot()
            if defaults.get("ok"):
                _wipe_configure_widgets()
                restore_recommended_defaults(state, defaults)
                st.rerun()
            render_status_panel("danger", "Central defaults unavailable", REASON_DEFAULTS)
    reason = continue_reason(state, demo=demo, defaults_ok=defaults_ok, saved_ok=saved_ok)
    events = render_action_row(
        back="Back",
        primary="Continue",
        primary_disabled=reason is not None,
        disabled_reason=reason,
        key="v2-configure-actions",
    )
    if events.back:
        back_to_step3(state)
        st.rerun()
    if events.primary and reason is None:
        store_frozen_snapshot(state)
        continue_to_step5(state)
        st.rerun()


def _absorb_widget_state(state: dict[str, Any]) -> None:
    shared: dict[str, Any] = {}
    one_battery: dict[str, Any] = {}
    sizing: dict[str, Any] = {}
    session = st.session_state
    if "v2_cfg_usable_kwh" in session:
        one_battery["usable_kwh"] = float(session["v2_cfg_usable_kwh"])
    if "v2_cfg_power_kw" in session:
        one_battery["power_kw"] = float(session["v2_cfg_power_kw"])
    if "v2_cfg_split" in session:
        configure = state.get("configure") or {}
        one = dict((configure or {}).get("one_battery") or {})
        one.update(one_battery)
        one_battery.update(apply_split_power(one, bool(session["v2_cfg_split"])))
    if "v2_cfg_charge_kw" in session and one_battery.get("split_power"):
        one_battery["charge_kw"] = float(session["v2_cfg_charge_kw"])
    if "v2_cfg_discharge_kw" in session and one_battery.get("split_power"):
        one_battery["discharge_kw"] = float(session["v2_cfg_discharge_kw"])
    if "v2_cfg_cost" in session:
        shared["cost_eur_per_kwh"] = float(session["v2_cfg_cost"])
    if "v2_cfg_sale" in session:
        shared["customer_sale_eur_per_mwh"] = float(session["v2_cfg_sale"])
    if "v2_cfg_peak_export" in session:
        shared["peak_export_eur_per_mwh"] = float(session["v2_cfg_peak_export"])
    if "v2_cfg_offpeak_export" in session:
        shared["offpeak_export_eur_per_mwh"] = float(session["v2_cfg_offpeak_export"])
    if "v2_cfg_eta_charge" in session:
        shared["eta_charge"] = _eta_fraction(session["v2_cfg_eta_charge"])
    if "v2_cfg_eta_discharge" in session:
        shared["eta_discharge"] = _eta_fraction(session["v2_cfg_eta_discharge"])
    if "v2_cfg_efc" in session:
        shared["max_efc_per_year"] = float(session["v2_cfg_efc"])
    if "v2_cfg_peak_start" in session and isinstance(session["v2_cfg_peak_start"], dt_time):
        shared["peak_start_local"] = format_hhmm(session["v2_cfg_peak_start"])
    if "v2_cfg_peak_end" in session and isinstance(session["v2_cfg_peak_end"], dt_time):
        shared["peak_end_local"] = format_hhmm(session["v2_cfg_peak_end"])
    if "v2_cfg_weekends" in session:
        shared["weekends_offpeak"] = bool(session["v2_cfg_weekends"])
    if "v2_cfg_seasonal" in session:
        shared["seasonal_plots"] = bool(session["v2_cfg_seasonal"])
    if "v2_cfg_duration_1h" in session:
        sizing["duration_1h"] = bool(session["v2_cfg_duration_1h"])
    if "v2_cfg_duration_2h" in session:
        sizing["duration_2h"] = bool(session["v2_cfg_duration_2h"])
    if "v2_cfg_duration_4h" in session:
        sizing["duration_4h"] = bool(session["v2_cfg_duration_4h"])
    if "v2_cfg_duration_6h" in session:
        sizing["duration_6h"] = bool(session["v2_cfg_duration_6h"])
    if "v2_cfg_custom_hours" in session:
        sizing["custom_hours_text"] = str(session["v2_cfg_custom_hours"])
    if "v2_cfg_power_mode" in session:
        label = session["v2_cfg_power_mode"]
        if label in POWER_LABEL_TO_MODE:
            sizing["power_mode"] = POWER_LABEL_TO_MODE[label]
    if "v2_cfg_min_power" in session:
        sizing["min_power_kw"] = float(session["v2_cfg_min_power"])
    if "v2_cfg_max_power" in session:
        sizing["max_power_kw"] = float(session["v2_cfg_max_power"])
    if "v2_cfg_power_step" in session:
        sizing["power_increment_kw"] = float(session["v2_cfg_power_step"])
    if "v2_cfg_explicit" in session:
        sizing["explicit_text"] = str(session["v2_cfg_explicit"])
    if "v2_cfg_years" in session:
        sizing["evaluation_years"] = float(session["v2_cfg_years"])
    if "v2_cfg_capture" in session:
        sizing["capture_pct"] = float(session["v2_cfg_capture"])
    if shared or one_battery or sizing:
        apply_configure_fields(
            state,
            shared=shared or None,
            one_battery=one_battery or None,
            sizing=sizing or None,
        )


def _render_one_battery(state: dict[str, Any], *, disabled: bool) -> None:
    configure = state["configure"]
    shared = dict(configure["shared"])
    one = dict(configure["one_battery"])
    st.markdown("**Battery**")
    row = st.columns(3)
    with row[0]:
        _init("v2_cfg_usable_kwh", float(one["usable_kwh"]))
        usable = st.number_input(
            "Usable battery capacity (kWh)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_usable_kwh",
            disabled=disabled,
        )
    with row[1]:
        if not one.get("split_power"):
            _init("v2_cfg_power_kw", float(one["power_kw"]))
            power = st.number_input(
                "Battery power (kW)",
                min_value=0.0,
                step=1.0,
                key="v2_cfg_power_kw",
                disabled=disabled,
            )
        else:
            power = float(one["power_kw"])
            st.text_input("Battery power (kW)", value="Set separately below", disabled=True)
    with row[2]:
        st.text_input(
            "Round-trip efficiency",
            value=format_percent(round_trip_percent(shared)),
            disabled=True,
            key="v2_cfg_roundtrip_display",
        )
    st.markdown("**Cost assumptions**")
    cost_row = st.columns(2)
    with cost_row[0]:
        _init("v2_cfg_cost", float(shared["cost_eur_per_kwh"]))
        cost = st.number_input(
            "Estimated battery cost (EUR/kWh usable capacity)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_cost",
            disabled=disabled,
        )
    with cost_row[1]:
        preview = dict(one)
        preview["usable_kwh"] = float(usable)
        shared_preview = dict(shared)
        shared_preview["cost_eur_per_kwh"] = float(cost)
        st.text_input(
            "Estimated battery CAPEX",
            value=format_eur(estimated_capex_eur(preview, shared_preview)),
            disabled=True,
            key="v2_cfg_capex_display",
        )
    st.caption(CAPEX_CAPTION)
    st.markdown("**Tariffs**")
    tariff_row = st.columns(3)
    with tariff_row[0]:
        _init("v2_cfg_sale", float(shared["customer_sale_eur_per_mwh"]))
        sale = st.number_input(
            "Customer PV-sale tariff (EUR/MWh)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_sale",
            disabled=disabled,
        )
    with tariff_row[1]:
        _init("v2_cfg_peak_export", float(shared["peak_export_eur_per_mwh"]))
        peak_export = st.number_input(
            "Peak injection tariff (EUR/MWh)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_peak_export",
            disabled=disabled,
        )
    with tariff_row[2]:
        _init("v2_cfg_offpeak_export", float(shared["offpeak_export_eur_per_mwh"]))
        offpeak = st.number_input(
            "Off-peak injection tariff (EUR/MWh)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_offpeak_export",
            disabled=disabled,
        )
    shared_patch, one_patch = _render_one_battery_advanced(shared, one, disabled=disabled)
    shared_patch.update(
        {
            "cost_eur_per_kwh": float(cost),
            "customer_sale_eur_per_mwh": float(sale),
            "peak_export_eur_per_mwh": float(peak_export),
            "offpeak_export_eur_per_mwh": float(offpeak),
        }
    )
    one_patch["usable_kwh"] = float(usable)
    if not one.get("split_power"):
        one_patch["power_kw"] = float(power)
    apply_configure_fields(state, shared=shared_patch, one_battery=one_patch)


def _render_one_battery_advanced(
    shared: dict[str, Any],
    one: dict[str, Any],
    *,
    disabled: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    shared_patch: dict[str, Any] = {}
    one_patch: dict[str, Any] = {}
    with st.expander("Advanced settings"):
        _init("v2_cfg_split", bool(one.get("split_power")))
        split = st.checkbox(
            "Set charge and discharge power separately",
            key="v2_cfg_split",
            disabled=disabled,
        )
        one_patch = apply_split_power(one, bool(split))
        if one_patch.get("split_power"):
            cols = st.columns(2)
            with cols[0]:
                _init("v2_cfg_charge_kw", float(one_patch["charge_kw"]))
                one_patch["charge_kw"] = float(
                    st.number_input(
                        "Charge power (kW)",
                        min_value=0.0,
                        step=1.0,
                        key="v2_cfg_charge_kw",
                        disabled=disabled,
                    )
                )
            with cols[1]:
                _init("v2_cfg_discharge_kw", float(one_patch["discharge_kw"]))
                one_patch["discharge_kw"] = float(
                    st.number_input(
                        "Discharge power (kW)",
                        min_value=0.0,
                        step=1.0,
                        key="v2_cfg_discharge_kw",
                        disabled=disabled,
                    )
                )
        eta_cols = st.columns(2)
        with eta_cols[0]:
            _init("v2_cfg_eta_charge", _eta_percent(shared["eta_charge"]))
            shared_patch["eta_charge"] = _eta_fraction(
                st.number_input(
                    "Charge efficiency (%)",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.1,
                    key="v2_cfg_eta_charge",
                    disabled=disabled,
                )
            )
        with eta_cols[1]:
            _init("v2_cfg_eta_discharge", _eta_percent(shared["eta_discharge"]))
            shared_patch["eta_discharge"] = _eta_fraction(
                st.number_input(
                    "Discharge efficiency (%)",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.1,
                    key="v2_cfg_eta_discharge",
                    disabled=disabled,
                )
            )
        _init("v2_cfg_efc", float(shared["max_efc_per_year"]))
        shared_patch["max_efc_per_year"] = float(
            st.number_input(
                "Maximum equivalent full cycles per year",
                min_value=0.0,
                step=1.0,
                key="v2_cfg_efc",
                disabled=disabled,
            )
        )
        st.text_input("Initial stored energy", value="0 kWh", disabled=True, key="v2_cfg_soc_display")
        time_cols = st.columns(2)
        with time_cols[0]:
            _init("v2_cfg_peak_start", parse_hhmm(str(shared["peak_start_local"])))
            start = st.time_input(
                "Peak-period start (Europe/Brussels)",
                key="v2_cfg_peak_start",
                disabled=disabled,
            )
            if isinstance(start, dt_time):
                shared_patch["peak_start_local"] = format_hhmm(start)
        with time_cols[1]:
            _init("v2_cfg_peak_end", parse_hhmm(str(shared["peak_end_local"])))
            end = st.time_input(
                "Peak-period end (Europe/Brussels)",
                key="v2_cfg_peak_end",
                disabled=disabled,
            )
            if isinstance(end, dt_time):
                shared_patch["peak_end_local"] = format_hhmm(end)
        _init("v2_cfg_weekends", bool(shared["weekends_offpeak"]))
        shared_patch["weekends_offpeak"] = bool(
            st.checkbox("Treat weekends as off-peak", key="v2_cfg_weekends", disabled=disabled)
        )
        _init("v2_cfg_seasonal", bool(shared["seasonal_plots"]))
        shared_patch["seasonal_plots"] = bool(
            st.checkbox("Generate seasonal plots", key="v2_cfg_seasonal", disabled=disabled)
        )
    return shared_patch, one_patch


def _render_sizing(state: dict[str, Any], *, disabled: bool) -> None:
    configure = state["configure"]
    shared = dict(configure["shared"])
    sizing = dict(configure["sizing"])
    candidates = dict(configure.get("candidates") or {})
    if period_is_partial(state):
        render_status_panel("warning", "Partial simulation period", PARTIAL_PERIOD_NOTE)
    st.markdown("**Battery durations**")
    main = st.columns(2)
    with main[0]:
        _init("v2_cfg_duration_2h", bool(sizing.get("duration_2h")))
        duration_2h = st.checkbox("2 hours", key="v2_cfg_duration_2h", disabled=disabled)
    with main[1]:
        _init("v2_cfg_duration_4h", bool(sizing.get("duration_4h")))
        duration_4h = st.checkbox("4 hours", key="v2_cfg_duration_4h", disabled=disabled)
    st.markdown("**Power range**")
    current_label = next(
        label for label, mode in POWER_LABEL_TO_MODE.items() if mode == sizing.get("power_mode")
    )
    _init("v2_cfg_power_mode", current_label)
    chosen_label = st.radio(
        "Power range",
        list(POWER_MODE_LABELS),
        key="v2_cfg_power_mode",
        disabled=disabled,
        label_visibility="collapsed",
    )
    power_mode = POWER_LABEL_TO_MODE[str(chosen_label)]
    if power_mode == POWER_SUGGESTED:
        _render_suggested_summary(candidates)
        if candidates.get("suggested_blocked"):
            render_status_panel(
                "warning",
                "Suggested sizes are not available",
                str(candidates.get("suggested_message") or "Choose a manual or explicit power range."),
            )
    elif power_mode == POWER_MANUAL:
        cols = st.columns(3)
        with cols[0]:
            _init("v2_cfg_min_power", float(sizing.get("min_power_kw") or 0.0))
            min_power = st.number_input(
                "Minimum power (kW)",
                min_value=0.0,
                step=1.0,
                key="v2_cfg_min_power",
                disabled=disabled,
            )
        with cols[1]:
            _init("v2_cfg_max_power", float(sizing.get("max_power_kw") or 0.0))
            max_power = st.number_input(
                "Maximum power (kW)",
                min_value=0.0,
                step=1.0,
                key="v2_cfg_max_power",
                disabled=disabled,
            )
        with cols[2]:
            _init("v2_cfg_power_step", float(sizing.get("power_increment_kw") or 0.0))
            increment = st.number_input(
                "Power increment (kW)",
                min_value=0.0,
                step=1.0,
                key="v2_cfg_power_step",
                disabled=disabled,
            )
    else:
        _init("v2_cfg_explicit", str(sizing.get("explicit_text") or ""))
        explicit_text = st.text_area(
            "Battery sizes (power kW, usable energy kWh)",
            key="v2_cfg_explicit",
            disabled=disabled,
            help=EXPLICIT_HELP,
        )
    if candidates.get("ok") and candidates.get("items"):
        _render_candidate_list(candidates)
    elif power_mode != POWER_SUGGESTED and candidates.get("error"):
        render_status_panel("danger", "Candidate list could not be built", str(candidates["error"]))
    st.markdown("**Cost assumptions**")
    cost_row = st.columns(2)
    with cost_row[0]:
        _init("v2_cfg_cost", float(shared["cost_eur_per_kwh"]))
        cost = st.number_input(
            "Estimated battery cost (EUR/kWh usable capacity)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_cost",
            disabled=disabled,
        )
    with cost_row[1]:
        _init("v2_cfg_years", float(sizing["evaluation_years"]))
        years = st.number_input(
            "Evaluation period (years)",
            min_value=0.0,
            step=1.0,
            key="v2_cfg_years",
            disabled=disabled,
        )
    st.caption(SWEEP_COST_CAPTION)
    st.markdown("**Revenue assumptions**")
    st.caption(
        f"Customer PV-sale {shared['customer_sale_eur_per_mwh']:g} EUR/MWh · "
        f"Peak injection {shared['peak_export_eur_per_mwh']:g} EUR/MWh · "
        f"Off-peak injection {shared['offpeak_export_eur_per_mwh']:g} EUR/MWh"
    )
    shared_patch: dict[str, Any] = {"cost_eur_per_kwh": float(cost)}
    with st.expander("Change revenue assumptions"):
        cols = st.columns(3)
        with cols[0]:
            _init("v2_cfg_sale", float(shared["customer_sale_eur_per_mwh"]))
            shared_patch["customer_sale_eur_per_mwh"] = float(
                st.number_input(
                    "Customer PV-sale tariff (EUR/MWh)",
                    min_value=0.0,
                    step=1.0,
                    key="v2_cfg_sale",
                    disabled=disabled,
                )
            )
        with cols[1]:
            _init("v2_cfg_peak_export", float(shared["peak_export_eur_per_mwh"]))
            shared_patch["peak_export_eur_per_mwh"] = float(
                st.number_input(
                    "Peak injection tariff (EUR/MWh)",
                    min_value=0.0,
                    step=1.0,
                    key="v2_cfg_peak_export",
                    disabled=disabled,
                )
            )
        with cols[2]:
            _init("v2_cfg_offpeak_export", float(shared["offpeak_export_eur_per_mwh"]))
            shared_patch["offpeak_export_eur_per_mwh"] = float(
                st.number_input(
                    "Off-peak injection tariff (EUR/MWh)",
                    min_value=0.0,
                    step=1.0,
                    key="v2_cfg_offpeak_export",
                    disabled=disabled,
                )
            )
        time_cols = st.columns(2)
        with time_cols[0]:
            _init("v2_cfg_peak_start", parse_hhmm(str(shared["peak_start_local"])))
            start = st.time_input(
                "Peak-period start (Europe/Brussels)",
                key="v2_cfg_peak_start",
                disabled=disabled,
            )
            if isinstance(start, dt_time):
                shared_patch["peak_start_local"] = format_hhmm(start)
        with time_cols[1]:
            _init("v2_cfg_peak_end", parse_hhmm(str(shared["peak_end_local"])))
            end = st.time_input(
                "Peak-period end (Europe/Brussels)",
                key="v2_cfg_peak_end",
                disabled=disabled,
            )
            if isinstance(end, dt_time):
                shared_patch["peak_end_local"] = format_hhmm(end)
        _init("v2_cfg_weekends", bool(shared["weekends_offpeak"]))
        shared_patch["weekends_offpeak"] = bool(
            st.checkbox("Treat weekends as off-peak", key="v2_cfg_weekends", disabled=disabled)
        )
    sizing_patch = {
        "duration_2h": bool(duration_2h),
        "duration_4h": bool(duration_4h),
        "power_mode": power_mode,
        "evaluation_years": float(years),
    }
    if power_mode == POWER_MANUAL:
        sizing_patch["min_power_kw"] = float(min_power)
        sizing_patch["max_power_kw"] = float(max_power)
        sizing_patch["power_increment_kw"] = float(increment)
    if power_mode == POWER_EXPLICIT:
        sizing_patch["explicit_text"] = str(explicit_text)
    shared_adv, sizing_adv = _render_sizing_advanced(shared, sizing, disabled=disabled)
    shared_patch.update(shared_adv)
    sizing_patch.update(sizing_adv)
    apply_configure_fields(state, shared=shared_patch, sizing=sizing_patch)


def _render_sizing_advanced(
    shared: dict[str, Any],
    sizing: dict[str, Any],
    *,
    disabled: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    shared_patch: dict[str, Any] = {}
    sizing_patch: dict[str, Any] = {}
    with st.expander("Advanced settings"):
        cols = st.columns(2)
        with cols[0]:
            _init("v2_cfg_duration_1h", bool(sizing.get("duration_1h")))
            sizing_patch["duration_1h"] = bool(
                st.checkbox("1 hour", key="v2_cfg_duration_1h", disabled=disabled)
            )
        with cols[1]:
            _init("v2_cfg_duration_6h", bool(sizing.get("duration_6h")))
            sizing_patch["duration_6h"] = bool(
                st.checkbox("6 hours", key="v2_cfg_duration_6h", disabled=disabled)
            )
        _init("v2_cfg_custom_hours", str(sizing.get("custom_hours_text") or ""))
        sizing_patch["custom_hours_text"] = str(
            st.text_input(
                "Additional durations (hours, comma-separated)",
                key="v2_cfg_custom_hours",
                disabled=disabled,
            )
        )
        _init("v2_cfg_capture", float(sizing["capture_pct"]))
        sizing_patch["capture_pct"] = float(
            st.number_input(
                "Revenue-capture threshold (%)",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                key="v2_cfg_capture",
                disabled=disabled,
            )
        )
        eta_cols = st.columns(2)
        with eta_cols[0]:
            _init("v2_cfg_eta_charge", _eta_percent(shared["eta_charge"]))
            shared_patch["eta_charge"] = _eta_fraction(
                st.number_input(
                    "Charge efficiency (%)",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.1,
                    key="v2_cfg_eta_charge",
                    disabled=disabled,
                )
            )
        with eta_cols[1]:
            _init("v2_cfg_eta_discharge", _eta_percent(shared["eta_discharge"]))
            shared_patch["eta_discharge"] = _eta_fraction(
                st.number_input(
                    "Discharge efficiency (%)",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.1,
                    key="v2_cfg_eta_discharge",
                    disabled=disabled,
                )
            )
        _init("v2_cfg_efc", float(shared["max_efc_per_year"]))
        shared_patch["max_efc_per_year"] = float(
            st.number_input(
                "Maximum equivalent full cycles per year",
                min_value=0.0,
                step=1.0,
                key="v2_cfg_efc",
                disabled=disabled,
            )
        )
        _init("v2_cfg_seasonal", bool(shared["seasonal_plots"]))
        shared_patch["seasonal_plots"] = bool(
            st.checkbox("Generate seasonal plots", key="v2_cfg_seasonal", disabled=disabled)
        )
    return shared_patch, sizing_patch


def _render_suggested_summary(candidates: dict[str, Any]) -> None:
    grid = candidates.get("power_range_kw") or []
    rangetext = format_power_range(grid)
    if rangetext:
        st.caption(f"Suggested power range: {rangetext}")
    import_kw = candidates.get("p995_import_kw")
    surplus_kw = candidates.get("p995_surplus_kw")
    if import_kw is not None and surplus_kw is not None:
        st.caption(
            f"Based on 99.5th-percentile import {import_kw:g} kW and surplus {surplus_kw:g} kW."
        )


def _render_candidate_list(candidates: dict[str, Any]) -> None:
    items = list(candidates.get("items") or [])
    st.caption(f"{len(items)} battery sizes will be tested.")
    rows = [
        {
            "Candidate": str(item.get("candidate_id") or ""),
            "Power (kW)": item.get("power_kw"),
            "Usable energy (kWh)": item.get("usable_energy_kwh"),
            "Duration (h)": item.get("duration_hours"),
        }
        for item in items
    ]
    with st.expander(f"Resolved candidate list ({len(items)})"):
        render_display_table(rows)
        duplicates = candidates.get("removed_duplicates") or []
        if duplicates:
            st.caption(f"Removed {len(duplicates)} duplicate size(s).")
