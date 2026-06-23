"""Tests for the workspace profile detector.

musubi-tier: substrate test — covers the per-workspace context
inference that the skill router (MVP item 6 / Track D.3) reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace.detector import detect_profile, format_profile_md


# ── Detection — single-language workspaces ──────────────────────────────────


@pytest.fixture
def py_workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.11"\n'
        "[tool.setuptools]\n",
        encoding="utf-8",
    )
    (tmp_path / "x.py").write_text("print(1)\n", encoding="utf-8")
    return tmp_path


def test_python_workspace_detected(py_workspace: Path) -> None:
    profile = detect_profile(py_workspace)
    assert profile["language"] == "python"
    assert profile["conventions"]["python_version"] == ">=3.11"
    assert "pip" in profile["package_managers"]
    assert any("pyproject.toml" in s for s in profile["signals"])


def test_python_workspace_with_pytest_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n', encoding="utf-8"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["test_framework"] == "pytest"


def test_node_workspace_with_typescript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "engines": {"node": ">=18"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["language"] == "typescript"
    assert profile["conventions"]["node_engine"] == ">=18"
    assert "npm" in profile["package_managers"]


def test_node_workspace_without_tsconfig_is_javascript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["language"] == "javascript"


def test_node_workspace_with_jest_config(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (tmp_path / "jest.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["test_framework"] == "jest"


def test_rust_workspace(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    profile = detect_profile(tmp_path)
    assert profile["language"] == "rust"
    assert "cargo" in profile["package_managers"]


def test_go_workspace(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module x\ngo 1.21\n", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["language"] == "go"


def test_empty_workspace_is_unknown(tmp_path: Path) -> None:
    profile = detect_profile(tmp_path)
    assert profile["language"] == "unknown"
    assert profile["secondary_languages"] == []


# ── Monorepo detection (root + one level deep) ──────────────────────────────


def test_monorepo_python_plus_typescript(tmp_path: Path) -> None:
    """Mirrors this very project's structure (musubi +
    copilot-harness-extension). Both languages should surface; the one
    with more manifests wins primary."""
    py_sub = tmp_path / "musubi"
    ts_sub = tmp_path / "copilot-harness-extension"
    py_sub.mkdir()
    ts_sub.mkdir()
    (py_sub / "pyproject.toml").write_text(
        '[project]\nname = "ch"\nrequires-python = ">=3.11"\n', encoding="utf-8"
    )
    (ts_sub / "package.json").write_text(
        json.dumps({"name": "che", "engines": {"vscode": "^1.93.0"}}),
        encoding="utf-8",
    )
    (ts_sub / "tsconfig.json").write_text("{}", encoding="utf-8")
    profile = detect_profile(tmp_path)
    # Both languages must be represented somewhere.
    all_langs = {profile["language"], *profile["secondary_languages"]}
    assert "python" in all_langs
    assert "typescript" in all_langs
    assert profile["conventions"].get("vscode_engine") == "^1.93.0"
    assert "pip" in profile["package_managers"]
    assert "npm" in profile["package_managers"]


# ── Doc tool detection ──────────────────────────────────────────────────────


def test_sphinx_doc_tool_detected(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "conf.py").write_text("project = 'x'\n", encoding="utf-8")
    (docs / "_static").mkdir()
    (docs / "index.rst").write_text("Welcome\n", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["doc_tool"] == "sphinx"


def test_mkdocs_doc_tool_detected(tmp_path: Path) -> None:
    (tmp_path / "mkdocs.yml").write_text("site_name: x\n", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["doc_tool"] == "mkdocs"


def test_mdbook_doc_tool_detected(tmp_path: Path) -> None:
    (tmp_path / "book.toml").write_text('[book]\ntitle = "x"\n', encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile["doc_tool"] == "mdbook"


# ── Serialisation ──────────────────────────────────────────────────────────


def test_profile_is_json_serialisable(py_workspace: Path) -> None:
    """The profile dict must be safe to round-trip through json.dumps —
    no Path objects, no datetime objects. Failure would break the
    eventual `/profile` MCP-tool surface."""
    profile = detect_profile(py_workspace)
    encoded = json.dumps(profile)
    decoded = json.loads(encoded)
    assert decoded["language"] == "python"


def test_format_profile_md_emits_frontmatter(py_workspace: Path) -> None:
    profile = detect_profile(py_workspace)
    md = format_profile_md(profile)
    assert md.startswith("---\n")
    assert "language: python" in md
    assert "# Project profile" in md
    assert "## Detection signals" in md


def test_format_profile_md_handles_empty_signals(tmp_path: Path) -> None:
    profile = detect_profile(tmp_path)
    md = format_profile_md(profile)
    # When no signals were found, the body says so.
    assert "No manifest signals detected" in md
    assert "language: unknown" in md


# ── Skip directories (huge repos must not stall detection) ─────────────────


def test_file_type_distribution_skips_node_modules(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    # Drop a bunch of files that should NOT pollute the distribution.
    for i in range(20):
        (nm / f"vendor{i}.js").write_text("", encoding="utf-8")
    (tmp_path / "main.ts").write_text("", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    profile = detect_profile(tmp_path)
    # .ts should dominate over the (skipped) vendor .js files.
    assert ".ts" in profile["file_types_present"]
