from __future__ import annotations

from ui.flow import (
    ROUTE_LIVE,
    ROUTE_SAVED,
    STATE_VERSION,
    UPLOAD_ORIGIN_BROWSER,
    UPLOAD_ORIGIN_TRANSFER,
    apply_analysis_mode,
    apply_period_change,
    apply_route_change,
    apply_site_boundary_ack,
    apply_site_name,
    apply_unvalidated_ack,
    apply_upload_change,
    apply_widget_upload_change,
    back_to_step1,
    back_to_step3,
    back_to_step4,
    continue_to_step2,
    continue_to_step4,
    continue_to_step5,
    continue_to_step6,
    default_state,
    navigate_to_step,
    reset_downstream,
    state_is_compatible,
    store_inspection,
    store_period_inspection,
    store_price_coverage,
    transferred_uploads_hold,
    upload_origin_of,
)


def test_clean_state_starts_at_step1_live() -> None:
    state = default_state()
    assert state["version"] == STATE_VERSION
    assert state["step"] == 1
    assert state["max_step"] == 1
    assert state["data_route"] == ROUTE_LIVE
    assert state["data_ready"] is False
    assert state["upload_payloads"] == ()
    assert state["upload_origin"] == UPLOAD_ORIGIN_BROWSER
    assert upload_origin_of(state) == UPLOAD_ORIGIN_BROWSER


def test_incompatible_version_is_detected() -> None:
    assert state_is_compatible(default_state())
    assert not state_is_compatible({"version": STATE_VERSION - 1, "step": 2})
    assert not state_is_compatible(None)
    legacy = default_state()
    legacy.pop("upload_origin", None)
    assert state_is_compatible(legacy)
    assert upload_origin_of(legacy) == UPLOAD_ORIGIN_BROWSER


def test_route_change_clears_downstream_and_payloads() -> None:
    state = default_state()
    state["step"] = 2
    state["max_step"] = 2
    state["upload_payloads"] = (("a.csv", b"abc"),)
    state["ingest_snapshot"] = {"ok": True}
    state["data_ready"] = True
    state["period_id"] = "2024"
    state["site_name"] = "Plant A"
    apply_route_change(state, ROUTE_SAVED)
    assert state["data_route"] == ROUTE_SAVED
    assert state["step"] == 1
    assert state["max_step"] == 1
    assert state["upload_payloads"] == ()
    assert state["ingest_snapshot"] is None
    assert state["data_ready"] is False
    assert "period_id" not in state
    assert state["site_name"] == ""
    assert state["upload_origin"] == UPLOAD_ORIGIN_BROWSER
    assert state["upload_generation"] == 1


def test_signature_change_clears_step2() -> None:
    state = default_state()
    state["step"] = 2
    state["max_step"] = 2
    state["site_name"] = "Plant A"
    state["ingest_snapshot"] = {"ok": True}
    apply_upload_change(
        state,
        signature=(("a.csv", 1, "aaa"),),
        payloads=(("a.csv", b"a"),),
    )
    assert state["step"] == 1
    assert state["max_step"] == 1
    assert state["site_name"] == "Plant A"
    assert state["ingest_snapshot"] is None
    apply_upload_change(
        state,
        signature=(("a.csv", 1, "bbb"),),
        payloads=(("a.csv", b"b"),),
    )
    assert state["upload_signature"] == (("a.csv", 1, "bbb"),)


def test_site_label_does_not_reset_inspection() -> None:
    state = default_state()
    state["ingest_snapshot"] = {"ok": True, "roles": {"offtake": {}}}
    state["data_ready"] = True
    state["upload_signature"] = (("a.csv", 3, "abc"),)
    apply_site_name(state, "Harbour")
    assert state["site_name"] == "Harbour"
    assert state["data_ready"] is True
    assert state["ingest_snapshot"]["ok"] is True
    assert state["upload_signature"] == (("a.csv", 3, "abc"),)


def test_continue_requires_ready_route_and_site_name() -> None:
    state = default_state()
    continue_to_step2(state)
    assert state["step"] == 1
    state["data_ready"] = True
    continue_to_step2(state)
    assert state["step"] == 1
    state["site_name"] = "Plant A"
    continue_to_step2(state)
    assert state["step"] == 2
    assert state["max_step"] == 2
    back_to_step1(state)
    assert state["step"] == 1
    assert state["max_step"] == 2
    assert state["data_ready"] is True
    assert state["site_name"] == "Plant A"


