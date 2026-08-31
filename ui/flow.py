"""V2-owned flow and session state. No Streamlit widgets or worker handles."""

from __future__ import annotations

from typing import Any, Literal, Mapping, MutableMapping

DataRoute = Literal["live", "saved"]

STATE_VERSION = 1
SESSION_KEY = "v2_flow"
SITE_WIDGET_KEY = "v2_site_name_input"
DEMO_CHECKBOX_KEY = "v2_demo_mode"
UPLOAD_WIDGET_PREFIX = "v2_fluvius_uploads_"

ROUTE_LIVE: DataRoute = "live"
ROUTE_SAVED: DataRoute = "saved"

UPLOAD_ORIGIN_BROWSER = "browser"
UPLOAD_ORIGIN_TRANSFER = "transfer"

SAVED_SITE_NAME = "Demo site"

PERIOD_SELECT_KEY = "v2_period_select"
UNVALIDATED_ACK_WIDGET_KEY = "v2_ack_unvalidated"
SITE_BOUNDARY_ACK_WIDGET_KEY = "v2_ack_site_boundary"
CONFIGURE_WIDGET_PREFIX = "v2_cfg_"
REVIEW_WIDGET_PREFIX = "v2_review_"

# Future step keys. Cleared by the central downstream reset when present.
_PERIOD_OPTIONAL_KEYS = (
    "period_id",
    "unvalidated_ack",
    "site_boundary_ack",
    "period_inspection",
    "period_inspection_key",
    "price_coverage",
    "price_coverage_key",
)
_STEP5_PLUS_KEYS = (
    "review",
    "job",
    "results",
    "launch_error",
)
_STEP4_PLUS_KEYS = (
    "analysis_mode",
    "configure",
) + _STEP5_PLUS_KEYS
_DOWNSTREAM_OPTIONAL_KEYS = _PERIOD_OPTIONAL_KEYS + _STEP4_PLUS_KEYS
_ANALYSIS_MODES = frozenset({"one-battery", "size"})


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "step": 1,
        "max_step": 1,
        "data_route": ROUTE_LIVE,
        "site_name": "",
        "upload_generation": 0,
        "upload_origin": UPLOAD_ORIGIN_BROWSER,
        "upload_signature": (),
        "upload_payloads": (),
        "ingest_snapshot": None,
        "upload_messages": [],
        "data_ready": False,
        "inspecting": False,
        "adapter_error": None,
    }


def state_is_compatible(state: Mapping[str, Any] | None) -> bool:
    return isinstance(state, dict) and state.get("version") == STATE_VERSION


def upload_widget_key(generation: int) -> str:
    return f"{UPLOAD_WIDGET_PREFIX}{int(generation)}"


def upload_origin_of(state: Mapping[str, Any]) -> str:
    origin = state.get("upload_origin")
    if origin == UPLOAD_ORIGIN_TRANSFER:
        return UPLOAD_ORIGIN_TRANSFER
    return UPLOAD_ORIGIN_BROWSER


def transferred_uploads_hold(state: Mapping[str, Any], widget_file_count: int) -> bool:
    """Empty native uploader must not delete payloads installed by Demo transfer."""
    if upload_origin_of(state) != UPLOAD_ORIGIN_TRANSFER:
        return False
    if state.get("data_route") != ROUTE_LIVE:
        return False
    if int(widget_file_count) != 0:
        return False
    if len(tuple(state.get("upload_payloads") or ())) != 3:
        return False
    return bool(state.get("data_ready"))


def reset_downstream(state: MutableMapping[str, Any]) -> dict[str, Any]:
    """Clear Step 2 onward and stale upload inspection. Keep route, site and widget generation."""
    state["step"] = 1
    state["max_step"] = 1
    state["upload_origin"] = UPLOAD_ORIGIN_BROWSER
    state["upload_signature"] = ()
    state["upload_payloads"] = ()
    state["ingest_snapshot"] = None
    state["upload_messages"] = []
    state["data_ready"] = False
    state["inspecting"] = False
    state["adapter_error"] = None
    for key in _DOWNSTREAM_OPTIONAL_KEYS:
        state.pop(key, None)
    return dict(state)


def apply_route_change(state: MutableMapping[str, Any], route: DataRoute) -> dict[str, Any]:
    previous = state.get("data_route")
    if route not in {ROUTE_LIVE, ROUTE_SAVED}:
        raise ValueError(f"unknown data route: {route!r}")
    if previous == route:
        return dict(state)
    reset_downstream(state)
    state["data_route"] = route
    state["site_name"] = ""
    state["upload_generation"] = int(state.get("upload_generation") or 0) + 1
    if route == ROUTE_SAVED:
        state["data_ready"] = False
    return dict(state)


