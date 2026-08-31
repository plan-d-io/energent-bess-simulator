from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ui.services.period_inspection import (
    clear_period_inspect_cache,
    inspect_period_payloads,
    period_inspection_cache_key,
)


def setup_function() -> None:
    clear_period_inspect_cache()


def _payloads() -> tuple[tuple[str, bytes], ...]:
    return (("offtake.csv", b"aaa"), ("injection.csv", b"bbb"), ("pv.csv", b"ccc"))


def _ok_result(paths: list[Path], period_id: str, **flags: object) -> SimpleNamespace:
    return SimpleNamespace(
        to_dict=lambda: {
            "ok": True,
            "requires_site_boundary_acknowledgement": False,
            "period_id": period_id,
            "selected_period": {"id": period_id},
            "fatal": [],
            "warnings": [],
            "report": {"sources": [{"path": str(paths[0])}]},
            "site_analysis": {"n_intervals": 4, "durations_hours": [2.0, 4.0]},
            "automatic_candidates": [],
        }
    )


def test_wrong_file_count_does_not_call_inspector() -> None:
    called = {"n": 0}

    def inspector(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("inspector must not run")

    snapshot = inspect_period_payloads(
        (("a.csv", b"a"), ("b.csv", b"b")),
        "2024",
        inspector=inspector,
    )
    assert called["n"] == 0
    assert snapshot["ok"] is False
    assert snapshot["error"]["code"] == "INSPECTION_FAILED"


def test_stages_three_safe_temp_files_and_strips_paths() -> None:
    recorded: dict[str, object] = {}

    def inspector(paths, period_id, **kwargs):
        recorded["names"] = [path.name for path in paths]
        recorded["parent"] = str(paths[0].parent)
        recorded["period_id"] = period_id
        recorded["kwargs"] = kwargs
        for path in paths:
            assert path.is_file()
            assert "btm_v2_upload_" in str(path)
        return _ok_result(paths, period_id, **kwargs)

    snapshot = inspect_period_payloads(
        (("dir/offtake.csv", b"a"), ("x/offtake.csv", b"b"), ("pv.csv", b"c")),
        "2024",
        inspector=inspector,
        acknowledge_site_boundary=False,
        allow_unvalidated=True,
    )
    assert recorded["names"] == ["offtake.csv", "offtake__2.csv", "pv.csv"]
    assert recorded["period_id"] == "2024"
    assert recorded["kwargs"]["acknowledge_site_boundary"] is False
    assert recorded["kwargs"]["allow_unvalidated"] is True
    assert recorded["kwargs"]["durations_hours"] == (2.0, 4.0)
    assert snapshot["ok"] is True
    assert snapshot["report"]["sources"][0]["path"] == "offtake.csv"
    json.dumps(snapshot)
    assert "frame" not in snapshot
    dumped = json.dumps(snapshot)
    assert "btm_v2_upload_" not in dumped
    assert ":\\" not in dumped


def test_adapter_exceptions_become_structured_failures() -> None:
    def inspector(*_args, **_kwargs):
        raise RuntimeError("boom")

    snapshot = inspect_period_payloads(_payloads(), "2024", inspector=inspector)
    assert snapshot["ok"] is False
    assert snapshot["error"]["exception_type"] == "RuntimeError"
    assert snapshot["site_analysis"] is None
    json.dumps(snapshot)


def test_cache_key_distinguishes_signature_period_policy_and_version() -> None:
    signature = (("offtake.csv", 3, "aaa"), ("injection.csv", 3, "bbb"), ("pv.csv", 3, "ccc"))
    base = period_inspection_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
    )
    assert base != period_inspection_cache_key(
        (("offtake.csv", 3, "zzz"), ("injection.csv", 3, "bbb"), ("pv.csv", 3, "ccc")),
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
    )
    assert base != period_inspection_cache_key(
        signature,
        "2025",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
    )
    assert base != period_inspection_cache_key(
        signature,
        "2024",
        allow_unvalidated=False,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
    )
    assert base != period_inspection_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=True,
        simulator_version="0.1.0",
    )
    assert base != period_inspection_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="9.9.9",
    )
    assert base != period_inspection_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
        durations_hours=(1.0, 6.0),
    )


def test_cached_inspect_reuses_unacknowledged_and_adds_one_acked_call(monkeypatch) -> None:
    calls: list[bool] = []

    def inspector(paths, period_id, **kwargs):
        calls.append(bool(kwargs.get("acknowledge_site_boundary")))
        payload = _ok_result(paths, period_id).to_dict()
        acked = bool(kwargs["acknowledge_site_boundary"])
        payload["requires_site_boundary_acknowledgement"] = not acked
        payload["ok"] = acked
        if acked:
            payload["warnings"] = [
                {
                    "code": "NEGATIVE_LOAD",
                    "details": {"acknowledged_site_boundary": True, "count": 2},
                }
            ]
            payload["fatal"] = []
        else:
            payload["site_analysis"] = None
            payload["fatal"] = [{"code": "NEGATIVE_LOAD", "details": {"count": 2}}]
        return SimpleNamespace(to_dict=lambda: payload)

    from ui.services import period_inspection as module

    monkeypatch.setattr(module, "inspect_selected_period", inspector)
    signature = (("a.csv", 1, "a"), ("b.csv", 1, "b"), ("c.csv", 1, "c"))
    discovery = module.inspect_period_payloads(
        _payloads(),
        "2024",
        acknowledge_site_boundary=False,
        signature=signature,
    )
    again = module.inspect_period_payloads(
        _payloads(),
        "2024",
        acknowledge_site_boundary=False,
        signature=signature,
    )
    acked = module.inspect_period_payloads(
        _payloads(),
        "2024",
        acknowledge_site_boundary=True,
        signature=signature,
    )
    unchecked = module.inspect_period_payloads(
        _payloads(),
        "2024",
        acknowledge_site_boundary=False,
        signature=signature,
    )
    assert calls == [False, True]
    assert discovery["requires_site_boundary_acknowledgement"] is True
    assert again == discovery
    assert acked["ok"] is True
    assert unchecked == discovery