def test_saved_route_continue_does_not_require_typed_site_name() -> None:
    state = default_state()
    state["data_route"] = ROUTE_SAVED
    state["data_ready"] = True
    continue_to_step2(state)
    assert state["step"] == 2


def test_continue_to_step3_preserves_snapshot() -> None:
    from ui.flow import back_to_step2, continue_to_step3

    state = default_state()
    state["data_ready"] = True
    state["site_name"] = "Plant A"
    state["ingest_snapshot"] = {"ok": True, "roles": {"offtake": {"register": "Afname Actief"}}}
    continue_to_step2(state)
    continue_to_step3(state)
    assert state["step"] == 3
    assert state["max_step"] == 3
    assert state["ingest_snapshot"]["ok"] is True
    back_to_step2(state)
    assert state["step"] == 2
    assert state["max_step"] == 3


def test_navigate_to_step_rejects_locked_and_invalid_targets() -> None:
    from ui.flow import continue_to_step3

    state = default_state()
    state["data_ready"] = True
    state["site_name"] = "Plant A"
    state["ingest_snapshot"] = {"ok": True}
    continue_to_step2(state)
    continue_to_step3(state)
    snapshot = dict(state["ingest_snapshot"])
    assert navigate_to_step(state, 1) is True
    assert state["step"] == 1
    assert state["max_step"] == 3
    assert state["ingest_snapshot"] == snapshot
    assert state["site_name"] == "Plant A"
    assert navigate_to_step(state, 1) is False
    assert navigate_to_step(state, 3) is True
    assert state["step"] == 3
    assert navigate_to_step(state, 4) is False
    assert navigate_to_step(state, 0) is False
    assert navigate_to_step(state, "x") is False
    assert state["step"] == 3
    assert state["max_step"] == 3


def test_reset_downstream_clears_future_keys() -> None:
    state = default_state()
    state.update(
        {
            "job": {"id": "x"},
            "results": {"dir": "y"},
            "configure": {},
            "review": {},
        }
    )
    reset_downstream(state)
    assert "job" not in state
    assert "results" not in state
    assert "period_id" not in state
    assert "period_inspection" not in state
    assert "price_coverage" not in state
    assert state["step"] == 1


def test_apply_period_change_resets_acks_and_later_state() -> None:
    state = default_state()
    state["data_route"] = ROUTE_LIVE
    state["site_name"] = "Plant A"
    state["upload_signature"] = (("a.csv", 1, "aaa"),)
    state["upload_payloads"] = (("a.csv", b"a"),)
    state["ingest_snapshot"] = {"ok": True, "periods": [{"id": "2024"}, {"id": "2025"}]}
    state["step"] = 4
    state["max_step"] = 4
    state["period_id"] = "2024"
    state["unvalidated_ack"] = True
    state["site_boundary_ack"] = True
    state["period_inspection"] = {"ok": True}
    state["price_coverage"] = {"covered": True}
    state["configure"] = {"mode": "size"}
    apply_period_change(state, "2024")
    assert state["unvalidated_ack"] is True
    assert state["period_inspection"] == {"ok": True}
    apply_period_change(state, "2025")
    assert state["period_id"] == "2025"
    assert state["unvalidated_ack"] is False
    assert state["site_boundary_ack"] is False
    assert "period_inspection" not in state
    assert "price_coverage" not in state
    assert "configure" not in state
    assert state["max_step"] == 3
    assert state["step"] == 3
    assert state["ingest_snapshot"]["ok"] is True
    assert state["upload_signature"] == (("a.csv", 1, "aaa"),)
    assert state["site_name"] == "Plant A"


def test_acks_and_unlocked_navigation_preserve_or_clear_as_specified() -> None:
    from ui.flow import continue_to_step3

    state = default_state()
    state["data_ready"] = True
    state["site_name"] = "Plant A"
    state["ingest_snapshot"] = {"ok": True, "periods": [{"id": "2024"}]}
    continue_to_step2(state)
    continue_to_step3(state)
    apply_period_change(state, "2024")
    apply_unvalidated_ack(state, True)
    store_period_inspection(state, {"ok": True, "period_id": "2024", "site_analysis": {}}, cache_key="k")
    store_price_coverage(state, {"covered": True}, cache_key="p")
    continue_to_step4(state)
    assert state["step"] == 4
    assert state["max_step"] == 4
    assert navigate_to_step(state, 1) is True
    assert state["period_id"] == "2024"
    assert state["unvalidated_ack"] is True
    assert state["price_coverage"]["covered"] is True
    assert navigate_to_step(state, 3) is True
    back_to_step3(state)
    assert state["step"] == 3
    assert state["period_id"] == "2024"
    apply_site_boundary_ack(state, True)
    assert "price_coverage" not in state
    assert state["max_step"] == 3


