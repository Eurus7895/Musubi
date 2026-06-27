"""Depth-2 worker nesting (Increment 4b).

A worker whose role declares a `spawn_allowlist` may itself summon workers, up
to `max_depth`. Here the root spawns `coder` (depth 1), which spawns `explorer`
(depth 2). We assert the coder's tool surface gained the spawn tool (it nests)
while the explorer's did not (depth-2 leaf), and that the grandchild actually
ran and its result flowed back up.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


def _spawn(role: str, brief: str) -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": f"spawn-{role}", "name": "musubi_spawn_subagent",
        "input": {"role": role, "brief": brief},
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


class NestRouter(LMRouter):
    name = "nest"
    model = "nest-1"

    def __init__(self) -> None:
        self.coder_had_spawn: bool | None = None
        self.explorer_had_spawn: bool | None = None
        self.explorer_ran = False

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        names = {t["name"] for t in tools}
        brief = _brief(messages)
        has_tr = _has_tool_result(messages)

        if "implement X" in brief and not has_tr:
            self.coder_had_spawn = "musubi_spawn_subagent" in names
            return _spawn("explorer", "scan callers")
        if "scan callers" in brief:
            self.explorer_had_spawn = "musubi_spawn_subagent" in names
            self.explorer_ran = True
            return _text("explored: 3 callers")
        if "implement X" in brief and has_tr:
            return _text("coded using the explorer findings")
        if has_tr:
            return _text("done")
        return _spawn("coder", "implement X")


def test_worker_can_nest_one_level_and_grandchild_is_a_leaf() -> None:
    router = NestRouter()
    answer = asyncio.run(run_agent("ship a feature", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    assert router.explorer_ran, "the depth-2 explorer never ran"
    assert router.coder_had_spawn is True, "coder (depth 1) should be able to nest"
    assert router.explorer_had_spawn is False, "explorer (depth 2) must be a leaf"
