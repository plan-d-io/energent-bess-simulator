from __future__ import annotations

import re
from pathlib import Path

from btm_sim import __version__ as SIMULATOR_VERSION
from btm_sim.version import read_package_version

from ui.version import UI_VERSION, read_ui_version

ROOT = Path(__file__).resolve().parents[1]
CORE_VERSION_FILE = ROOT / "src" / "btm_sim" / "VERSION"
UI_VERSION_FILE = ROOT / "ui" / "VERSION"
CORE_READER = ROOT / "src" / "btm_sim" / "version.py"
UI_READER = ROOT / "ui" / "version.py"
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_LITERAL_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def test_public_simulator_version_matches_version_file() -> None:
    assert SIMULATOR_VERSION == CORE_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert SIMULATOR_VERSION == read_package_version()
    assert _VERSION_RE.fullmatch(SIMULATOR_VERSION)


def test_ui_version_does_not_change_simulator_version(tmp_path: Path) -> None:
    projected = tmp_path / "VERSION"
    projected.write_text("9.9.9\n", encoding="utf-8")
    assert read_ui_version(projected) == "9.9.9"
    assert SIMULATOR_VERSION == CORE_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert UI_VERSION == UI_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert UI_VERSION != "9.9.9"


def test_simulator_version_file_is_independent_of_ui_version() -> None:
    assert CORE_VERSION_FILE.resolve() != UI_VERSION_FILE.resolve()
    assert SIMULATOR_VERSION == CORE_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert UI_VERSION == UI_VERSION_FILE.read_text(encoding="utf-8").strip()


def test_core_version_reader_has_no_fallback_literal() -> None:
    text = CORE_READER.read_text(encoding="utf-8")
    assert _LITERAL_RE.search(text) is None


def test_version_files_are_single_nonempty_lines() -> None:
    for path in (CORE_VERSION_FILE, UI_VERSION_FILE):
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        assert len(lines) == 1
        assert _VERSION_RE.fullmatch(lines[0].strip())