def test_store_inspection_sets_readiness() -> None:
    state = default_state()
    store_inspection(state, {"ok": True, "roles": {}}, ready=True, messages=["ok"])
    assert state["data_ready"] is True
    assert state["inspecting"] is False
    assert state["upload_messages"] == ["ok"]


def test_analysis_mode_clears_review_but_keeps_configure() -> None:
    state = default_state()
    state["step"] = 5
    state["max_step"] = 5
    state["analysis_mode"] = "one-battery"
    state["configure"] = {"shared": {"cost_eur_per_kwh": 250.0}, "snapshot": {"x": 1}}
    state["review"] = {"ready": True}
    state["period_id"] = "2024"
    apply_analysis_mode(state, "size")
    assert state["analysis_mode"] == "size"
    assert state["configure"]["shared"]["cost_eur_per_kwh"] == 250.0
    assert state["configure"]["snapshot"] is None
    assert "review" not in state
    assert state["max_step"] == 4
    assert state["period_id"] == "2024"


def test_continue_to_step5_and_back_preserve_configure() -> None:
    state = default_state()
    state["configure"] = {"one_battery": {"usable_kwh": 77.0}}
    continue_to_step4(state)
    continue_to_step5(state)
    assert state["step"] == 5
    assert state["max_step"] == 5
    back_to_step4(state)
    assert state["step"] == 4
    assert state["configure"]["one_battery"]["usable_kwh"] == 77.0
    assert navigate_to_step(state, 5) is True
    assert state["step"] == 5
    continue_to_step6(state)
    assert state["step"] == 6
    assert state["max_step"] == 6
    state["job"] = {"job_id": "btm-lock", "launch_state": "launched", "lock_navigation": True}
    assert navigate_to_step(state, 5) is False
    assert state["step"] == 6


def test_empty_widget_does_not_clear_transferred_payloads() -> None:
    state = default_state()
    payloads = (("a.csv", b"aaa"), ("b.csv", b"bbb"), ("c.csv", b"ccc"))
    signature = (("a.csv", 3, "x"), ("b.csv", 3, "y"), ("c.csv", 3, "z"))
    state["upload_origin"] = UPLOAD_ORIGIN_TRANSFER
    state["upload_payloads"] = payloads
    state["upload_signature"] = signature
    state["data_ready"] = True
    state["ingest_snapshot"] = {"ok": True}
    state["site_name"] = "Ganda Cars"
    state["period_id"] = "2024"
    state["step"] = 4
    state["max_step"] = 4
    assert transferred_uploads_hold(state, 0) is True
    apply_widget_upload_change(state, signature=(), payloads=())
    assert state["upload_payloads"] == payloads
    assert state["upload_signature"] == signature
    assert state["period_id"] == "2024"
    assert state["upload_origin"] == UPLOAD_ORIGIN_TRANSFER
    replacement = (("n.csv", b"n"), ("o.csv", b"o"), ("p.csv", b"p"))
    apply_widget_upload_change(
        state,
        signature=(("n.csv", 1, "n"), ("o.csv", 1, "o"), ("p.csv", 1, "p")),
        payloads=replacement,
    )
    assert state["upload_payloads"] == replacement
    assert state["upload_origin"] == UPLOAD_ORIGIN_BROWSER
    assert "period_id" not in state
    assert state["step"] == 1


def test_leaving_saved_route_does_not_keep_site_name() -> None:
    state = default_state()
    state["data_route"] = ROUTE_SAVED
    state["site_name"] = "Ganda Cars"
    state["results"] = {"kind": "sweep"}
    apply_route_change(state, ROUTE_LIVE)
    assert state["data_route"] == ROUTE_LIVE
    assert state["site_name"] == ""
    assert "results" not in state
    assert state["upload_payloads"] == ()
    assert state["upload_origin"] == UPLOAD_ORIGIN_BROWSER
    assert state["data_ready"] is False
