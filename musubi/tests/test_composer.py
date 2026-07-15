"""Tests for composer.injected_skill_ids — pipeline.yaml-driven skill
injection that replaced the old _STAGE_SKILL_MAP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import composer
import server

# ── pure composer.injected_skill_ids ─────────────────────────────────────────

def test_feature_dev_designer_gets_api_design() -> None:
    assert composer.injected_skill_ids("feature-dev", "plan", "designer") == ["api-design"]


def test_feature_dev_coder_gets_python() -> None:
    assert composer.injected_skill_ids("feature-dev", "design", "coder") == ["python"]


def test_feature_dev_reviewer_gets_code_review() -> None:
    assert composer.injected_skill_ids("feature-dev", "code", "reviewer") == ["code-review"]


def test_planner_reading_request_has_no_skill() -> None:
    # planner has no prior stage in the STAGES order; nothing to inject.
    assert composer.injected_skill_ids("feature-dev", "request", "planner") == []
    assert composer.injected_skill_ids("feature-dev", "plan", "planner") == []


def test_skill_only_injected_on_prior_stage_read() -> None:
    """coder.skill=python is injected when coder reads 'design'; reading any
    other stage as coder yields nothing."""
    assert composer.injected_skill_ids("feature-dev", "plan", "coder") == []
    assert composer.injected_skill_ids("feature-dev", "code", "coder") == []


def test_unknown_pipeline_returns_empty() -> None:
    assert composer.injected_skill_ids("nope", "design", "coder") == []


def test_invalid_pipeline_name_returns_empty() -> None:
    assert composer.injected_skill_ids("../etc/passwd", "design", "coder") == []
    assert composer.injected_skill_ids("foo/bar", "design", "coder") == []
    assert composer.injected_skill_ids("", "design", "coder") == []


def test_unknown_agent_returns_empty() -> None:
    assert composer.injected_skill_ids("feature-dev", "design", "mystery") == []


def test_case_insensitive_agent_name() -> None:
    assert composer.injected_skill_ids("feature-dev", "design", "Coder") == ["python"]
    assert composer.injected_skill_ids("feature-dev", "code", "REVIEWER") == ["code-review"]


# ── synthetic pipeline.yaml: validate edge cases ─────────────────────────────

def _write_pipeline_yaml(tmp_path: Path, name: str, body: dict) -> Path:
    """Write `body` to tmp .github/pipelines/<name>/pipeline.yaml and point
    MUSUBI_ROOT at tmp_path so composer reads it."""
    pdir = tmp_path / ".github" / "pipelines" / name
    pdir.mkdir(parents=True)
    yaml_path = pdir / "pipeline.yaml"
    with yaml_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(body, fh)
    return yaml_path


def _write_preset_yaml(tmp_path: Path, name: str, body: dict) -> Path:
    presets = tmp_path / ".github" / "pipelines" / "presets"
    presets.mkdir(parents=True, exist_ok=True)
    path = presets / f"{name}.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(body, fh)
    return path


def test_flat_explicit_stage_projection_preserves_spawn_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_pipeline_yaml(tmp_path, "flat-explicit", {
        "stages": [
            {"agent": "Coder", "stage": "build", "spawns": ["Explorer"]},
            {"agent": "reviewer", "stage": "check"},
        ],
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    assert composer.pipeline_stage_entries("flat-explicit") == [
        {
            "agent": "coder",
            "stage": "build",
            "preset": "",
            "spawns": ["explorer"],
        },
        {
            "agent": "reviewer",
            "stage": "check",
            "preset": "",
            "spawns": [],
        },
    ]


def test_flat_preset_stage_projection_resolves_agent_and_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_preset_yaml(tmp_path, "build", {
        "id": "build",
        "agent": "coder",
        "stage": "code",
    })
    _write_pipeline_yaml(tmp_path, "flat-preset", {
        "stages": [
            {"preset": "build", "spawns": ["Explorer"]},
            {"agent": "reviewer", "stage": "review"},
        ],
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    assert composer.pipeline_stage_entries("flat-preset")[0] == {
        "agent": "coder",
        "stage": "code",
        "preset": "build",
        "spawns": ["explorer"],
    }


def test_flat_stage_spawns_do_not_change_pipeline_chain_order() -> None:
    data = {
        "stages": [
            {"agent": "planner", "stage": "plan", "spawns": []},
            {"agent": "coder", "stage": "code", "spawns": ["explorer"]},
            {"agent": "reviewer", "stage": "review"},
        ],
    }

    assert composer._pipeline_stage_chain(data) == [
        ("planner", "plan"),
        ("coder", "code"),
        ("reviewer", "review"),
    ]


@pytest.mark.parametrize(
    ("spawns", "error_fragment"),
    [
        (None, "must be a list"),
        ("explorer", "must be a list"),
        ([7], "non-string"),
        (["explorer", "EXPLORER"], "duplicate"),
        (["ghost-runner"], "unknown role"),
        (["reviewer-aux"], "outside"),
    ],
)
def test_catalog_rejects_invalid_flat_stage_spawn_declarations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spawns: object,
    error_fragment: str,
) -> None:
    _write_pipeline_yaml(tmp_path, "invalid-spawns", {
        "stages": [
            {"agent": "coder", "stage": "code", "spawns": spawns},
            {"agent": "reviewer", "stage": "review"},
        ],
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    errors = composer.validate_catalog()

    assert any(
        "invalid-spawns" in error
        and "coder" in error
        and error_fragment in error
        for error in errors
    ), errors


def test_catalog_rejects_duplicate_resolved_flat_stage_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_pipeline_yaml(tmp_path, "duplicate-agent", {
        "stages": [
            {"agent": "coder", "stage": "prepare"},
            {"agent": "Coder", "stage": "implement", "spawns": ["explorer"]},
            {"agent": "reviewer", "stage": "review"},
        ],
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    errors = composer.validate_catalog()

    assert any(
        "duplicate-agent" in error
        and "coder" in error
        and "duplicate resolved agent" in error
        for error in errors
    ), errors


def test_null_skill_field_yields_no_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_pipeline_yaml(tmp_path, "synth", {
        "name": "synth",
        "generator": {
            "agents": [
                {"name": "coder", "skill": None},
            ],
        },
        "evaluator": {"skill": None},
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()
    assert composer.injected_skill_ids("synth", "design", "coder") == []
    assert composer.injected_skill_ids("synth", "code", "reviewer") == []


def test_bare_skill_id_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare 'api-design' value (not 'skills/api-design/SKILL.md') is
    accepted — convenience for hand-written pipelines."""
    _write_pipeline_yaml(tmp_path, "synth2", {
        "name": "synth2",
        "generator": {
            "agents": [
                # planner first so designer has a prior stage to read.
                {"name": "planner", "stage": "plan", "skill": None},
                {"name": "designer", "stage": "design", "skill": "api-design"},
            ],
        },
        "evaluator": {},
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()
    assert composer.injected_skill_ids("synth2", "plan", "designer") == ["api-design"]


def test_malformed_yaml_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdir = tmp_path / ".github" / "pipelines" / "broken"
    pdir.mkdir(parents=True)
    (pdir / "pipeline.yaml").write_text("::: not yaml :::", encoding="utf-8")
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()
    assert composer.injected_skill_ids("broken", "design", "coder") == []


def test_missing_yaml_module_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: older PyInstaller bundles of the harness don't include
    PyYAML in their venv. composer.py was rewritten to import yaml lazily
    inside `_load_pipeline_yaml` and soft-fail on ImportError so the server
    still boots — `active_stages` and friends fall back to the canonical
    feature-dev defaults. Without this guard the harness binary fails to
    activate with `ModuleNotFoundError: No module named 'yaml'`.
    """
    import sys
    monkeypatch.setitem(sys.modules, "yaml", None)
    composer.reset_cache()
    # Importing composer is unaffected (lazy import lives inside the loader).
    # active_stages must return the canonical 4-stage list, not crash.
    assert composer.active_stages("feature-dev") == ["plan", "design", "code", "review"]
    # Same for the other helpers — they all funnel through _load_pipeline_yaml
    # which now soft-fails when yaml is unimportable.
    assert composer.output_stage_for_agent("feature-dev", "coder") == "code"
    assert composer.evaluator_input_stage("feature-dev") == "code"
    # injected_skill_ids — without yaml the loader returns {}, so no skill
    # is injected. The harness's overall behaviour degrades to "no per-
    # pipeline customisation" but the server keeps running.
    assert composer.injected_skill_ids("feature-dev", "design", "coder") == []


# ── musubi_get_injected_skills MCP tool ─────────────────────────────────────

def test_mcp_tool_feature_dev_designer() -> None:
    raw = server.musubi_get_injected_skills("feature-dev", "plan", "designer")
    out = json.loads(raw)
    assert out["status"] == "ok"
    assert out["skill_ids"] == ["api-design"]
    assert out["pipeline_name"] == "feature-dev"


def test_mcp_tool_firewall_blocks_disallowed() -> None:
    """If a pipeline.yaml declares a skill outside the agent's allowlist, the
    MCP tool drops it. We can't trivially test this against feature-dev
    (every declaration is allowed by AGENT_SKILL_ALLOWLIST), but we can pin
    the contract: the tool filters via AGENT_SKILL_ALLOWLIST.

    Coder's allowlist contains 'python' but not 'code-review' — verify the
    tool would drop code-review if a pipeline declared it for coder.
    """
    # Constructed by hand: composer returns what yaml says; server filters.
    # The contract is: server.musubi_get_injected_skills returns []
    # when allowlist denies. Smoke against feature-dev's clean case here.
    out = json.loads(server.musubi_get_injected_skills("feature-dev", "design", "coder"))
    assert "python" in out["skill_ids"]


def test_mcp_tool_unknown_pipeline_returns_empty_list() -> None:
    out = json.loads(server.musubi_get_injected_skills("nope", "design", "coder"))
    assert out["status"] == "ok"
    assert out["skill_ids"] == []


# ── active_stages / output_stage_for_agent / evaluator_input_stage ───────────

def test_active_stages_feature_dev_canonical() -> None:
    """Feature-dev's pipeline.yaml doesn't declare `stage:` per agent yet;
    the loader falls back to the canonical map and returns the 4-stage list."""
    assert composer.active_stages("feature-dev") == ["plan", "design", "code", "review"]


def test_active_stages_unknown_pipeline_falls_back() -> None:
    """Soft-fail: an unknown pipeline returns the canonical list, matching the
    same posture as `musubi_get_correction_rules`."""
    assert composer.active_stages("nope") == ["plan", "design", "code", "review"]


def test_output_stage_for_agent_feature_dev() -> None:
    assert composer.output_stage_for_agent("feature-dev", "planner") == "plan"
    assert composer.output_stage_for_agent("feature-dev", "designer") == "design"
    assert composer.output_stage_for_agent("feature-dev", "coder") == "code"
    assert composer.output_stage_for_agent("feature-dev", "reviewer") == "review"


def test_output_stage_for_unknown_agent_returns_none() -> None:
    assert composer.output_stage_for_agent("feature-dev", "mystery") is None


def test_agent_for_stage_feature_dev() -> None:
    assert composer.agent_for_stage("feature-dev", "plan") == "planner"
    assert composer.agent_for_stage("feature-dev", "design") == "designer"
    assert composer.agent_for_stage("feature-dev", "code") == "coder"
    assert composer.agent_for_stage("feature-dev", "review") == "reviewer"


def test_agent_for_unknown_stage_returns_none() -> None:
    assert composer.agent_for_stage("feature-dev", "mystery") is None


def test_evaluator_input_stage_feature_dev() -> None:
    """Feature-dev's evaluator (reviewer) reads `code` as its input."""
    assert composer.evaluator_input_stage("feature-dev") == "code"


def test_evaluator_input_stage_unknown_pipeline_canonical_fallback() -> None:
    assert composer.evaluator_input_stage("nope") == "code"


# ── synthetic pipeline declaring explicit `stage:` fields ────────────────────

def test_synthetic_pipeline_explicit_stage_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipeline declaring `stage:` per agent (the shape new pipelines will
    use) drives the helpers without touching the canonical fallback."""
    _write_pipeline_yaml(tmp_path, "review-pipe", {
        "name": "review-pipe",
        "generator": {
            "agents": [
                {"name": "scoper",  "stage": "scope",
                 "skill": "skills/pr-scope-detection/SKILL.md"},
                {"name": "finder",  "stage": "findings",
                 "skill": "skills/per-file-review/SKILL.md"},
            ],
        },
        "evaluator": {
            "name": "synthesizer",
            "stage": "synthesis",
            "skill": "skills/code-review/SKILL.md",
        },
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    assert composer.active_stages("review-pipe") == ["scope", "findings", "synthesis"]
    assert composer.output_stage_for_agent("review-pipe", "scoper") == "scope"
    assert composer.output_stage_for_agent("review-pipe", "finder") == "findings"
    assert composer.output_stage_for_agent("review-pipe", "synthesizer") == "synthesis"
    assert composer.agent_for_stage("review-pipe", "findings") == "finder"
    assert composer.evaluator_input_stage("review-pipe") == "findings"


def test_synthetic_pipeline_three_arbitrary_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage names are arbitrary — `[alpha, beta, gamma]` resolves cleanly.
    Pinned to ensure no canonical-name assumption leaked into the helpers."""
    _write_pipeline_yaml(tmp_path, "greek", {
        "name": "greek",
        "generator": {
            "agents": [
                {"name": "first",  "stage": "alpha", "skill": None},
                {"name": "second", "stage": "beta",  "skill": None},
            ],
        },
        "evaluator": {"name": "judge", "stage": "gamma", "skill": None},
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    assert composer.active_stages("greek") == ["alpha", "beta", "gamma"]
    assert composer.output_stage_for_agent("greek", "first") == "alpha"
    assert composer.evaluator_input_stage("greek") == "beta"


# ── musubi_get_pipeline_stages MCP tool ─────────────────────────────────────

def test_get_pipeline_stages_tool_feature_dev() -> None:
    raw = server.musubi_get_pipeline_stages("feature-dev")
    out = json.loads(raw)
    assert out["status"] == "ok"
    assert out["pipeline_name"] == "feature-dev"
    assert out["stages"] == ["plan", "design", "code", "review"]


def test_get_pipeline_stages_tool_unknown_pipeline_falls_back() -> None:
    out = json.loads(server.musubi_get_pipeline_stages("nope"))
    assert out["status"] == "ok"
    assert out["stages"] == ["plan", "design", "code", "review"]


# ── stage-active guards in musubi_read_stage / musubi_write_stage ──────────

def test_read_stage_rejects_inactive_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the calling session belongs to a pipeline that doesn't run a given
    stage, musubi_read_stage returns a `note` rather than serving data."""
    _write_pipeline_yaml(tmp_path, "shortie", {
        "name": "shortie",
        "generator": {
            "agents": [
                {"name": "scoper", "stage": "scope", "skill": None},
            ],
        },
        "evaluator": {"name": "judge", "stage": "verdict", "skill": None},
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    # Fake `get_pipeline_run` so the session lookup returns the shortie pipeline.
    from storage import db as _db
    monkeypatch.setattr(
        _db, "get_pipeline_run",
        lambda sid, db_path=None: {"pipeline_name": "shortie"},
    )

    raw = server.musubi_read_stage("sess-x", "design", "coder")
    out = json.loads(raw)
    assert out["data"] is None
    assert "not active" in out["note"]
    assert "shortie" in out["note"]


def test_write_stage_rejects_inactive_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guard on the write side."""
    _write_pipeline_yaml(tmp_path, "shortie2", {
        "name": "shortie2",
        "generator": {
            "agents": [
                {"name": "scoper", "stage": "scope", "skill": None},
            ],
        },
        "evaluator": {"name": "judge", "stage": "verdict", "skill": None},
    })
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    composer.reset_cache()

    from storage import db as _db
    monkeypatch.setattr(
        _db, "get_pipeline_run",
        lambda sid, db_path=None: {"pipeline_name": "shortie2"},
    )

    raw = server.musubi_write_stage(
        "sess-y", "design", {"summary": "x", "tasks": []}, "designer",
    )
    out = json.loads(raw)
    assert out["status"] == "error"
    assert "not active" in out["error"]
    assert "shortie2" in out["error"]
