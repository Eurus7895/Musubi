"""Tests for .github/commands/*.md slash command definitions (Week 3c).

The Python side can't run slashCommands.ts directly, so this test
validates the on-disk frontmatter matches the TS loader's expectations:
valid YAML-ish frontmatter, one of four action values, `agent` present
when action=step, `pipeline` present when action=pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_COMMANDS_DIR = _REPO_ROOT / ".github" / "commands"

VALID_ACTIONS = {"pipeline", "step", "continue", "status", "help", "agent", "orchestrator"}
# Step actions must use a canonical pipeline role. `action: agent` (one-shot)
# accepts any agent file in `.github/agents/`.
VALID_STEP_AGENTS = {"planner", "designer", "coder", "reviewer"}
VALID_AGENTS = VALID_STEP_AGENTS


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _command_files() -> list[Path]:
    return sorted(_COMMANDS_DIR.glob("*.md"))


def test_commands_directory_exists() -> None:
    assert _COMMANDS_DIR.is_dir(), (
        f"Slash command directory missing: {_COMMANDS_DIR}"
    )


def test_at_least_one_command_file_present() -> None:
    assert _command_files(), "No .github/commands/*.md files found"


def test_feature_dev_command_exists() -> None:
    assert (_COMMANDS_DIR / "feature-dev.md").is_file()


def test_every_command_has_valid_frontmatter() -> None:
    for f in _command_files():
        fm = _parse_frontmatter(f.read_text())
        assert fm, f"{f.name}: no frontmatter parsed"
        assert "name" in fm, f"{f.name}: missing name"
        assert "description" in fm, f"{f.name}: missing description"
        assert fm.get("action") in VALID_ACTIONS, (
            f"{f.name}: action={fm.get('action')!r} not in {VALID_ACTIONS}"
        )


def test_pipeline_commands_declare_a_pipeline() -> None:
    for f in _command_files():
        fm = _parse_frontmatter(f.read_text())
        if fm.get("action") == "pipeline":
            assert fm.get("pipeline"), f"{f.name}: pipeline action needs pipeline key"


def test_step_commands_declare_a_valid_agent() -> None:
    for f in _command_files():
        fm = _parse_frontmatter(f.read_text())
        if fm.get("action") == "step":
            agent = fm.get("agent")
            assert agent in VALID_AGENTS, (
                f"{f.name}: step action needs a valid agent, got {agent!r}"
            )


def test_command_name_matches_filename_stem() -> None:
    for f in _command_files():
        fm = _parse_frontmatter(f.read_text())
        assert fm.get("name") == f.stem, (
            f"{f.name}: name={fm.get('name')!r} does not match stem {f.stem!r}"
        )


def test_help_command_exists_with_help_action() -> None:
    help_file = _COMMANDS_DIR / "help.md"
    assert help_file.is_file(), "Week 4 Day 1: .github/commands/help.md missing"
    fm = _parse_frontmatter(help_file.read_text())
    assert fm.get("action") == "help", (
        f"help.md: action must be 'help', got {fm.get('action')!r}"
    )
    # help takes no args, so neither pipeline nor agent are required.
    assert "pipeline" not in fm, "help.md: must not declare a pipeline"
    assert "agent" not in fm, "help.md: must not declare an agent"


def test_help_action_needs_no_pipeline_or_agent() -> None:
    for f in _command_files():
        fm = _parse_frontmatter(f.read_text())
        if fm.get("action") == "help":
            # help is a meta-command; it neither runs a pipeline nor a single agent.
            assert not fm.get("pipeline"), f"{f.name}: help action must not declare pipeline"
            assert not fm.get("agent"), f"{f.name}: help action must not declare agent"
