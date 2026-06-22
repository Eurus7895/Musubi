"""Detect a project profile from workspace manifest files.

harness-tier: substrate
expires-when: never — per-workspace context is durable regardless of
  whether the pipeline shape dissolves later (Track D.1).

MVP item 4 / Track D.1. Single public function `detect_profile()`
scans the workspace root (and one level deep, for monorepos) for
common manifest files (`pyproject.toml`, `package.json`, `Cargo.toml`,
…) and returns a dict capturing language, secondary languages,
package managers, test framework, doc tool, and version conventions.

The result is consumed by:

  - The skill router (Track D.3, item 6) — intersects skill
    `applies-to:` declarations against this profile to filter the
    catalog so the model never sees skills that don't fit the workspace.
  - Future `/profile` command (Track D.7) for manual inspection
    and override.

Detection is deliberately conservative: when a signal is ambiguous,
secondary fields are populated instead of overwriting the primary.
When NO signals match (truly empty workspace), `language` is set to
`"unknown"` so downstream consumers know to fall through. No I/O
outside reading manifest files — no LLM calls, no network, no shelling
out. Pure Python plus stdlib + tomllib + PyYAML (already a harness dep).
"""

from __future__ import annotations

import datetime as _dt
import os
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover — codebase requires 3.11
    tomllib = None  # type: ignore[assignment]

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — harness deps include PyYAML
    yaml = None  # type: ignore[assignment]


# ── Public API ──────────────────────────────────────────────────────────────


