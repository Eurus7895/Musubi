"""Agent summons a pipeline (Increment 5).

A pipeline is an ordered recipe of workers. The agent calls
`musubi_spawn_pipeline`; the driver runs each stage as a worker, threading the
prior summary forward. Asserts the stages run in declared order and that the
evaluator (last stage) sees ONLY the prior stage's output — never the original
request or earlier stages (HI #3, generalised).
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


def _spawn_pipeline(name: str, brief: str) -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": "pl-1", "name": "musubi_spawn_pipeline",
        "input": {"pipeline_name": name, "brief": brief},
    }])


def _brief_text(messages: list[dict[str, Any]]) -> str | None:
    for m in messages:
        c = m.get("content")
        if isinstance(c, str) and "## Brief" in c:
            return c.split("## Brief", 1)[1]
    return None


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in messages
    )


class PipelineRouter(LMRouter):
    name = "pipeline"
    model = "pipeline-1"

    def __init__(self) -> None:
        self.order: list[str] = []
        self.reviewer_brief: str = ""

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        brief = _brief_text(messages)
        if brief is None:
            if _has_tool_result(messages):
                return _text("done")
            return _spawn_pipeline("feature-dev", "build a thing")
        if "Evaluate the output of the prior stage" in brief:
            self.reviewer_brief = brief
            self.order.append("reviewer")
            return _text("review: PASS")
        # Generator stages are told apart by how many prior summaries they see.
        n_prior = brief.count("### ")
        if n_prior == 0:
            self.order.append("planner")
            return _text("plan: step1, step2")
        if n_prior == 1:
            self.order.append("designer")
            return _text("design: moduleX")
        self.order.append("coder")
        return _text("code: wrote moduleX")


def test_agent_summons_pipeline_runs_stages_in_order_with_evaluator_firewall() -> None:
    router = PipelineRouter()
    answer = asyncio.run(run_agent("ship it via pipeline", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    assert router.order == ["planner", "designer", "coder", "reviewer"]

    # HI #3: the evaluator sees ONLY the immediately prior stage (coder), not the
    # original request or the earlier plan/design outputs.
    assert "code: wrote moduleX" in router.reviewer_brief
    assert "build a thing" not in router.reviewer_brief
    assert "plan: step1" not in router.reviewer_brief
    assert "design: moduleX" not in router.reviewer_brief
