"""Skill file I/O — serves SKILL.md and references from .github/skills/.

Zero LLM calls. Pure file I/O.

Public API:
    get_skill(skill_id, skills_dir?) → str | None
    get_reference(skill_id, reference_name, skills_dir?) → str | None
    list_skills(skills_dir?) → list[SkillMeta]
    list_references(skill_id, skills_dir?) → list[str]
"""

from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_SKILLS_DIR = _REPO_ROOT / ".github" / "skills"


@dataclass
class SkillMeta:
    skill_id: str
    title: str
    path: str


def get_skill(skill_id: str, skills_dir: Path | None = None) -> str | None:
    """Return SKILL.md content for skill_id, or None if not found."""
    base = skills_dir or _SKILLS_DIR
    path = base / skill_id / "SKILL.md"
    return path.read_text() if path.exists() else None


def get_reference(
    skill_id: str,
    reference_name: str,
    skills_dir: Path | None = None,
) -> str | None:
    """Return reference file content, or None if not found."""
    base = skills_dir or _SKILLS_DIR
    path = base / skill_id / "references" / reference_name
    return path.read_text() if path.exists() else None


def list_skills(skills_dir: Path | None = None) -> list[SkillMeta]:
    """Return metadata for every skill that has a SKILL.md."""
    base = skills_dir or _SKILLS_DIR
    skills: list[SkillMeta] = []
    for skill_path in sorted(base.glob("*/SKILL.md")):
        skill_id = skill_path.parent.name
        title = skill_id
        for line in skill_path.read_text().splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        skills.append(SkillMeta(skill_id=skill_id, title=title, path=str(skill_path)))
    return skills


def list_references(skill_id: str, skills_dir: Path | None = None) -> list[str]:
    """Return sorted list of reference file names for a skill."""
    base = skills_dir or _SKILLS_DIR
    refs_dir = base / skill_id / "references"
    if not refs_dir.exists():
        return []
    return sorted(p.name for p in refs_dir.glob("*") if p.is_file())
