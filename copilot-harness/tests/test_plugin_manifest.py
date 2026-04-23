"""Validate the feature-dev plugin manifest (Week 4 Day 2).

The plugin.json is purely declarative — it lists every command, agent,
skill, and hook file that makes up the pipeline. The test fails if any
referenced path does not exist on disk, so the manifest cannot drift
silently from the real files.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST = (
    _REPO_ROOT
    / ".github"
    / "pipelines"
    / "feature-dev"
    / ".claude-plugin"
    / "plugin.json"
)

REQUIRED_KEYS = {"name", "version", "description", "commands", "agents", "skills", "hooks"}
VALID_LOCALITY_MODES = {"global", "pipeline-local"}


def _load() -> dict:
    assert _MANIFEST.is_file(), f"Plugin manifest missing: {_MANIFEST}"
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_manifest_parses_as_json() -> None:
    data = _load()
    assert isinstance(data, dict), "plugin.json top-level must be an object"


def test_manifest_has_required_keys() -> None:
    data = _load()
    missing = REQUIRED_KEYS - data.keys()
    assert not missing, f"plugin.json missing keys: {missing}"


def test_manifest_name_matches_pipeline_dir() -> None:
    data = _load()
    assert data["name"] == "feature-dev"


def test_every_referenced_command_exists() -> None:
    data = _load()
    for rel in data["commands"]:
        assert (_REPO_ROOT / rel).is_file(), f"Missing command file: {rel}"


def test_every_referenced_agent_exists() -> None:
    data = _load()
    for rel in data["agents"]:
        assert (_REPO_ROOT / rel).is_file(), f"Missing agent file: {rel}"


def test_every_referenced_skill_exists() -> None:
    data = _load()
    for rel in data["skills"]:
        assert (_REPO_ROOT / rel).is_file(), f"Missing skill file: {rel}"


def test_hooks_path_exists() -> None:
    data = _load()
    assert (_REPO_ROOT / data["hooks"]).is_file(), f"Missing hooks file: {data['hooks']}"


def test_pipeline_definition_exists() -> None:
    data = _load()
    pipeline_block = data.get("pipeline")
    assert isinstance(pipeline_block, dict), "pipeline block must be an object"
    definition = pipeline_block.get("definition")
    assert definition, "pipeline.definition path missing"
    assert (_REPO_ROOT / definition).is_file(), f"Missing pipeline.yaml: {definition}"


def test_skill_locality_decision_is_recorded() -> None:
    """Week 4 Day 2 — pipeline must declare whether skills are global or pipeline-local."""
    data = _load()
    locality = data.get("skillLocality")
    assert isinstance(locality, dict), (
        "Week 4 Day 2 decision missing — add skillLocality block to plugin.json"
    )
    mode = locality.get("mode")
    assert mode in VALID_LOCALITY_MODES, (
        f"skillLocality.mode must be one of {VALID_LOCALITY_MODES}, got {mode!r}"
    )
    rationale = locality.get("rationale", "")
    assert len(rationale) > 40, (
        "skillLocality.rationale must explain the decision (min 40 chars)"
    )


def test_mcp_server_entry_present() -> None:
    data = _load()
    servers = data.get("mcpServers", {})
    assert "copilot-harness" in servers, "plugin.json: mcpServers.copilot-harness missing"
    spec = servers["copilot-harness"]
    assert "command" in spec and "args" in spec, (
        "mcpServers.copilot-harness needs both 'command' and 'args'"
    )
