"""Tests for SKILL.md `applies-to` frontmatter parsing — MVP item 5 / Track D.2.

musubi-tier: substrate test — applicability declarations are the input
to the skill router (item 6 / Track D.3). These tests pin the parsing
contract that the router will rely on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skills import skill_loader
from skills.skill_loader import (
    SkillMeta,
    _coerce_applies_to,
    _parse_frontmatter,
    list_skills,
)


# ── Frontmatter parser ─────────────────────────────────────────────────────


def test_frontmatter_parses_simple_dict() -> None:
    text = "---\nname: foo\ndescription: bar\n---\n# Body\n"
    assert _parse_frontmatter(text) == {"name": "foo", "description": "bar"}


def test_frontmatter_missing_returns_empty() -> None:
    assert _parse_frontmatter("# Just a body\n") == {}


def test_frontmatter_unterminated_returns_empty() -> None:
    # Opening delim but no closing one — treat as no frontmatter.
    assert _parse_frontmatter("---\nname: foo\nno closing delim\n") == {}


def test_frontmatter_malformed_yaml_returns_empty() -> None:
    # `foo: : bar` is a YAML parse error.
    assert _parse_frontmatter("---\nfoo: : bar\n---\nbody\n") == {}


def test_frontmatter_non_dict_returns_empty() -> None:
    # A YAML scalar at the top level is not a frontmatter dict.
    assert _parse_frontmatter("---\njust-a-string\n---\nbody\n") == {}


def test_frontmatter_handles_eof_without_trailing_newline() -> None:
    # Closing delim is the last line, no newline after.
    text = "---\nname: foo\n---"
    assert _parse_frontmatter(text) == {"name": "foo"}


# ── applies-to coercion ────────────────────────────────────────────────────


def test_coerce_none_means_universal() -> None:
    assert _coerce_applies_to(None) is None


def test_coerce_non_dict_means_universal() -> None:
    assert _coerce_applies_to(["python"]) is None
    assert _coerce_applies_to("python") is None


def test_coerce_lists_preserved() -> None:
    raw = {"languages": ["python", "typescript"], "test_frameworks": ["pytest"]}
    assert _coerce_applies_to(raw) == {
        "languages": ["python", "typescript"],
        "test_frameworks": ["pytest"],
    }


def test_coerce_scalar_wrapped_to_list() -> None:
    # Shorthand: `languages: python` → ["python"].
    assert _coerce_applies_to({"languages": "python"}) == {"languages": ["python"]}


def test_coerce_drops_nested_dict_values() -> None:
    # Anything that isn't a list-or-scalar is dropped; the key survives if
    # any other valid entries exist; otherwise we get {}.
    result = _coerce_applies_to({"languages": ["python"], "nested": {"x": 1}})
    assert result == {"languages": ["python"]}


def test_coerce_empty_dict_returns_empty_dict() -> None:
    # An explicit empty applies-to is still "universal" semantically, but
    # the loader preserves the distinction (the router treats both as match-all).
    assert _coerce_applies_to({}) == {}


def test_coerce_int_value_stringified() -> None:
    # YAML may parse a bare token as an int; we want strings on the way out.
    assert _coerce_applies_to({"languages": [3, "python"]}) == {
        "languages": ["3", "python"],
    }


# ── list_skills end-to-end against fixture catalogs ────────────────────────


@pytest.fixture
def fixture_catalog(tmp_path: Path) -> Path:
    """Build a tiny fixture skills dir to exercise applies_to round-trip."""
    skills = tmp_path / "skills"
    universal = skills / "universal-thing" / "SKILL.md"
    universal.parent.mkdir(parents=True)
    universal.write_text(
        "---\nname: universal-thing\ndescription: applies everywhere\n---\n"
        "## Purpose\nBody.\n",
        encoding="utf-8",
    )
    py_only = skills / "py-only" / "SKILL.md"
    py_only.parent.mkdir(parents=True)
    py_only.write_text(
        "---\nname: py-only\ndescription: python only\n"
        "applies-to:\n  languages: [python]\n---\n"
        "## Purpose\nBody.\n",
        encoding="utf-8",
    )
    py_pytest = skills / "py-pytest" / "SKILL.md"
    py_pytest.parent.mkdir(parents=True)
    py_pytest.write_text(
        "---\nname: py-pytest\ndescription: python pytest only\n"
        "applies-to:\n  languages: [python]\n  test_frameworks: [pytest]\n---\n"
        "## Purpose\nBody.\n",
        encoding="utf-8",
    )
    return skills


def test_list_skills_marks_universal_skill_with_none(fixture_catalog: Path) -> None:
    by_id = {s.skill_id: s for s in list_skills(skills_dir=fixture_catalog)}
    assert by_id["universal-thing"].applies_to is None


def test_list_skills_parses_single_constraint(fixture_catalog: Path) -> None:
    by_id = {s.skill_id: s for s in list_skills(skills_dir=fixture_catalog)}
    assert by_id["py-only"].applies_to == {"languages": ["python"]}


def test_list_skills_parses_multiple_constraints(fixture_catalog: Path) -> None:
    by_id = {s.skill_id: s for s in list_skills(skills_dir=fixture_catalog)}
    assert by_id["py-pytest"].applies_to == {
        "languages": ["python"],
        "test_frameworks": ["pytest"],
    }


# ── Real catalog (.github/skills/) carries the right declarations ──────────
# These are integration-style guards on the actual shipping SKILL.md files.


def test_real_python_skill_declares_language_python() -> None:
    skills = {s.skill_id: s for s in list_skills()}
    assert "python" in skills, "python skill missing from catalog"
    assert skills["python"].applies_to == {"languages": ["python"]}


def test_real_testing_skill_declares_pytest_python() -> None:
    skills = {s.skill_id: s for s in list_skills()}
    assert "testing" in skills, "testing skill missing from catalog"
    assert skills["testing"].applies_to == {
        "languages": ["python"],
        "test_frameworks": ["pytest"],
    }


def test_real_universal_skills_have_no_applies_to() -> None:
    """Sub-agent and meta skills are universal by design — no constraint."""
    skills = {s.skill_id: s for s in list_skills()}
    universal_ids = [
        "explorer",
        "investigator",
        "reviewer-aux",
        "summarizer",
        "agent-routing",
        "code-review",
        "per-file-review",
    ]
    for sid in universal_ids:
        if sid in skills:  # tolerate catalog growth
            assert skills[sid].applies_to is None, (
                f"{sid} unexpectedly declares applies-to: {skills[sid].applies_to}"
            )


# ── Backwards-compat: SkillMeta default doesn't break existing callers ────


def test_skill_meta_default_applies_to_is_none() -> None:
    """Existing callers that construct SkillMeta(skill_id, title, path) must
    keep working — the new field defaults to None."""
    meta = SkillMeta(skill_id="x", title="X", path="/tmp/x/SKILL.md")
    assert meta.applies_to is None


def test_list_skills_payload_still_has_skill_id_and_title() -> None:
    """musubi_list_skills uses skill_id + title; guard those stay populated."""
    skills = list_skills()
    assert skills, "no skills in real catalog?"
    for s in skills:
        assert s.skill_id
        assert s.title


# Sanity guard: the importable name didn't change.
def test_skill_loader_module_exposes_applies_to_field() -> None:
    assert "applies_to" in SkillMeta.__dataclass_fields__