def clear_route_change_widget_keys(session: MutableMapping[str, Any]) -> None:
    """Drop route-owned widgets. Keep the Demo checkbox; the caller just set it."""
    session.pop(SITE_WIDGET_KEY, None)
    session.pop(PERIOD_SELECT_KEY, None)
    session.pop(UNVALIDATED_ACK_WIDGET_KEY, None)
    session.pop(SITE_BOUNDARY_ACK_WIDGET_KEY, None)
    for key in list(session.keys()):
        text = str(key)
        if (
            text.startswith(UPLOAD_WIDGET_PREFIX)
            or text.startswith(CONFIGURE_WIDGET_PREFIX)
            or text.startswith(REVIEW_WIDGET_PREFIX)
        ):
            session.pop(key, None)


def apply_site_name(state: MutableMapping[str, Any], name: str) -> dict[str, Any]:
    """Update the live site label. Does not re-ingest or reset downstream state."""
    state["site_name"] = str(name)
    return dict(state)


def apply_upload_change(
    state: MutableMapping[str, Any],
    *,
    signature: tuple[Any, ...],
    payloads: tuple[tuple[str, bytes], ...],
) -> dict[str, Any]:
    stored = tuple(state.get("upload_signature") or ())
    if stored == tuple(signature):
        return dict(state)
    site_name = str(state.get("site_name") or "")
    generation = int(state.get("upload_generation") or 0)
    route = state.get("data_route") or ROUTE_LIVE
    reset_downstream(state)
    state["data_route"] = route
    state["site_name"] = site_name
    state["upload_generation"] = generation
    state["upload_origin"] = UPLOAD_ORIGIN_BROWSER
    state["upload_signature"] = tuple(signature)
    state["upload_payloads"] = tuple(payloads)
    if len(payloads) == 3:
        state["inspecting"] = True
    return dict(state)


def apply_widget_upload_change(
    state: MutableMapping[str, Any],
    *,
    signature: tuple[Any, ...],
    payloads: tuple[tuple[str, bytes], ...],
) -> dict[str, Any]:
    """Apply native uploader values. An empty widget does not delete transferred payloads."""
    if transferred_uploads_hold(state, len(payloads)):
        return dict(state)
    return apply_upload_change(state, signature=signature, payloads=payloads)


def store_inspection(
    state: MutableMapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    *,
    ready: bool,
    messages: list[str] | None = None,
) -> dict[str, Any]:
    state["inspecting"] = False
    state["ingest_snapshot"] = None if snapshot is None else dict(snapshot)
    state["data_ready"] = bool(ready)
    state["upload_messages"] = list(messages or [])
    error = None if snapshot is None else snapshot.get("error")
    state["adapter_error"] = error
    return dict(state)


def navigate_to_step(state: MutableMapping[str, Any], target_step: Any) -> bool:
    """Move to an unlocked stage. Does not clear uploads, snapshot or max_step."""
    from ui.services.job import job_locks_navigation

    try:
        target = int(target_step)
    except (TypeError, ValueError):
        return False
    if job_locks_navigation(state):
        return False
    max_step = int(state.get("max_step") or 1)
    current = int(state.get("step") or 1)
    if target == current:
        return False
    if target < 1 or target > max_step:
        return False
    state["step"] = target
    return True


def continue_to_step2(state: MutableMapping[str, Any]) -> dict[str, Any]:
    if not state.get("data_ready"):
        return dict(state)
    if state.get("data_route") != ROUTE_SAVED and not str(state.get("site_name") or "").strip():
        return dict(state)
    state["step"] = 2
    state["max_step"] = max(int(state.get("max_step") or 1), 2)
    return dict(state)


