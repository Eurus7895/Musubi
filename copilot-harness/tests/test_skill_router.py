"""Tests for the skill router — MVP item 6 / Track D.3.

harness-tier: substrate test — pins the applicability-matching contract
that joins the project profile (item 4) to per-skill applies-to (item 5).
"""

from __future__ import annotations

import json

import pytest

import server
from skills import router
from skills.skill_loader import SkillMeta


def _meta(skill_id: str, applies_to: dict | None) -> SkillMeta:
    return SkillMeta(
        skill_id=skill_id,
        title=skill_id,
        path=f"/x/{skill_id}/SKILL.md",
        applies_to=applies_to,
    )


# Representative profiles.
PY = {
    "language": "python",
    "secondary_languages": ["typescript"],
    "package_managers": ["pip", "npm"],
    "test_framework": "pytest",
    "doc_tool": "sphinx",
    "file_types_present": [".py", ".ts"],
}
RUST = {
    "language": "rust",
    "secondary_languages": [],
    "package_managers": ["cargo"],
    "test_framework": None,
    "doc_tool": None,
    "file_types_present": [".rs"],
}
EMPTY = {
    "language": "unknown",
    "secondary_languages": [],
    "package_managers": [],
    "test_framework": None,
    "doc_tool": None,
    "file_types_present": [],
}


# ── Universal skills always pass ───────────────────────────────────────────


def test_universal_skill_none_applies_everywhere() -> None:
    m = _meta("code-review", None)
    assert router.skill_applies(m, PY)
    assert router.skill_applies(m, RUST)
    assert router.skill_applies(m, EMPTY)


def test_universal_skill_empty_dict_applies_everywhere() -> None:
    m = _meta("docs", {})
    assert router.skill_applies(m, RUST)
    assert router.skill_applies(m, EMPTY)


# ── Single-dimension language matching ─────────────────────────────────────


def test_language_match_primary() -> None:
    assert router.skill_applies(_meta("python", {"languages": ["python"]}), PY)


def test_language_match_secondary() -> None:
    # typescript is a secondary language of the PY profile.
    assert router.skill_applies(_meta("ts", {"languages": ["typescript"]}), PY)


def test_language_mismatch_excludes() -> None:
    assert not router.skill_applies(_meta("python", {"languages": ["python"]}), RUST)


def test_language_unknown_workspace_excludes_language_scoped() -> None:
    assert not router.skill_applies(_meta("python", {"languages": ["python"]}), EMPTY)


def test_language_case_insensitive() -> None:
    assert router.skill_applies(_meta("py", {"languages": ["Python"]}), PY)


# ── Multi-dimension AND semantics ──────────────────────────────────────────


def test_all_dimensions_must_match() -> None:
    m = _meta("testing", {"languages": ["python"], "test_frameworks": ["pytest"]})
    assert router.skill_applies(m, PY)  # python + pytest both hold


def test_one_dimension_failing_excludes() -> None:
    # RUST has language rust (≠ python) → excluded even though it has no
    # pytest either.
    m = _meta("testing", {"languages": ["python"], "test_frameworks": ["pytest"]})
    assert not router.skill_applies(m, RUST)


def test_test_framework_mismatch_excludes() -> None:
    jest_profile = {**PY, "test_framework": "jest"}
    m = _meta("testing", {"test_frameworks": ["pytest"]})
    assert not router.skill_applies(m, jest_profile)


def test_doc_tool_match() -> None:
    assert router.skill_applies(_meta("sphinx", {"doc_tools": ["sphinx"]}), PY)


def test_doc_tool_absent_excludes() -> None:
    # RUST has no doc tool → a doc-tool-scoped skill is excluded.
    assert not router.skill_applies(_meta("sphinx", {"doc_tools": ["sphinx"]}), RUST)


def test_package_manager_any_of() -> None:
    assert router.skill_applies(_meta("pkg", {"package_managers": ["cargo", "pip"]}), PY)


def test_file_types_match() -> None:
    assert router.skill_applies(_meta("ts-tool", {"file_types": [".ts"]}), PY)


# ── Unrecognised dimension is ignored (fail-open) ──────────────────────────


def test_unknown_dimension_ignored() -> None:
    # A typo'd / future key must not hide the skill — applicability is UX,
    # not a firewall.
    m = _meta("x", {"frameworks": ["django"]})  # not a recognised dimension
    assert router.skill_applies(m, PY)


def test_unknown_dimension_mixed_with_known() -> None:
    # Known dimension matches, unknown is ignored → applies.
    m = _meta("x", {"languages": ["python"], "frameworks": ["django"]})
    assert router.skill_applies(m, PY)
    # Known dimension fails → excluded regardless of the ignored unknown.
    assert not router.skill_applies(m, RUST)


# ── applicable_skills list filter ──────────────────────────────────────────


def test_applicable_skills_filters_list() -> None:
    skills = [
        _meta("code-review", None),                       # universal
        _meta("python", {"languages": ["python"]}),       # matches PY
        _meta("rustfmt", {"languages": ["rust"]}),        # excluded on PY
    ]
    out = {m.skill_id for m in router.applicable_skills(PY, skills)}
    assert out == {"code-review", "python"}


def test_applicable_skills_none_profile_no_filtering() -> None:
    skills = [
        _meta("python", {"languages": ["python"]}),
        _meta("rustfmt", {"languages": ["rust"]}),
    ]
    out = {m.skill_id for m in router.applicable_skills(None, skills)}
    assert out == {"python", "rustfmt"}  # nothing filtered


def test_applicable_skills_empty_profile_dict_no_filtering() -> None:
    skills = [_meta("python", {"languages": ["python"]})]
    # An empty dict is falsy → treated as "no profile" → no filtering.
    assert len(router.applicable_skills({}, skills)) == 1


# ── server.harness_list_skills integration ─────────────────────────────────


def test_list_skills_payload_has_filtered_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # No profile → flag false, allowlist behaviour unchanged.
    monkeypatch.setattr(server, "_load_project_profile", lambda: None)
    payload = json.loads(server.harness_list_skills("coder"))
    assert payload["filtered_by_profile"] is False
    assert "skills" in payload


def test_list_skills_python_profile_keeps_python_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "_load_project_profile", lambda: PY)
    payload = json.loads(server.harness_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert payload["filtered_by_profile"] is True
    # python skill is in coder's allowlist AND applies to a python workspace.
    assert "python" in ids


def test_list_skills_rust_profile_drops_python_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline behaviour: a python-scoped skill disappears from the
    catalog in a rust workspace."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: RUST)
    payload = json.loads(server.harness_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "python" not in ids
    assert "testing" not in ids  # python + pytest scoped


def test_list_skills_rust_profile_keeps_universal_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Universal skills survive profile filtering. database-patterns has no
    applies-to, so it stays for any agent allowed to load it."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: RUST)
    payload = json.loads(server.harness_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    # At least one universal skill in coder's allowlist remains.
    assert "database-patterns" in ids or "api-design" in ids


def test_list_skills_allowlist_still_enforced_under_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile filtering must never widen the allowlist — code-review is
    reviewer-only and must not leak into coder's catalog even though it's
    a universal skill."""
    monkeypatch.setattr(server, "_load_project_profile", lambda: PY)
    payload = json.loads(server.harness_list_skills("coder"))
    ids = {s["skill_id"] for s in payload["skills"]}
    assert "code-review" not in ids
