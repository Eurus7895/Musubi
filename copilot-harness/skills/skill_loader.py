"""Skill file I/O — serves SKILL.md and references from .github/skills/.

harness-tier: substrate
expires-when: never — the skill catalog is the substrate the model
  pulls from; this loader is its single read path.

Zero LLM calls. Pure file I/O + YAML parsing.

Public API:
    get_skill(skill_id, skills_dir?) → str | None
    get_reference(skill_id, reference_name, skills_dir?) → str | None
    list_skills(skills_dir?) → list[SkillMeta]
    list_references(skill_id, skills_dir?) → list[str]
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _resolve_skills_dir() -> Path:
    # When running as a bundled extension binary, HARNESS_ROOT is set by the
    # VS Code extension to its own extensionPath (where .github/ is shipped).
    harness_root = os.environ.get("HARNESS_ROOT")
    if harness_root:
        return Path(harness_root) / ".github" / "skills"
    # Development: resolve relative to this file's repo root.
    # __file__ = copilot-harness/skills/skill_loader.py → repo root is 3 up.
    return Path(__file__).parent.parent.parent / ".github" / "skills"


_SKILLS_DIR = _resolve_skills_dir()


@dataclass
class SkillMeta:
    """Catalog entry for one skill.

    `applies_to` is the per-skill applicability declaration (MVP item 5
    / Track D.2). Shape mirrors the project-profile dict from MVP item
    4 / Track D.1 (`workspace/detector.py::detect_profile`) using
    plural keys to encode "any-of" matching:

        applies_to = {
            "languages":        ["python"],   # matches profile.language or profile.secondary_languages
            "test_frameworks":  ["pytest"],   # matches profile.test_framework
            "doc_tools":        ["sphinx"],   # matches profile.doc_tool
            "package_managers": ["pip"],      # matches profile.package_managers
            "file_types":       [".py"],      # matches profile.file_types_present
        }

    All keys are optional. A skill with `applies_to is None` (no
    declaration in its frontmatter) is **universal** — the skill router
    (item 6 / Track D.3) treats it as matching every workspace. A skill
    with `applies_to = {}` is also universal (empty constraint set).
    """

    skill_id: str
    title: str
    path: str
    applies_to: dict[str, list[str]] | None = field(default=None)


_FRONTMATTER_DELIM = "---"


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Return the YAML frontmatter dict, or {} if none / malformed.

    Frontmatter is the optional ``---\\n...\\n---\\n`` block at the top
    of a SKILL.md. Hand-split rather than pull in a Markdown library —
    we only need this one read. Parse errors fall through to {} so a
    malformed SKILL.md still lists in the catalog (just without
    applies_to).
    """
    if not text.startswith(_FRONTMATTER_DELIM):
        return {}
    rest = text[len(_FRONTMATTER_DELIM):].lstrip("\n")
    end = rest.find(f"\n{_FRONTMATTER_DELIM}\n")
    if end == -1:
        # Tolerate the form ending without a trailing newline (EOF).
        end = rest.find(f"\n{_FRONTMATTER_DELIM}")
        if end == -1:
            return {}
    block = rest[:end]
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_applies_to(raw: Any) -> dict[str, list[str]] | None:
    """Normalise the `applies-to:` frontmatter value.

    Accepts: None / missing → None (universal).
    Accepts: dict[str, list] → str-coerced lists.
    Accepts: dict[str, scalar] (shorthand) → wrapped to a 1-element list.

    Anything else returns None — better to treat as universal than to
    silently mismatch a malformed entry against the router.
    """
    if raw is None or not isinstance(raw, dict):
        return None
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, list):
            out[key] = [str(item) for item in value]
        elif isinstance(value, (str, int, float, bool)):
            out[key] = [str(value)]
        # Drop nested dicts / Nones — not a valid applies-to value.
    return out


def get_skill(skill_id: str, skills_dir: Path | None = None) -> str | None:
    """Return SKILL.md content for skill_id, or None if not found."""
    base = skills_dir or _SKILLS_DIR
    path = base / skill_id / "SKILL.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def get_reference(
    skill_id: str,
    reference_name: str,
    skills_dir: Path | None = None,
) -> str | None:
    """Return reference file content, or None if not found."""
    base = skills_dir or _SKILLS_DIR
    path = base / skill_id / "references" / reference_name
    return path.read_text(encoding="utf-8") if path.exists() else None


def list_skills(skills_dir: Path | None = None) -> list[SkillMeta]:
    """Return metadata for every skill that has a SKILL.md.

    Parses YAML frontmatter to extract `applies-to:` (item 5 / D.2)
    when present. A skill without the field is universal — `applies_to`
    on the returned SkillMeta is None.
    """
    base = skills_dir or _SKILLS_DIR
    skills: list[SkillMeta] = []
    for skill_path in sorted(base.glob("*/SKILL.md")):
        skill_id = skill_path.parent.name
        text = skill_path.read_text(encoding="utf-8")
        title = skill_id
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        frontmatter = _parse_frontmatter(text)
        applies_to = _coerce_applies_to(frontmatter.get("applies-to"))
        skills.append(SkillMeta(
            skill_id=skill_id,
            title=title,
            path=str(skill_path),
            applies_to=applies_to,
        ))
    return skills


def list_references(skill_id: str, skills_dir: Path | None = None) -> list[str]:
    """Return sorted list of reference file names for a skill."""
    base = skills_dir or _SKILLS_DIR
    refs_dir = base / skill_id / "references"
    if not refs_dir.exists():
        return []
    return sorted(p.name for p in refs_dir.glob("*") if p.is_file())
