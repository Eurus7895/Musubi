"""Agent prompt resolver tests.

musubi-tier: substrate test - prompt lookup is the runtime boundary between
root, direct workers, pipeline stages, and meta agents.
"""

from __future__ import annotations

from pathlib import Path

from agent.prompt_resolver import (
    AgentPromptPurpose,
    read_agent_prompt,
    resolve_agent_prompt_path,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_worker_prompt_precedes_legacy_flat_prompt(tmp_path: Path) -> None:
    _write(tmp_path / ".github/agents/coder.agent.md", "legacy coder")
    _write(tmp_path / ".github/agents/workers/coder.agent.md", "direct coder")

    path = resolve_agent_prompt_path(
        [tmp_path], "coder", purpose=AgentPromptPurpose.WORKER,
    )

    assert path == tmp_path / ".github/agents/workers/coder.agent.md"
    assert read_agent_prompt([tmp_path], "coder", purpose=AgentPromptPurpose.WORKER) == "direct coder"


def test_pipeline_stage_prompt_precedes_prefixed_and_legacy_paths(tmp_path: Path) -> None:
    _write(tmp_path / ".github/agents/coder.agent.md", "legacy coder")
    _write(tmp_path / ".github/agents/feature-dev-coder.agent.md", "prefixed coder")
    _write(
        tmp_path / ".github/agents/pipeline-stages/feature-dev/coder.agent.md",
        "pipeline coder",
    )

    path = resolve_agent_prompt_path(
        [tmp_path],
        "coder",
        purpose=AgentPromptPurpose.PIPELINE_STAGE,
        pipeline_name="feature-dev",
    )

    assert path == tmp_path / ".github/agents/pipeline-stages/feature-dev/coder.agent.md"
    assert (
        read_agent_prompt(
            [tmp_path],
            "coder",
            purpose=AgentPromptPurpose.PIPELINE_STAGE,
            pipeline_name="feature-dev",
        )
        == "pipeline coder"
    )


def test_pipeline_stage_falls_back_to_legacy_prefixed_variant(tmp_path: Path) -> None:
    _write(tmp_path / ".github/agents/code-review-finder.agent.md", "prefixed finder")
    _write(tmp_path / ".github/agents/finder.agent.md", "legacy finder")

    path = resolve_agent_prompt_path(
        [tmp_path],
        "finder",
        purpose=AgentPromptPurpose.PIPELINE_STAGE,
        pipeline_name="code-review",
    )

    assert path == tmp_path / ".github/agents/code-review-finder.agent.md"


def test_invalid_prompt_names_do_not_escape_catalog(tmp_path: Path) -> None:
    _write(tmp_path / ".github/agents/workers/coder.agent.md", "direct coder")

    assert resolve_agent_prompt_path([tmp_path], "../coder", purpose=AgentPromptPurpose.WORKER) is None
    assert (
        resolve_agent_prompt_path(
            [tmp_path],
            "coder",
            purpose=AgentPromptPurpose.PIPELINE_STAGE,
            pipeline_name="../feature-dev",
        )
        is None
    )
