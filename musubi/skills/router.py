"""Skill router — filter the catalog to skills that fit the workspace.

musubi-tier: substrate
expires-when: never — applicability matching is durable governance.
  It is the join between the project profile (item 4) and per-skill
  `applies-to` declarations (item 5); it survives any pipeline-shape
  dissolution.

MVP item 6 / Track D.3. Pure functions, zero I/O, zero LLM calls.
Given a project-profile dict (from `workspace/detector.py`) and skill
metadata carrying `applies_to` (from `skills/skill_loader.py`), return
only the skills whose declared applicability matches the workspace.

Matching semantics:
  - A skill with `applies_to is None` (or `{}`) is UNIVERSAL — always
    included. Most skills are universal (sub-agent procedures, code
    review, docs, api-design, …).
  - A skill that declares constraints applies only when EVERY declared
    dimension overlaps the workspace (AND across dimensions), where
    each dimension is "any-of" (OR within its list).
        applies-to: { languages: [python], test_frameworks: [pytest] }
    → shows only in a workspace whose language (or secondary language)
      is python AND whose test framework is pytest.
  - A dimension the harness doesn't recognise is IGNORED, not treated
    as a mismatch. Applicability is a UX optimisation, not a security
    boundary (the agent allowlist is the firewall, HI #3). Fail-open on
    catalog visibility: better to show a skill than to hide it because
    of a typo'd key.
  - When no profile is available, NO filtering happens — the full
    (allowlist-filtered) catalog is returned. Graceful degradation:
    the router never makes the catalog emptier than it was before
    item 6 shipped.
"""

from __future__ import annotations

from typing import Any

from skills.skill_loader import SkillMeta

# Maps an `applies-to` dimension key to the set of workspace values it is
# matched against. Each extractor returns a lowercased set; an empty set
# means "the workspace has no value for this dimension", which makes any
# skill that *declares* the dimension fail to match (a sphinx-doc skill
# must not surface in a project with no doc tool).
_DIMENSIONS: dict[str, Any] = {
    "languages": lambda p: _langs(p),
    "test_frameworks": lambda p: _scalar(p, "test_framework"),
    "doc_tools": lambda p: _scalar(p, "doc_tool"),
    "package_managers": lambda p: _list(p, "package_managers"),
    "file_types": lambda p: _list(p, "file_types_present"),
}


def applicable_skills(
    profile: dict[str, Any] | None,
    skills: list[SkillMeta],
) -> list[SkillMeta]:
    """Return the subset of `skills` that applies to `profile`.

    `profile is None` → no filtering (returns a copy of `skills`).
    """
    if not profile:
        return list(skills)
    return [meta for meta in skills if skill_applies(meta, profile)]


def skill_applies(meta: SkillMeta, profile: dict[str, Any]) -> bool:
    """True when `meta` applies to the workspace described by `profile`."""
    if not meta.applies_to:  # None or {} → universal
        return True
    for dimension, declared in meta.applies_to.items():
        extractor = _DIMENSIONS.get(dimension)
        if extractor is None:
            # Unrecognised dimension → ignore it (fail-open, see module doc).
            continue
        workspace_values = extractor(profile)
        declared_values = {str(v).lower() for v in declared}
        if not (declared_values & workspace_values):
            return False  # this dimension has no overlap → skill excluded
    return True


# ── Profile value extractors ────────────────────────────────────────────────


def _langs(profile: dict[str, Any]) -> set[str]:
    """Primary language ∪ secondary languages, lowercased.

    `language: "unknown"` (the empty-workspace sentinel) is dropped so a
    language-scoped skill never matches an unknown workspace.
    """
    values: set[str] = set()
    primary = profile.get("language")
    if primary and primary != "unknown":
        values.add(str(primary).lower())
    for secondary in profile.get("secondary_languages") or []:
        values.add(str(secondary).lower())
    return values


def _scalar(profile: dict[str, Any], key: str) -> set[str]:
    value = profile.get(key)
    return {str(value).lower()} if value else set()


def _list(profile: dict[str, Any], key: str) -> set[str]:
    return {str(v).lower() for v in (profile.get(key) or [])}
