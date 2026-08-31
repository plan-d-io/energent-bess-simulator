from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_uses_dynamic_version_from_core_file() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert payload["project"].get("version") is None
    assert payload["project"]["dynamic"] == ["version"]
    dynamic = payload["tool"]["setuptools"]["dynamic"]["version"]
    assert dynamic["file"] == "src/btm_sim/VERSION"
    assert payload["tool"]["setuptools"]["package-data"]["btm_sim"] == ["VERSION"]


def test_pyproject_declares_author_and_project_urls() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert payload["project"]["authors"] == [{"name": "Joannes Laveyne"}]
    assert payload["project"]["urls"]["Developer"] == "https://www.plan-d.io/"
    assert payload["project"]["urls"]["Client"] == "https://energent.be/"


def test_readme_links_to_local_authors() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "AUTHORS.md" in readme
    assert (ROOT / "AUTHORS.md").is_file()
    authors = (ROOT / "AUTHORS.md").read_text(encoding="utf-8")
    assert "https://www.plan-d.io/" in authors
    assert "https://energent.be/" in authors
    assert "Joannes Laveyne" in authors
    ui_readme = (ROOT / "ui" / "README.md").read_text(encoding="utf-8")
    assert "AUTHORS.md" in ui_readme
    assert "../AUTHORS.md" in ui_readme


def test_pyproject_declares_ui_optional_dependencies() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = payload["project"]["optional-dependencies"]
    assert "streamlit>=1.61" in extras["ui"]
    assert "altair>=5.5" in extras["ui"]
    assert "plotly>=5.24" in extras["ui"]
    assert "pillow>=11" in extras["ui"]
    assert extras["dev"] == ["pytest>=8"]
