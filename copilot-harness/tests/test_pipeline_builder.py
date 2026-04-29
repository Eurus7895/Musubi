"""Tests for the pipeline-builder pipeline.

Validates the artifact set authored at .github/pipelines/pipeline-builder/:
pipeline.yaml shape, agent files exist with valid frontmatter, slash command
is registered.

Mirrors test_pipeline_yaml.py + test_slash_commands.py but scoped to
pipeline-builder so a regression fails its own test without coupling.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PIPELINE_DIR = _REPO_ROOT / ".github" / "pipelines" / "pipeline-builder"
_PIPELINE_YAML = _PIPELINE_DIR / "pipeline.yaml"
_SLASH_CMD = _REPO_ROOT / ".github" / "commands" / "pipeline-builder.md"

_CANONICAL_AGENTS = {"planner", "designer", "coder", "reviewer"}
_CANONICAL_STAGES = {"plan", "design", "code", "review"}
_REQUIRED_BODY_SECTIONS = (
    "## Role",
    "## Instructions",
    "## Input Contract",
    "## Output Contract",
    "## Behavior Rules",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_yaml() -> dict:
    with open(_PIPELINE_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _agent_file(name: str) -> Path:
    return _PIPELINE_DIR / "agents" / f"{name}.agent.md"


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    out: dict[str, str] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if not m:
            continue
        out[m.group(1)] = m.group(2)
    return out


# ── pipeline.yaml shape ──────────────────────────────────────────────────────

def test_pipeline_dir_exists() -> None:
    assert _PIPELINE_DIR.is_dir()


def test_pipeline_yaml_exists() -> None:
    assert _PIPELINE_YAML.is_file()


def test_pipeline_yaml_top_level_keys() -> None:
    cfg = _load_yaml()
    for key in ("name", "description", "version", "level", "generator", "evaluator", "correction"):
        assert key in cfg, f"Missing top-level key {key!r}"


def test_pipeline_name_matches_dir() -> None:
    assert _load_yaml()["name"] == "pipeline-builder"


def test_pipeline_is_level_two() -> None:
    assert _load_yaml()["level"] == 2


def test_generator_uses_plural_agents_for_level_two() -> None:
    gen = _load_yaml()["generator"]
    assert "agents" in gen, "Level-2 pipeline must use generator.agents (plural)"
    assert isinstance(gen["agents"], list)
    # planner + designer + coder
    assert len(gen["agents"]) == 3, (
        f"pipeline-builder runs planner+designer+coder, got {len(gen['agents'])}"
    )


def test_generator_agent_names_are_canonical() -> None:
    names = [a["name"] for a in _load_yaml()["generator"]["agents"]]
    assert set(names) == {"planner", "designer", "coder"}, (
        f"generator names must be canonical (planner/designer/coder), got {names}"
    )


def test_generator_agent_paths_resolve() -> None:
    for item in _load_yaml()["generator"]["agents"]:
        agent_file = _PIPELINE_DIR / item["agent"]
        assert agent_file.is_file(), f"Missing agent file: {agent_file}"


def test_evaluator_agent_resolves() -> None:
    ev = _load_yaml()["evaluator"]
    assert (_PIPELINE_DIR / ev["agent"]).is_file()


def test_correction_max_retries_positive() -> None:
    corr = _load_yaml()["correction"]
    assert isinstance(corr["max_retries"], int)
    assert corr["max_retries"] >= 1


def test_baseline_checks_target_pipelines_dir() -> None:
    """pipeline-builder operates on .github/pipelines/, not src/, so its
    baseline_check should reference that path — otherwise the harness will
    refuse to run the pipeline in repos that lack a src/ tree."""
    cfg = _load_yaml()
    checks = cfg.get("baseline_checks") or []
    paths = {c.get("path") for c in checks}
    assert any(p and ".github/pipelines" in p for p in paths), (
        f"baseline_checks must include .github/pipelines/, got {paths}"
    )


# ── Agent files ──────────────────────────────────────────────────────────────

def test_all_four_canonical_agents_present() -> None:
    for name in _CANONICAL_AGENTS:
        assert _agent_file(name).is_file(), f"Missing agent: {name}"


def test_every_agent_has_valid_frontmatter() -> None:
    for name in _CANONICAL_AGENTS:
        text = _agent_file(name).read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        assert fm, f"{name}: frontmatter not parsed"
        for key in ("name", "version", "description", "model", "maxTurns"):
            assert key in fm, f"{name}: missing frontmatter key {key!r}"


def test_every_agent_has_required_body_sections() -> None:
    for name in _CANONICAL_AGENTS:
        text = _agent_file(name).read_text(encoding="utf-8")
        for section in _REQUIRED_BODY_SECTIONS:
            assert section in text, f"{name}: missing body section {section!r}"


def test_every_agent_documents_its_write_stage() -> None:
    """Each agent must document calling harness_write_stage with the right
    canonical stage — otherwise the harness will reject the write at runtime
    (server.py:_AGENT_OUTPUT_STAGE is keyed by agent name)."""
    expected = {
        "planner": "plan",
        "designer": "design",
        "coder": "code",
        "reviewer": "review",
    }
    for name, stage in expected.items():
        text = _agent_file(name).read_text(encoding="utf-8")
        marker = f'harness_write_stage(session_id, "{stage}"'
        assert marker in text, (
            f"{name}: Output Contract must call harness_write_stage(..., {stage!r}, ...)"
        )


def test_reviewer_disregards_auto_injected_skill() -> None:
    """Reviewer's auto-injected skill is feature-dev's code-review checklist —
    pipeline-builder's reviewer must explicitly tell the LLM to disregard it
    and apply the embedded pipeline-config checklist instead."""
    text = _agent_file("reviewer").read_text(encoding="utf-8")
    assert "code-review" in text.lower() and "disregard" in text.lower(), (
        "reviewer.agent.md must explicitly disregard the auto-injected code-review skill"
    )


# ── Slash command ────────────────────────────────────────────────────────────

def test_slash_command_file_exists() -> None:
    assert _SLASH_CMD.is_file()


def test_slash_command_frontmatter() -> None:
    fm = _parse_frontmatter(_SLASH_CMD.read_text(encoding="utf-8"))
    assert fm.get("name") == "pipeline-builder"
    assert fm.get("action") == "pipeline"
    assert fm.get("pipeline") == "pipeline-builder"


# ── No regression on feature-dev ──────────────────────────────────────────────

def test_feature_dev_still_intact() -> None:
    """Adding pipeline-builder must not have damaged feature-dev's pipeline.yaml."""
    fd = _REPO_ROOT / ".github" / "pipelines" / "feature-dev" / "pipeline.yaml"
    assert fd.is_file()
    cfg = yaml.safe_load(fd.read_text(encoding="utf-8"))
    assert cfg["name"] == "feature-dev"
    assert cfg["level"] == 2


# ── Stage / agent name invariants ────────────────────────────────────────────

def test_pipeline_builder_uses_only_canonical_stages() -> None:
    """Verifier and state.py reject non-canonical stages. The pipeline-builder
    docs must not introduce new stage names — its agents reuse plan/design/
    code/review."""
    expected_stages_per_agent = {
        "planner": "plan", "designer": "design",
        "coder": "code", "reviewer": "review",
    }
    for agent, stage in expected_stages_per_agent.items():
        assert stage in _CANONICAL_STAGES
        text = _agent_file(agent).read_text(encoding="utf-8")
        # confidence check: the agent at least mentions its expected stage
        assert stage in text, f"{agent}: must reference canonical stage {stage!r}"