def continue_to_step3(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 3
    state["max_step"] = max(int(state.get("max_step") or 1), 3)
    return dict(state)


def continue_to_step4(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 4
    state["max_step"] = max(int(state.get("max_step") or 1), 4)
    return dict(state)


def continue_to_step5(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 5
    state["max_step"] = max(int(state.get("max_step") or 1), 5)
    return dict(state)


def continue_to_step6(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 6
    state["max_step"] = max(int(state.get("max_step") or 1), 6)
    return dict(state)


def back_to_step1(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 1
    return dict(state)


def back_to_step2(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 2
    return dict(state)


def back_to_step3(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 3
    return dict(state)


def back_to_step4(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["step"] = 4
    return dict(state)


def clear_step5_plus(state: MutableMapping[str, Any]) -> dict[str, Any]:
    """Clear Review/job/results and the frozen Configure snapshot. Keep branch values."""
    for key in _STEP5_PLUS_KEYS:
        state.pop(key, None)
    configure = state.get("configure")
    if isinstance(configure, dict):
        configure["snapshot"] = None
    if int(state.get("max_step") or 1) > 4:
        state["max_step"] = 4
    if int(state.get("step") or 1) > 4:
        state["step"] = 4
    return dict(state)


def apply_analysis_mode(state: MutableMapping[str, Any], mode: str) -> dict[str, Any]:
    """Switch analysis mode. Preserves both Configure branches and shared assumptions."""
    if mode not in _ANALYSIS_MODES:
        raise ValueError(f"unknown analysis mode: {mode!r}")
    if state.get("analysis_mode") == mode:
        return dict(state)
    state["analysis_mode"] = mode
    return clear_step5_plus(state)


def _clear_period_derived(state: MutableMapping[str, Any]) -> None:
    state["unvalidated_ack"] = False
    state["site_boundary_ack"] = False
    for key in (
        "period_inspection",
        "period_inspection_key",
        "price_coverage",
        "price_coverage_key",
    ):
        state.pop(key, None)
    for key in _STEP4_PLUS_KEYS:
        state.pop(key, None)
    if int(state.get("max_step") or 1) > 3:
        state["max_step"] = 3
    if int(state.get("step") or 1) > 3:
        state["step"] = 3


def apply_period_change(state: MutableMapping[str, Any], period_id: str) -> dict[str, Any]:
    """Store the selected period id. A real change resets acknowledgements and Step 4+."""
    new_id = str(period_id)
    previous = state.get("period_id")
    if previous is not None and str(previous) == new_id:
        return dict(state)
    if previous is not None:
        _clear_period_derived(state)
    else:
        state["unvalidated_ack"] = False
        state["site_boundary_ack"] = False
    state["period_id"] = new_id
    return dict(state)


def apply_unvalidated_ack(state: MutableMapping[str, Any], checked: bool) -> dict[str, Any]:
    value = bool(checked)
    if bool(state.get("unvalidated_ack")) == value:
        return dict(state)
    state["unvalidated_ack"] = value
    for key in ("period_inspection", "period_inspection_key", "price_coverage", "price_coverage_key"):
        state.pop(key, None)
    for key in _STEP4_PLUS_KEYS:
        state.pop(key, None)
    if int(state.get("max_step") or 1) > 3:
        state["max_step"] = 3
    return dict(state)


def apply_site_boundary_ack(state: MutableMapping[str, Any], checked: bool) -> dict[str, Any]:
    value = bool(checked)
    if bool(state.get("site_boundary_ack")) == value:
        return dict(state)
    state["site_boundary_ack"] = value
    for key in ("period_inspection", "period_inspection_key", "price_coverage", "price_coverage_key"):
        state.pop(key, None)
    for key in _STEP4_PLUS_KEYS:
        state.pop(key, None)
    if int(state.get("max_step") or 1) > 3:
        state["max_step"] = 3
    return dict(state)


def store_period_inspection(
    state: MutableMapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    *,
    cache_key: Any = None,
) -> dict[str, Any]:
    state["period_inspection"] = None if snapshot is None else dict(snapshot)
    state["period_inspection_key"] = cache_key
    return dict(state)


def store_price_coverage(
    state: MutableMapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    *,
    cache_key: Any = None,
) -> dict[str, Any]:
    state["price_coverage"] = None if snapshot is None else dict(snapshot)
    state["price_coverage_key"] = cache_key
    return dict(state)


def store_saved_period_context(
    state: MutableMapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the read-only Demo period record from verified artifacts."""
    state["period_id"] = str(context.get("period_id") or "")
    state["unvalidated_ack"] = bool(context.get("unvalidated_ack"))
    state["site_boundary_ack"] = bool(context.get("site_boundary_ack"))
    inspection = context.get("period_inspection")
    state["period_inspection"] = None if inspection is None else dict(inspection)
    state["period_inspection_key"] = context.get("period_inspection_key")
    coverage = context.get("price_coverage")
    state["price_coverage"] = None if coverage is None else dict(coverage)
    state["price_coverage_key"] = context.get("price_coverage_key")
    return dict(state)


def site_name_is_present(state: Mapping[str, Any]) -> bool:
    if state.get("data_route") == ROUTE_SAVED:
        return True
    return bool(str(state.get("site_name") or "").strip())


def displayed_site_name(state: Mapping[str, Any]) -> str:
    if state.get("data_route") == ROUTE_SAVED:
        return SAVED_SITE_NAME
    return str(state.get("site_name") or "")


def is_saved_example(state: Mapping[str, Any]) -> bool:
    return state.get("data_route") == ROUTE_SAVED


def analysis_mode_or_none(state: Mapping[str, Any]) -> str | None:
    mode = state.get("analysis_mode")
    if mode in _ANALYSIS_MODES:
        return str(mode)
    return None