def detect_profile(workspace_root: Path) -> dict[str, Any]:
    """Return a project profile describing the workspace's stack.

    Looks at `workspace_root` AND one level of immediate subdirectories
    so monorepos (Python harness + TS extension is the canonical example)
    surface both languages.

    Shape of the returned dict:

        {
            "detected_at": "2026-06-13T05:42:01Z",
            "detection_method": "pyproject.toml + package.json",
            "language": "python",
            "secondary_languages": ["typescript"],
            "package_managers": ["pip", "npm"],
            "test_framework": "pytest" | None,
            "doc_tool": "sphinx" | None,
            "file_types_present": [".py", ".ts", ".md", ".toml", ".json"],
            "conventions": {
                "encoding": "utf-8",
                "python_version": ">=3.11",
                ...
            },
            "signals": [
                "pyproject.toml at copilot-harness/ → python (requires-python >=3.11)",
                "package.json at copilot-harness-extension/ → typescript",
                ...
            ],
        }

    Best-effort: a malformed manifest causes that signal to be skipped,
    not the whole detection to fail.
    """
    workspace_root = workspace_root.resolve()
    signals: list[str] = []
    # Map language → set of evidence strings, used to score the primary.
    lang_evidence: dict[str, set[str]] = {}
    pkg_managers: set[str] = set()
    secondary: set[str] = set()
    test_framework: str | None = None
    doc_tool: str | None = None
    conventions: dict[str, Any] = {"encoding": "utf-8"}
    detection_method_parts: list[str] = []

    # Scan root + immediate subdirs.
    scan_dirs = [workspace_root]
    for entry in sorted(workspace_root.iterdir()) if workspace_root.is_dir() else []:
        if entry.is_dir() and not entry.name.startswith("."):
            scan_dirs.append(entry)

    for scan_dir in scan_dirs:
        rel = _rel(scan_dir, workspace_root)
        # Python — pyproject.toml is the strongest signal.
        py_proj = scan_dir / "pyproject.toml"
        if py_proj.is_file():
            detection_method_parts.append("pyproject.toml")
            lang_evidence.setdefault("python", set()).add(str(py_proj))
            parsed = _read_toml(py_proj)
            requires_py = (
                parsed.get("project", {}).get("requires-python")
                if parsed
                else None
            )
            if requires_py:
                conventions["python_version"] = requires_py
            # Package manager hints — default to pip; poetry / setuptools
            # win when their tool block is present.
            tool = parsed.get("tool", {}) if parsed else {}
            if "poetry" in tool:
                pkg_managers.add("poetry")
            else:
                pkg_managers.add("pip")
            # pytest inference.
            if "pytest" in tool:
                test_framework = test_framework or "pytest"
            signals.append(
                f"`{rel}/pyproject.toml` → python"
                + (f" (`requires-python {requires_py}`)" if requires_py else "")
            )

        # Lower-confidence Python signals.
        if (scan_dir / "requirements.txt").is_file():
            lang_evidence.setdefault("python", set()).add(
                str(scan_dir / "requirements.txt")
            )
            pkg_managers.add("pip")
            signals.append(f"`{rel}/requirements.txt` → python (pip)")
        if (scan_dir / "setup.py").is_file():
            lang_evidence.setdefault("python", set()).add(str(scan_dir / "setup.py"))
            pkg_managers.add("pip")
            signals.append(f"`{rel}/setup.py` → python (legacy setuptools)")

        # Node / JS / TS — package.json + tsconfig.json.
        pkg_json = scan_dir / "package.json"
        if pkg_json.is_file():
            detection_method_parts.append("package.json")
            has_tsconfig = (scan_dir / "tsconfig.json").is_file()
            lang_key = "typescript" if has_tsconfig else "javascript"
            lang_evidence.setdefault(lang_key, set()).add(str(pkg_json))
            pkg_managers.add("npm")
            parsed_json = _read_json(pkg_json)
            engines = parsed_json.get("engines", {}) if parsed_json else {}
            node_engine = engines.get("node")
            if node_engine:
                conventions["node_engine"] = node_engine
            vscode_engine = engines.get("vscode")
            if vscode_engine:
                conventions["vscode_engine"] = vscode_engine
            if parsed_json and "jest" in parsed_json:
                test_framework = test_framework or "jest"
            signals.append(
                f"`{rel}/package.json` → {lang_key}"
                + (f" (node `{node_engine}`)" if node_engine else "")
            )

        # Rust.
        if (scan_dir / "Cargo.toml").is_file():
            detection_method_parts.append("Cargo.toml")
            lang_evidence.setdefault("rust", set()).add(str(scan_dir / "Cargo.toml"))
            pkg_managers.add("cargo")
            signals.append(f"`{rel}/Cargo.toml` → rust")

        # Go.
        if (scan_dir / "go.mod").is_file():
            detection_method_parts.append("go.mod")
            lang_evidence.setdefault("go", set()).add(str(scan_dir / "go.mod"))
            pkg_managers.add("go modules")
            signals.append(f"`{rel}/go.mod` → go")

        # Java / Kotlin.
        if (scan_dir / "pom.xml").is_file():
            detection_method_parts.append("pom.xml")
            lang_evidence.setdefault("java", set()).add(str(scan_dir / "pom.xml"))
            pkg_managers.add("maven")
            signals.append(f"`{rel}/pom.xml` → java (maven)")
        if (scan_dir / "build.gradle").is_file() or (
            scan_dir / "build.gradle.kts"
        ).is_file():
            detection_method_parts.append("build.gradle")
            lang_evidence.setdefault("java", set()).add(str(scan_dir / "build.gradle"))
            pkg_managers.add("gradle")
            signals.append(f"`{rel}/build.gradle` → java / kotlin (gradle)")

        # Ruby.
        if (scan_dir / "Gemfile").is_file():
            detection_method_parts.append("Gemfile")
            lang_evidence.setdefault("ruby", set()).add(str(scan_dir / "Gemfile"))
            pkg_managers.add("bundler")
            signals.append(f"`{rel}/Gemfile` → ruby (bundler)")

        # PHP.
        if (scan_dir / "composer.json").is_file():
            detection_method_parts.append("composer.json")
            lang_evidence.setdefault("php", set()).add(
                str(scan_dir / "composer.json")
            )
            pkg_managers.add("composer")
            signals.append(f"`{rel}/composer.json` → php")

        # Doc tooling.
        if (scan_dir / "conf.py").is_file() and (
            (scan_dir / "_static").is_dir()
            or any(scan_dir.glob("*.rst"))
        ):
            doc_tool = doc_tool or "sphinx"
            signals.append(f"`{rel}/conf.py` + `.rst` → sphinx")
        if (scan_dir / "mkdocs.yml").is_file():
            doc_tool = doc_tool or "mkdocs"
            signals.append(f"`{rel}/mkdocs.yml` → mkdocs")
        if (scan_dir / "book.toml").is_file():
            doc_tool = doc_tool or "mdbook"
            signals.append(f"`{rel}/book.toml` → mdbook")

        # pytest configs.
        if (scan_dir / "pytest.ini").is_file() or (
            scan_dir / "conftest.py"
        ).is_file():
            test_framework = test_framework or "pytest"
            signals.append(f"`{rel}/pytest.ini|conftest.py` → pytest")
        # jest configs.
        if (scan_dir / "jest.config.js").is_file() or (
            scan_dir / "jest.config.ts"
        ).is_file():
            test_framework = test_framework or "jest"
            signals.append(f"`{rel}/jest.config.*` → jest")

    # Decide primary language by manifest-count.
    if lang_evidence:
        # Tie-break: more pieces of evidence wins.
        primary = max(lang_evidence.items(), key=lambda kv: len(kv[1]))[0]
        for other in lang_evidence:
            if other != primary:
                secondary.add(other)
        language: str = primary
    else:
        language = "unknown"

    # File-type distribution (top extensions). Cap walk for safety.
    file_types_present = _file_type_distribution(workspace_root, cap=2000)

    return {
        "detected_at": _utcnow_iso(),
        "detection_method": " + ".join(
            dict.fromkeys(detection_method_parts)  # dedup, preserve order
        )
        or "no manifest detected",
        "language": language,
        "secondary_languages": sorted(secondary),
        "package_managers": sorted(pkg_managers),
        "test_framework": test_framework,
        "doc_tool": doc_tool,
        "file_types_present": file_types_present,
        "conventions": conventions,
        "signals": signals,
    }


