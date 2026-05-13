"""Tests for pipeline.yaml-driven sub-agent spawn resolution
(scripts/policy_engine.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import policy_engine as p

import server

# ── pipeline.yaml-aware list_subagent_roles / check_subagent_allowed ─────────

def test_feature_dev_coder_spawns_from_yaml() -> None:
    """The yaml says `coder.spawns: [explorer, investigator]` — and that's
    a subset of the firewall, so the effective list matches."""
    assert p.list_subagent_roles("coder", "feature-dev") == ["explorer", "investigator"]


def test_feature_dev_reviewer_spawns_from_yaml() -> None:
    assert p.list_subagent_roles("reviewer", "feature-dev") == ["reviewer-aux"]


def test_feature_dev_planner_no_spawns() -> None:
    """planner has no `spawns:` field → empty."""
    assert p.list_subagent_roles("planner", "feature-dev") == []


def test_feature_dev_designer_no_spawns() -> None:
    assert p.list_subagent_roles("designer", "feature-dev") == []


def test_back_compat_no_pipeline_returns_firewall() -> None:
    """The pre-Phase-H call signature: pipeline_name omitted → firewall
    verbatim. Orchestrator paths + legacy callers still work."""
    assert p.list_subagent_roles("coder") == ["explorer", "investigator"]
    assert p.list_subagent_roles("reviewer") == ["reviewer-aux"]


def test_check_subagent_allowed_uses_pipeline_yaml() -> None:
    assert p.check_subagent_allowed("coder", "explorer", "feature-dev") is True
    assert p.check_subagent_allowed("coder", "reviewer-aux", "feature-dev") is False
    assert p.check_subagent_allowed("reviewer", "reviewer-aux", "feature-dev") is True


def test_check_subagent_allowed_unknown_pipeline_is_empty() -> None:
    """A pipeline name not in `.github/pipelines/` declares nothing →
    fail-closed: no spawns."""
    assert p.check_subagent_allowed("coder", "explorer", "ghost-pipeline") is False
    assert p.list_subagent_roles("coder", "ghost-pipeline") == []


def test_orchestrator_ignores_pipeline_arg() -> None:
    """Orchestrator has no pipeline.yaml. Passing a pipeline_name doesn't
    narrow its firewall entry."""
    orch = sorted(p.list_subagent_roles("orchestrator"))
    orch_with_pipe = sorted(p.list_subagent_roles("orchestrator", "feature-dev"))
    assert orch == orch_with_pipe
    assert "explorer" in orch
    assert "planner" in orch  # ad-hoc pipeline roles still spawnable


# ── firewall narrows pipeline declarations ───────────────────────────────────

def _write_pipeline_yaml(tmp_path: Path, name: str, body: dict) -> None:
    pdir = tmp_path / ".github" / "pipelines" / name
    pdir.mkdir(parents=True)
    with (pdir / "pipeline.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(body, fh)


def test_firewall_blocks_yaml_widening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a pipeline.yaml declares spawns outside MAIN_SUBAGENT_ALLOWLIST,
    the firewall drops them — yaml cannot widen the cap."""
    _write_pipeline_yaml(tmp_path, "rogue", {
        "name": "rogue",
        "generator": {
            "agents": [
                # coder firewall = ["explorer", "investigator"]; reviewer-aux
                # is permitted by SUBAGENT_POLICIES but NOT in coder's
                # firewall cap, so it must be dropped.
                {"name": "coder", "spawns": ["explorer", "reviewer-aux"]},
            ],
        },
        "evaluator": {"spawns": ["reviewer-aux", "explorer"]},
    })
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    p._reset_pipeline_spawns_cache()

    coder_roles = p.list_subagent_roles("coder", "rogue")
    assert coder_roles == ["explorer"]
    assert "reviewer-aux" not in coder_roles

    # reviewer firewall = ["reviewer-aux"]; explorer dropped.
    reviewer_roles = p.list_subagent_roles("reviewer", "rogue")
    assert reviewer_roles == ["reviewer-aux"]
    assert "explorer" not in reviewer_roles


def test_pipeline_missing_spawns_field_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipeline declares an agent but omits `spawns:` → fail-closed []."""
    _write_pipeline_yaml(tmp_path, "silent", {
        "name": "silent",
        "generator": {
            "agents": [
                {"name": "coder"},  # no spawns key
            ],
        },
    })
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    p._reset_pipeline_spawns_cache()

    assert p.list_subagent_roles("coder", "silent") == []


def test_deny_reason_mentions_pipeline_name() -> None:
    """When pipeline-scoped, the deny message should name the pipeline so
    a misconfiguration is easy to track down."""
    msg = p.subagent_deny_reason("coder", "reviewer-aux", "feature-dev")
    assert "feature-dev" in msg
    assert "coder" in msg
    assert "reviewer-aux" in msg


# ── harness_list_subagent_spawns MCP tool ────────────────────────────────────

def test_list_subagent_spawns_tool_returns_yaml_intersection() -> None:
    raw = server.harness_list_subagent_spawns("feature-dev", "coder")
    out = json.loads(raw)
    assert out["status"] == "ok"
    assert out["pipeline_name"] == "feature-dev"
    assert out["main_agent_name"] == "coder"
    assert out["roles"] == ["explorer", "investigator"]


def test_list_subagent_spawns_tool_for_unknown_pipeline() -> None:
    out = json.loads(server.harness_list_subagent_spawns("nope", "coder"))
    assert out["status"] == "ok"
    assert out["roles"] == []


def test_list_subagents_accepts_pipeline_name() -> None:
    """harness_list_subagents was extended with an optional pipeline_name
    arg; passing it should narrow the result through pipeline.yaml."""
    raw = server.harness_list_subagents("coder", pipeline_name="feature-dev")
    out = json.loads(raw)
    role_names = {r["role"] for r in out["roles"]}
    assert role_names == {"explorer", "investigator"}


def test_list_subagents_back_compat_without_pipeline() -> None:
    """Pre-Phase-H callers omit pipeline_name and get the firewall verbatim."""
    raw = server.harness_list_subagents("coder")
    out = json.loads(raw)
    role_names = {r["role"] for r in out["roles"]}
    assert role_names == {"explorer", "investigator"}


# ── pipeline.yaml shape regression: feature-dev declares the new fields ──────

def test_feature_dev_pipeline_yaml_declares_coder_spawns() -> None:
    """Phase H — feature-dev's pipeline.yaml must declare the spawns:
    field for coder so the runtime stops depending on Python defaults."""
    yaml_path = _REPO_ROOT / ".github" / "pipelines" / "feature-dev" / "pipeline.yaml"
    with yaml_path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    coder_entry = next(
        a for a in cfg["generator"]["agents"] if a["name"] == "coder"
    )
    assert coder_entry.get("spawns") == ["explorer", "investigator"]


def test_feature_dev_pipeline_yaml_declares_reviewer_spawns() -> None:
    yaml_path = _REPO_ROOT / ".github" / "pipelines" / "feature-dev" / "pipeline.yaml"
    with yaml_path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    assert cfg["evaluator"].get("spawns") == ["reviewer-aux"]
