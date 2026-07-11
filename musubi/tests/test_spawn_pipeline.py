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
import json
from pathlib import Path
from typing import Any

import pytest

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


def test_run_agent_pipeline_flag_runs_stages_directly() -> None:
    """`run_agent(..., pipeline="feature-dev")` runs the pipeline deterministically
    — the model never has to decide to spawn it (no musubi_spawn_pipeline tool_use
    from the router), yet the stages, order, and evaluator firewall are identical
    to the model-summoned path."""
    router = PipelineRouter()
    answer = asyncio.run(
        run_agent(
            "ship it", router, _musubi_dir(), log=io.StringIO(),
            pipeline="feature-dev",
        )
    )

    # Every stage ran, in declared order, without the model routing to it.
    assert router.order == ["planner", "designer", "coder", "reviewer"]
    # The final stage's summary is returned verbatim.
    assert "review: PASS" in answer
    # HI #3 holds under direct invocation: the evaluator sees only the prior
    # stage (coder), not the original task or earlier stage outputs.
    assert "code: wrote moduleX" in router.reviewer_brief
    assert "ship it" not in router.reviewer_brief
    assert "plan: step1" not in router.reviewer_brief
    assert "design: moduleX" not in router.reviewer_brief


def test_run_pipeline_strict_raises_on_spawn_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`strict=True` (the deterministic CLI path) turns a rejected spawn into a
    RuntimeError instead of returning the raw error text as a 'successful'
    answer. The default (model) path still returns the text so the model can
    react."""
    from agent import pipeline_runner

    async def fake_reject(session: Any, name: str, args: dict[str, Any]) -> str:
        return json.dumps({"status": "error", "error": "no such pipeline"})

    # run_pipeline imports _call_tool_text from agent.run at call time.
    monkeypatch.setattr("agent.run._call_tool_text", fake_reject)

    async def _strict() -> str:
        return await pipeline_runner.run_pipeline(
            None, {"pipeline_name": "x", "brief": "b"}, None, [],
            io.StringIO(), strict=True,
        )

    with pytest.raises(RuntimeError, match="spawn rejected"):
        asyncio.run(_strict())

    # Default (non-strict) path returns the raw text, unchanged.
    async def _lenient() -> str:
        return await pipeline_runner.run_pipeline(
            None, {"pipeline_name": "x", "brief": "b"}, None, [],
            io.StringIO(),
        )

    result = asyncio.run(_lenient())
    assert "no such pipeline" in result


def test_run_pipeline_finalizes_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import pipeline_runner

    finalizations: list[dict[str, Any]] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned",
                "pipeline_session_id": "pipe-1",
                "pipeline_name": "feature-dev",
                "plan": [
                    {"stage": "plan", "role": "planner"},
                    {"stage": "check", "role": "reviewer"},
                ],
            })
        if name == "musubi_spawn_pipeline_stage":
            return json.dumps({
                "status": "spawned",
                "handle_id": f"h-{args['stage']}",
                "role": "planner" if args["stage"] == "plan" else "reviewer",
                "allowed_tools": [],
            })
        if name == "musubi_complete_subagent":
            return json.dumps({"status": "ok"})
        if name == "musubi_finalize_pipeline_run":
            finalizations.append(args)
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    result = asyncio.run(pipeline_runner.run_pipeline(
        None,
        {
            "parent_session_id": "outer",
            "parent_agent_name": "agent",
            "pipeline_name": "feature-dev",
            "brief": "ship it",
        },
        PipelineRouter(),
        [],
        io.StringIO(),
        strict=True,
    ))

    assert "review: PASS" in result
    assert finalizations == [{
        "session_id": "pipe-1",
        "final_status": "success",
        "escalated": False,
    }]


def test_run_pipeline_finalizes_aborted_stage_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import pipeline_runner

    finalizations: list[dict[str, Any]] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned",
                "pipeline_session_id": "pipe-2",
                "pipeline_name": "feature-dev",
                "plan": [
                    {"stage": "plan", "role": "planner"},
                    {"stage": "check", "role": "reviewer"},
                ],
            })
        if name == "musubi_spawn_pipeline_stage":
            return json.dumps({"status": "error", "error": "policy denied"})
        if name == "musubi_finalize_pipeline_run":
            finalizations.append(args)
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)

    with pytest.raises(RuntimeError, match="could not start"):
        asyncio.run(pipeline_runner.run_pipeline(
            None,
            {
                "parent_session_id": "outer",
                "parent_agent_name": "agent",
                "pipeline_name": "feature-dev",
                "brief": "ship it",
            },
            PipelineRouter(),
            [],
            io.StringIO(),
            strict=True,
        ))

    assert finalizations == [{
        "session_id": "pipe-2",
        "final_status": "aborted",
        "escalated": False,
    }]


def test_run_pipeline_aborts_truncated_write_without_dispatching_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A max-token write call is incomplete, never a successful stage."""
    from agent import pipeline_runner

    completions: list[dict[str, Any]] = []
    finalizations: list[dict[str, Any]] = []

    class TruncatedWriteRouter(LMRouter):
        name = "truncated"
        model = "truncated-1"

        def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001, ARG002
            return LMResponse(stop_reason="max_tokens", content=[{
                "type": "tool_use", "id": "partial-write",
                "name": "musubi_write_file",
                "input": {"path": "dashboard.html", "content": "<html>"},
            }])

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned", "pipeline_session_id": "pipe-truncated",
                "pipeline_name": "feature-dev",
                "plan": [{"stage": "code", "role": "coder"}],
            })
        if name == "musubi_spawn_pipeline_stage":
            return json.dumps({
                "status": "spawned", "handle_id": "h-code", "role": "coder",
                "allowed_tools": [],
            })
        if name == "musubi_complete_subagent":
            completions.append(args)
            return json.dumps({"status": "ok"})
        if name == "musubi_finalize_pipeline_run":
            finalizations.append(args)
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    result = asyncio.run(pipeline_runner.run_pipeline(
        None,
        {"parent_session_id": "outer", "parent_agent_name": "agent", "pipeline_name": "feature-dev", "brief": "make dashboard"},
        TruncatedWriteRouter(), [], io.StringIO(), strict=False,
    ))

    assert "[blocked] " in result
    assert completions[0]["status"] == "failed"
    assert finalizations == [{
        "session_id": "pipe-truncated", "final_status": "aborted", "escalated": False,
    }]