# ── Private helpers ─────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    """ISO-8601 timestamp without microseconds + Z suffix.

    Plain `datetime.utcnow()` is deprecated in 3.12+; use the timezone-
    aware form and strip the offset to keep the format compact."""
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _rel(p: Path, root: Path) -> str:
    """Workspace-relative path string; '.' for the root itself."""
    try:
        rel = p.resolve().relative_to(root)
        s = str(rel)
        return s if s != "." else "."
    except ValueError:
        return str(p)


def _read_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f) or {}
    except Exception:
        return {}


def _read_json(path: Path) -> dict[str, Any]:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _file_type_distribution(root: Path, cap: int) -> list[str]:
    """Return up to top-8 file extensions present under `root`, sorted by
    frequency. Skips dotfiles + common build/vendor dirs. Walks up to
    `cap` files for safety on huge repos."""
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
        "build", "out", "target", ".harness", ".tox", ".pytest_cache",
        ".mypy_cache", ".ruff_cache",
    }
    counts: Counter[str] = Counter()
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in place.
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".git")]
        for f in filenames:
            if f.startswith("."):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext:
                counts[ext] += 1
            seen += 1
            if seen >= cap:
                break
        if seen >= cap:
            break
    return [ext for ext, _ in counts.most_common(8)]


# ── Markdown formatter ──────────────────────────────────────────────────────


def format_profile_md(profile: dict[str, Any]) -> str:
    """Serialise the profile dict into the tier-2 memory entry shape:
    YAML frontmatter + human-readable Markdown body. Used by
    `scripts/session_start.py` when writing `.github/memory/project-profile.md`.
    """
    if yaml is None:
        # PyYAML isn't installed — write a JSON-shaped fallback body.
        import json

        return (
            "# Project profile\n\n"
            "_PyYAML not available; falling back to JSON for the header._\n\n"
            "```json\n" + json.dumps(profile, indent=2) + "\n```\n"
        )

    fm_keys = (
        "detected_at",
        "detection_method",
        "language",
        "secondary_languages",
        "package_managers",
        "test_framework",
        "doc_tool",
        "file_types_present",
        "conventions",
    )
    frontmatter = {k: profile[k] for k in fm_keys if k in profile}

    # default_flow_style=False = block style. sort_keys=False preserves
    # the explicit order in `fm_keys` so the file is diff-stable.
    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False)

    signals = profile.get("signals") or []
    signal_lines = (
        "\n".join(f"- {s}" for s in signals)
        if signals
        else "_No manifest signals detected; the workspace appears empty or unfamiliar._"
    )

    body = f"""---
{yaml_block}---
# Project profile

Auto-detected at SessionStart by `copilot-harness/workspace/detector.py`
(MVP item 4 / Track D.1). The frontmatter is what the future skill
router (Track D.3) reads to filter the catalog to applicable skills.

Re-run by deleting this file and starting a new session (the harness
regenerates it). A manual `/profile --refresh` slash command will land
with Track D.7.

## Detection signals

{signal_lines}

## How this drives skill applicability

When the skill router (item 6 / Track D.3) ships, it will intersect
each `.github/skills/<name>/SKILL.md` `applies-to:` frontmatter
against the profile above. A C-language skill in a Python workspace,
or a Sphinx-doc skill in an mkdocs project, is hidden from the model's
catalog — preventing the failure pattern of "tried C skill on Python"
that the article (`docs/harness-direction.md` § 3) calls out.
"""
    return body
