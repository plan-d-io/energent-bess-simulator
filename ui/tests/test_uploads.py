from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ui.services import uploads
from ui.services.uploads import (
    blocking_panels,
    clear_inspect_cache,
    file_signature,
    inspect_fluvius_payloads,
    live_role_rows,
    project_ingest_result,
    safe_basename,
    snapshot_is_ready,
    unique_basename,
)


def _issue(code: str, **details: object) -> SimpleNamespace:
    payload = {
        "severity": "fatal",
        "code": code,
        "message": f"{code} occurred",
        "details": details,
    }
    return SimpleNamespace(to_dict=lambda: dict(payload))


def _ok_result(paths: list[Path]) -> SimpleNamespace:
    warning = SimpleNamespace(
        to_dict=lambda: {
            "severity": "warning",
            "code": "UNUSED_REGISTERS",
            "message": "unused",
        }
    )
    return SimpleNamespace(
        ok=True,
        roles={
            "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 10, "ean": "1"},
            "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 11},
            "pv": {"register": "Productie Actief", "unit": "kWh", "n_rows": 12},
        },
        sources=[{"path": str(paths[0]), "n_rows": 10, "registers": ["Afname Actief"]}],
        issues=SimpleNamespace(items=[warning], ok=True),
        periods=[SimpleNamespace(to_dict=lambda: {"id": "2024", "label": "Calendar year 2024"})],
        dst={"n_spring_skipped_wall_clock": 0},
        usable="DATAFRAME-MUST-NOT-LEAK",
    )


def test_signature_changes_with_bytes_not_name_or_size() -> None:
    first = file_signature((("meter.csv", b"abc"),))
    second = file_signature((("meter.csv", b"xyz"),))
    assert first != second
    assert first[0][0] == second[0][0] == "meter.csv"
    assert first[0][1] == second[0][1] == 3


def test_unsafe_names_become_basenames() -> None:
    assert safe_basename("../secret/offtake.csv") == "offtake.csv"
    assert safe_basename(r"C:\exports\injectie.csv") == "injectie.csv"
    assert safe_basename("") == "upload.csv"


def test_duplicate_basenames_are_deterministic() -> None:
    used: set[str] = set()
    assert unique_basename("a.csv", used) == "a.csv"
    assert unique_basename("other/a.csv", used) == "a__2.csv"
    recorded: list[str] = []

    def ingest(paths: list[Path]) -> SimpleNamespace:
        recorded.extend(path.name for path in paths)
        return _ok_result(paths)

    inspect_fluvius_payloads(
        (("dir/a.csv", b"aa"), ("x/a.csv", b"bb"), ("c.csv", b"cc")),
        ingest=ingest,
    )
    assert recorded == ["a.csv", "a__2.csv", "c.csv"]


def test_wrong_count_does_not_call_core() -> None:
    calls: list[object] = []

    def ingest(paths: list[Path]) -> SimpleNamespace:
        calls.append(paths)
        return _ok_result(paths)

    assert inspect_fluvius_payloads((("a.csv", b"a"), ("b.csv", b"b")), ingest=ingest) is None
    assert calls == []


def test_projection_keeps_roles_and_issues_without_dataframe() -> None:
    snapshot = inspect_fluvius_payloads(
        (("a.csv", b"1"), ("b.csv", b"2"), ("c.csv", b"3")),
        ingest=_ok_result,
    )
    assert snapshot is not None
    assert snapshot["ok"] is True
    assert snapshot_is_ready(snapshot)
    assert "usable" not in snapshot
    dumped = json.dumps(snapshot)
    assert "DATAFRAME-MUST-NOT-LEAK" not in dumped
    assert "btm_v2_upload_" not in dumped
    assert snapshot["sources"][0]["path"] == "a.csv"
    assert snapshot["roles"]["pv"]["n_rows"] == 12
    assert snapshot["issues"][0]["code"] == "UNUSED_REGISTERS"
    rows = live_role_rows(snapshot)
    assert [row["Role"] for row in rows] == ["Offtake", "Injection", "PV production"]
    assert rows[0]["Rows"] == "10"


def test_fatal_and_missing_role_remain_blocked() -> None:
    def ingest(paths: list[Path]) -> SimpleNamespace:
        return SimpleNamespace(
            ok=False,
            roles={
                "offtake": {"register": "Afname Actief", "unit": "kWh", "n_rows": 1},
                "injection": {"register": "Injectie Actief", "unit": "kWh", "n_rows": 1},
            },
            sources=[{"path": str(paths[0])}],
            issues=SimpleNamespace(
                items=[_issue("MISSING_REGISTER", role="pv", register="Productie Actief")]
            ),
            periods=[],
            dst={},
            usable=None,
        )

    snapshot = inspect_fluvius_payloads(
        (("a.csv", b"1"), ("b.csv", b"2"), ("c.csv", b"3")),
        ingest=ingest,
    )
    assert snapshot is not None
    assert snapshot_is_ready(snapshot) is False
    panels = blocking_panels(snapshot)
    assert panels[0][0] == "The PV production role is missing"
    titles = [title for title, _body in panels]
    assert titles.count("The PV production role is missing") == 1


def test_repeated_payloads_use_cache(monkeypatch: object) -> None:
    calls = {"n": 0}

    def ingest(paths: list[Path]) -> SimpleNamespace:
        calls["n"] += 1
        return _ok_result(paths)

    monkeypatch.setattr(uploads, "ingest_fluvius", ingest)
    clear_inspect_cache()
    payloads = (("a.csv", b"1"), ("b.csv", b"2"), ("c.csv", b"3"))
    first = inspect_fluvius_payloads(payloads)
    second = inspect_fluvius_payloads(payloads)
    assert first == second
    assert calls["n"] == 1
    clear_inspect_cache()


def test_adapter_failure_is_structured() -> None:
    def ingest(_paths: list[Path]) -> SimpleNamespace:
        raise RuntimeError("boom")

    snapshot = inspect_fluvius_payloads(
        (("a.csv", b"1"), ("b.csv", b"2"), ("c.csv", b"3")),
        ingest=ingest,
    )
    assert snapshot is not None
    assert snapshot["ok"] is False
    assert snapshot["error"]["code"] == "ADAPTER_FAILURE"
    assert snapshot["error"]["exception_type"] == "RuntimeError"
    assert "boom" not in blocking_panels(snapshot)[0][1]


def test_project_ingest_result_sanitises_issue_paths() -> None:
    result = SimpleNamespace(
        ok=False,
        roles={},
        sources=[{"path": "/tmp/btm_v2_upload_xx/secret.csv"}],
        issues=SimpleNamespace(
            items=[
                _issue(
                    "UNREADABLE_FILE",
                    path="/tmp/btm_v2_upload_xx/secret.csv",
                )
            ]
        ),
        periods=[],
        dst={},
    )
    snapshot = project_ingest_result(result)
    assert snapshot["sources"][0]["path"] == "secret.csv"
    assert snapshot["issues"][0]["details"]["path"] == "secret.csv"
    assert "btm_v2_upload_xx" not in json.dumps(snapshot)
