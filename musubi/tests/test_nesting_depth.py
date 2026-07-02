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


class LeafCoderRouter(LMRouter):
    name = "nest"
    model = "nest-1"

    def __init__(self) -> None:
        self.coder_had_spawn: bool | None = None

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        names = {t["name"] for t in tools}
        brief = _brief(messages)
        has_tr = _has_tool_result(messages)

        if "implement X" in brief:
            self.coder_had_spawn = "musubi_spawn_subagent" in names
            return _text("coded directly")
        if has_tr:
            return _text("done")
        return _spawn("coder", "implement X")


def test_direct_coder_worker_is_leaf_by_default() -> None:
    router = LeafCoderRouter()
    answer = asyncio.run(run_agent("ship a feature", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    assert router.coder_had_spawn is False, "direct coder workers must be leaves by default"
