"""Tests for .github/pipelines/feature-dev/pipeline.yaml (Week 3b).

Validates the YAML structure matches the contract in CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PIPELINE_DIR = _REPO_ROOT / ".github" / "pipelines" / "feature-dev"
_PIPELINE_YAML = _PIPELINE_DIR / "pipeline.yaml"


def _load() -> dict:
    with open(_PIPELINE_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_pipeline_yaml_exists() -> None:
    assert _PIPELINE_YAML.is_file()


def test_pipeline_yaml_has_required_top_level_keys() -> None:
    cfg = _load()
    for key in ("name", "description", "version", "level", "generator", "evaluator", "correction"):
        assert key in cfg, f"Missing top-level key {key!r}"


def test_pipeline_name_is_feature_dev() -> None:
    assert _load()["name"] == "feature-dev"


def test_level_is_two_because_multi_agent() -> None:
    cfg = _load()
    # CLAUDE.md Week 3b: multi-agent pipeline requires level: 2.
    assert cfg["level"] == 2


def test_generator_uses_plural_agents_list() -> None:
    gen = _load()["generator"]
    assert "agents" in gen, "Level-2 pipeline must use plural generator.agents"
    assert isinstance(gen["agents"], list)
    assert len(gen["agents"]) >= 2


def test_generator_agent_paths_resolve() -> None:
    for item in _load()["generator"]["agents"]:
        agent_file = _PIPELINE_DIR / item["agent"]
        assert agent_file.is_file(), f"Missing agent file {agent_file}"


def test_evaluator_agent_path_resolves() -> None:
    ev_path = _PIPELINE_DIR / _load()["evaluator"]["agent"]
    assert ev_path.is_file(), f"Missing evaluator agent file {ev_path}"


def test_correction_max_retries_positive() -> None:
    corr = _load()["correction"]
    assert isinstance(corr["max_retries"], int)
    assert corr["max_retries"] >= 1


def test_baseline_checks_is_list() -> None:
    cfg = _load()
    checks = cfg.get("baseline_checks") or []
    assert isinstance(checks, list)
    for c in checks:
        assert "type" in c
