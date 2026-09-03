"""Worker nesting controls.

Direct standalone workers are leaves by default. A worker may summon another
worker only when its prompt mode explicitly declares a `spawn_allowlist` and
the orchestration depth still allows it.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter
from tests.work_package_fixtures import (
    GOAL_CONTRACT,
    WORK_PACKAGE,
    spawn_contract_fields,
)


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


def _spawn(role: str, brief: str) -> LMResponse:
    selected_skill = {"coder": "python"}.get(role, role)
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": f"spawn-{role}", "name": "musubi_spawn_subagent",
        "input": {
            "role": role, "brief": brief, "pushed_skill_id": selected_skill,
            **spawn_contract_fields(),
        },
    }])


def _brief(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        c = m.get("content")
        if isinstance(c, str) and "## Brief" in c:
            return c
    return ""


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in messages
    )


class LeafCoderRouter(LMRouter):
    name = "nest"
    model = "nest-1"

    def __init__(self) -> None:
        self.coder_had_spawn: bool | None = None
        self.spawned = False
        self.work_package_committed = False

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        names = {t["name"] for t in tools}
        brief = _brief(messages)
        has_tr = _has_tool_result(messages)

        if brief:
            self.coder_had_spawn = "musubi_spawn_subagent" in names
            return _text("coded directly")
        if "musubi_begin_plan" in names:
            return LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use",
                "id": "mode-plan",
                "name": "musubi_begin_plan",
                "input": {"deliverable": "report.md"},
            }])
        if "musubi_commit_plan" in names:
            return LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use",
                "id": "commit-plan",
                "name": "musubi_commit_plan",
                "input": {
                    "plan_markdown": "# Plan\n\nCreate report.md.",
                    "change_manifest": {
                        "files_expected": 1, "subsystems": ["agent"],
                    },
                    "change_size": "small",
                    "worker_chain": ["coder"],
                    "goal_contract": GOAL_CONTRACT,
                },
            }])
        if "musubi_commit_work_package" in names and not self.work_package_committed:
            self.work_package_committed = True
            return LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use",
                "id": "commit-work-package",
                "name": "musubi_commit_work_package",
                "input": {"work_package": WORK_PACKAGE},
            }])
        if has_tr and not self.spawned:
            self.spawned = True
            return _spawn("coder", "implement X")
        if has_tr:
            return _text("done")
        return _text("done")


def test_direct_coder_worker_is_leaf_by_default() -> None:
    router = LeafCoderRouter()
    answer = asyncio.run(run_agent("create report.md", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    assert router.coder_had_spawn is False, "direct coder workers must be leaves by default"
