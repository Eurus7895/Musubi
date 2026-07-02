"""Agent prompt lookup by runtime purpose.

musubi-tier: substrate
expires-when: never - prompt purpose is part of the agent boundary contract.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Iterable


class AgentPromptPurpose(StrEnum):
    ROOT = "root"
    WORKER = "worker"
    PIPELINE_STAGE = "pipeline-stage"
    META = "meta"


def resolve_agent_prompt_path(
    roots: Iterable[Path],
    agent_name: str,
    *,
    purpose: AgentPromptPurpose | str,
    pipeline_name: str | None = None,
) -> Path | None:
    """Return the first prompt path for ``agent_name`` under ``roots``.

    New purpose-specific locations win, then legacy flat paths keep existing
    workspaces and installed bundles working during the catalog migration.
    """
    safe_agent = _safe_segment(agent_name)
    if safe_agent is None:
        return None
    purpose_key = AgentPromptPurpose(str(purpose))
    safe_pipeline = _safe_segment(pipeline_name) if pipeline_name else None
    if pipeline_name and safe_pipeline is None:
        return None

    for root in roots:
        if root is None:
            continue
        base = Path(root) / ".github" / "agents"
        for candidate in _candidates(
            base,
            safe_agent,
            purpose=purpose_key,
            pipeline_name=safe_pipeline,
        ):
            if candidate.is_file():
                return candidate
    return None


def read_agent_prompt(
    roots: Iterable[Path],
    agent_name: str,
    *,
    purpose: AgentPromptPurpose | str,
    pipeline_name: str | None = None,
) -> str:
    path = resolve_agent_prompt_path(
        roots, agent_name, purpose=purpose, pipeline_name=pipeline_name,
    )
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _candidates(
    base: Path,
    agent_name: str,
    *,
    purpose: AgentPromptPurpose,
    pipeline_name: str | None,
) -> list[Path]:
    filename = f"{agent_name}.agent.md"
    if purpose is AgentPromptPurpose.ROOT:
        return [base / "root" / filename, base / filename]
    if purpose is AgentPromptPurpose.WORKER:
        return [base / "workers" / filename, base / filename]
    if purpose is AgentPromptPurpose.META:
        return [base / "meta" / filename, base / filename]
    if pipeline_name:
        return [
            base / "pipeline-stages" / pipeline_name / filename,
            base / f"{pipeline_name}-{filename}",
            base / filename,
        ]
    return [base / "pipeline-stages" / filename, base / filename]


def _safe_segment(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "/" in raw or "\\" in raw or ".." in raw:
        return None
    return raw
