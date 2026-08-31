from __future__ import annotations

import re
from pathlib import Path

from btm_sim import __version__ as SIMULATOR_VERSION

from ui.presentation.shell import UI_VERSION as SHELL_UI_VERSION
from ui.presentation.shell import SIMULATOR_VERSION as SHELL_SIMULATOR_VERSION
from ui.presentation.shell import frontend_version_label, identity_html, simulator_version_label
from ui.version import UI_VERSION, read_ui_version

ROOT = Path(__file__).resolve().parents[2]
UI_VERSION_FILE = ROOT / "ui" / "VERSION"
UI_READER = ROOT / "ui" / "version.py"
_LITERAL_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def test_ui_version_has_a_single_file_source() -> None:
    assert UI_VERSION == UI_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert UI_VERSION == read_ui_version()
    assert SHELL_UI_VERSION is UI_VERSION
    assert _VERSION_RE.fullmatch(UI_VERSION)


def test_simulator_version_comes_from_public_core() -> None:
    assert SIMULATOR_VERSION == "0.2.0"
    assert SHELL_SIMULATOR_VERSION == SIMULATOR_VERSION


def test_shell_shows_two_separate_version_lines() -> None:
    markup = identity_html(demo=False, mode=None)
    sim = simulator_version_label()
    front = frontend_version_label()
    assert sim == f"Simulator {SIMULATOR_VERSION}"
    assert front == f"Front-end {UI_VERSION}"
    assert sim == f"Simulator {SIMULATOR_VERSION}"
    assert front == f"Front-end {UI_VERSION}"
    assert sim in markup
    assert front in markup
    assert markup.index(sim) < markup.index(front)
    assert markup.count('class="v2-identity-version"') == 2


def test_ui_version_reader_has_no_fallback_literal() -> None:
    text = UI_READER.read_text(encoding="utf-8")
    assert _LITERAL_RE.search(text) is None


def test_product_code_does_not_parse_project_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "pyproject.toml" in text or "configs/defaults.toml" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
