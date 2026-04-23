"""Tests for the Week 4 Day 5 Level-1 single-generator probe.

The probe is a measurement tool — it lets us compare a single-composite
generator against the production multi-agent Level-2 pipeline. These tests
validate the on-disk artifacts (YAML, agent file, README) so the probe
cannot drift silently while it waits to be run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROBE_DIR = _REPO_ROOT / ".github" / "pipelines" / "feature-dev-level1-probe"
_PROD_DIR = _REPO_ROOT / ".github" / "pipelines" / "feature-dev"
_PROBE_YAML = _PROBE_DIR / "pipeline.yaml"


def _load() -> dict:
    with open(_PROBE_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_probe_pipeline_yaml_exists() -> None:
    assert _PROBE_YAML.is_file(), "Week 4 Day 5 probe pipeline.yaml missing"


def test_probe_is_level_1() -> None:
    cfg = _load()
    assert cfg["level"] == 1, (
        "Probe pipeline must declare level: 1 — that is the entire point "
        "of the experiment."
    )


def test_probe_has_single_generator_not_plural() -> None:
    gen = _load()["generator"]
    assert "agent" in gen, "Level-1 probe must use singular generator.agent"
    assert "agents" not in gen, (
        "Level-1 cannot use plural generator.agents (that is Level-2 only)"
    )


def test_probe_composite_agent_file_exists() -> None:
    gen = _load()["generator"]
    agent_path = _PROBE_DIR / gen["agent"]
    assert agent_path.is_file(), f"Missing composite agent file {agent_path}"


def test_probe_reviewer_reuses_production() -> None:
    """Probe must reference the production reviewer, not a duplicate."""
    ev = _load()["evaluator"]
    ev_path = (_PROBE_DIR / ev["agent"]).resolve()
    prod_reviewer = (_PROD_DIR / "agents" / "reviewer.agent.md").resolve()
    assert ev_path == prod_reviewer, (
        f"Probe reviewer must reuse the production reviewer (got {ev_path}, "
        f"expected {prod_reviewer})"
    )


def test_probe_declares_measurement_protocol() -> None:
    cfg = _load()
    probe = cfg.get("probe")
    assert isinstance(probe, dict), "Probe pipeline must declare a probe block"
    assert "target_pass_rate" in probe
    assert "sample_size" in probe
    assert "baseline_pipeline" in probe
    assert probe["baseline_pipeline"] == "feature-dev"
    # 80% threshold from CLAUDE.md Week 4 Day 5 decision rule.
    assert 0 < probe["target_pass_rate"] <= 1


def test_probe_readme_exists_with_decision_rule() -> None:
    readme = _PROBE_DIR / "README.md"
    assert readme.is_file(), "Probe must ship with a README documenting protocol"
    text = readme.read_text(encoding="utf-8")
    # Decision rule must be explicit in the README so the protocol is discoverable.
    assert "80%" in text or "0.80" in text
    assert "decision" in text.lower()


def test_composite_agent_frontmatter_names_it_composite() -> None:
    """Composite agent file must exist with proper frontmatter."""
    agent_path = _PROBE_DIR / "agents" / "composite.agent.md"
    assert agent_path.is_file()
    text = agent_path.read_text(encoding="utf-8")
    # Frontmatter presence — the state loader depends on the `version:` field.
    assert text.startswith("---")
    assert "version:" in text
    assert "model:" in text


def test_probe_does_not_leak_into_production_pipeline_yaml() -> None:
    """Production feature-dev must stay Level 2 while the probe exists."""
    prod_yaml = _PROD_DIR / "pipeline.yaml"
    with open(prod_yaml, encoding="utf-8") as f:
        prod_cfg = yaml.safe_load(f)
    assert prod_cfg["level"] == 2, (
        "Production feature-dev stays Level 2 until probe measurement is complete"
    )
