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
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok",
                "brief": "b",
                "role": "planner",
                "role_skill": None,
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


def test_stage_without_role_prompt_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage whose role has no prompt in workers/ or
    pipeline-stages/<pipeline>/ must fail the pipeline, not run silently
    on an empty prompt. The stage is audited as failed and the run
    finalised as aborted."""
    from agent import pipeline_runner

    completions: list[dict[str, Any]] = []
    finalizations: list[dict[str, Any]] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned", "pipeline_session_id": "pipe-ghost",
                "pipeline_name": "ghost-pipe",
                "plan": [{"stage": "scan", "role": "phantom"}],
            })
        if name == "musubi_spawn_pipeline_stage":
            return json.dumps({
                "status": "spawned", "handle_id": "h-scan", "role": "phantom",
                "allowed_tools": [],
            })
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok", "brief": "b", "role": "phantom",
                "role_skill": None, "allowed_tools": [],
            })
        if name == "musubi_complete_subagent":
            completions.append(args)
            return json.dumps({"status": "ok"})
        if name == "musubi_finalize_pipeline_run":
            finalizations.append(args)
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)

    with pytest.raises(RuntimeError, match="no role prompt"):
        asyncio.run(pipeline_runner.run_pipeline(
            None,
            {"parent_session_id": "outer", "parent_agent_name": "agent",
             "pipeline_name": "ghost-pipe", "brief": "scan it"},
            PipelineRouter(), [], io.StringIO(), strict=True,
        ))

    assert completions and completions[0]["status"] == "failed"
    assert finalizations == [{
        "session_id": "pipe-ghost", "final_status": "aborted", "escalated": False,
    }]


def test_stage_gets_role_skill_pushed_into_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HI #2: the spawn context's role_skill is embedded into the stage
    worker's system prompt — the same push path a direct worker takes."""
    from agent import pipeline_runner

    captured_prompts: list[str] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned", "pipeline_session_id": "pipe-skill",
                "pipeline_name": "feature-dev",
                "plan": [
                    {"stage": "plan", "role": "planner"},
                    {"stage": "check", "role": "reviewer"},
                ],
            })
        if name == "musubi_spawn_pipeline_stage":
            return json.dumps({
                "status": "spawned", "handle_id": f"h-{args['stage']}",
                "role": "planner" if args["stage"] == "plan" else "reviewer",
                "allowed_tools": [],
            })
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok", "brief": "b", "role": "planner",
                "role_skill": "---\nname: x\n---\nSKILL-CONTENT-XYZ",
                "allowed_tools": [],
            })
        if name in ("musubi_complete_subagent", "musubi_finalize_pipeline_run"):
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    async def fake_run_unit(*args: Any, **kwargs: Any) -> tuple[str, int]:
        captured_prompts.append(kwargs["system_prompt"])
        return "stage done", 1

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    monkeypatch.setattr("agent.run.run_unit", fake_run_unit)

    asyncio.run(pipeline_runner.run_pipeline(
        None,
        {"parent_session_id": "outer", "parent_agent_name": "agent",
         "pipeline_name": "feature-dev", "brief": "ship it"},
        PipelineRouter(), [], io.StringIO(), strict=True,
    ))

    assert len(captured_prompts) == 2
    for prompt in captured_prompts:
        assert "SKILL-CONTENT-XYZ" in prompt, "role_skill not pushed into stage prompt"
        assert "## Skill (pushed by harness)" in prompt


def test_pipeline_stage_threads_frontmatter_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipeline stages honor the same per-worker output cap as direct spawns."""
    from agent import pipeline_runner

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned",
                "pipeline_session_id": "pipe-budget",
                "pipeline_name": "feature-dev",
                "plan": [{"stage": "code", "role": "coder"}],
            })
        if name == "musubi_spawn_pipeline_stage":
            return json.dumps({
                "status": "spawned",
                "handle_id": "h-code",
                "role": "coder",
                "allowed_tools": [],
            })
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok",
                "brief": "write it",
                "role": "coder",
                "role_skill": None,
                "allowed_tools": [],
            })
        if name in ("musubi_complete_subagent", "musubi_finalize_pipeline_run"):
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    seen: dict[str, Any] = {}

    async def fake_run_unit(*args: Any, **kwargs: Any) -> tuple[str, int]:
        seen.update(kwargs)
        return "stage done", 1

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    monkeypatch.setattr("agent.run.run_unit", fake_run_unit)
    monkeypatch.setattr(
        pipeline_runner,
        "_read_stage_agent_md",
        lambda role, pipeline_name, agents_dir: (
            "---\nname: coder\nmaxOutputTokens: 32768\n---\n# Coder"
        ),
    )

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

    assert seen["worker_max_output"] == 32768
    assert seen["audit_session_id"] == "pipe-budget"
    assert seen["audit_worker_id"] == "h-code"
    assert seen["audit_stage"] == "code"


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
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok", "brief": "b", "role": "coder",
                "role_skill": None, "allowed_tools": [],
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


# ── PipelineWorkerSpec: validated stage contract resolved before spawn ──────


def _write_worker(agents_dir: Path, role: str, body: str) -> None:
    workers = agents_dir / ".github" / "agents" / "workers"
    workers.mkdir(parents=True, exist_ok=True)
    (workers / f"{role}.agent.md").write_text(body, encoding="utf-8")


def test_resolve_pipeline_worker_spec_reads_maxturns_and_budget(tmp_path: Path) -> None:
    from agent.pipeline_runner import PIPELINE_CONTEXT_BUDGET, resolve_pipeline_worker_spec

    _write_worker(
        tmp_path, "planner",
        "---\nname: Planner\nmaxTurns: 4\ntools: [Read, View]\n---\n"
        "Return a compact implementation plan.\n",
    )
    agents_dir = tmp_path / ".github" / "agents"
    spec = resolve_pipeline_worker_spec("planner", "feature-dev", agents_dir)
    assert spec.role == "planner"
    assert spec.max_cycles == 4
    assert spec.context_budget_chars == PIPELINE_CONTEXT_BUDGET == 16_000
    assert "compact implementation plan" in spec.prompt
    assert spec.worker_max_output is None


def test_resolve_pipeline_worker_spec_defaults_absent_maxturns(tmp_path: Path) -> None:
    from agent.pipeline_runner import DEFAULT_STAGE_MAX_CYCLES, resolve_pipeline_worker_spec

    _write_worker(tmp_path, "planner", "---\nname: Planner\n---\nPlan it.\n")
    spec = resolve_pipeline_worker_spec(
        "planner", "feature-dev", tmp_path / ".github" / "agents",
    )
    assert spec.max_cycles == DEFAULT_STAGE_MAX_CYCLES == 12


def test_resolve_pipeline_worker_spec_carries_output_cap(tmp_path: Path) -> None:
    from agent.pipeline_runner import resolve_pipeline_worker_spec

    _write_worker(
        tmp_path, "coder",
        "---\nname: Coder\nmaxTurns: 6\nmaxOutputTokens: 32768\n---\nWrite it.\n",
    )
    spec = resolve_pipeline_worker_spec(
        "coder", "feature-dev", tmp_path / ".github" / "agents",
    )
    assert spec.max_cycles == 6
    assert spec.worker_max_output == 32768


def test_resolve_pipeline_worker_spec_missing_prompt_fails_closed(tmp_path: Path) -> None:
    from agent.pipeline_runner import resolve_pipeline_worker_spec

    with pytest.raises(RuntimeError, match="no role prompt"):
        resolve_pipeline_worker_spec(
            "ghost", "feature-dev", tmp_path / ".github" / "agents",
        )


def test_resolve_pipeline_worker_spec_rejects_zero_maxturns(tmp_path: Path) -> None:
    from agent.pipeline_runner import resolve_pipeline_worker_spec

    _write_worker(tmp_path, "planner", "---\nname: Planner\nmaxTurns: 0\n---\nPlan.\n")
    with pytest.raises(RuntimeError, match="maxTurns"):
        resolve_pipeline_worker_spec(
            "planner", "feature-dev", tmp_path / ".github" / "agents",
        )


def test_resolve_pipeline_worker_spec_rejects_noninteger_maxturns(tmp_path: Path) -> None:
    from agent.pipeline_runner import resolve_pipeline_worker_spec

    _write_worker(tmp_path, "planner", "---\nname: Planner\nmaxTurns: many\n---\nPlan.\n")
    with pytest.raises(RuntimeError, match="maxTurns"):
        resolve_pipeline_worker_spec(
            "planner", "feature-dev", tmp_path / ".github" / "agents",
        )


def test_resolve_pipeline_worker_spec_rejects_over_ceiling_maxturns(tmp_path: Path) -> None:
    from agent.pipeline_runner import resolve_pipeline_worker_spec

    _write_worker(tmp_path, "planner", "---\nname: Planner\nmaxTurns: 99\n---\nPlan.\n")
    with pytest.raises(RuntimeError, match="maxTurns"):
        resolve_pipeline_worker_spec(
            "planner", "feature-dev", tmp_path / ".github" / "agents",
        )


def test_resolve_pipeline_worker_spec_reads_pipeline_stage_variant(tmp_path: Path) -> None:
    """A role that exists only as a pipeline-stage prompt still resolves."""
    from agent.pipeline_runner import resolve_pipeline_worker_spec

    stages = tmp_path / ".github" / "agents" / "pipeline-stages" / "code-review"
    stages.mkdir(parents=True, exist_ok=True)
    (stages / "scoper.agent.md").write_text(
        "---\nname: Scoper\nmaxTurns: 4\n---\nScope the diff.\n", encoding="utf-8",
    )
    spec = resolve_pipeline_worker_spec(
        "scoper", "code-review", tmp_path / ".github" / "agents",
    )
    assert spec.max_cycles == 4
    assert "Scope the diff" in spec.prompt


# ── Feature A: stage nesting — spawn_roles gates the spawn tool ─────────────


def _nesting_fakes(
    monkeypatch: pytest.MonkeyPatch,
    spawn_roles_by_stage: dict[str, list[str] | None],
) -> list[dict[str, Any]]:
    """Fake the MCP calls for a 2-stage feature-dev run and capture every
    run_unit invocation (args + kwargs). `spawn_roles_by_stage` maps stage →
    spawn_roles value (None = field absent, as an older server would return)."""
    captured: list[dict[str, Any]] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned", "pipeline_session_id": "pipe-nest",
                "pipeline_name": "feature-dev",
                "plan": [
                    {"stage": "code", "role": "coder"},
                    {"stage": "check", "role": "reviewer"},
                ],
            })
        if name == "musubi_spawn_pipeline_stage":
            stage = args["stage"]
            resp: dict[str, Any] = {
                "status": "spawned", "handle_id": f"h-{stage}",
                "role": "coder" if stage == "code" else "reviewer",
                "allowed_tools": [],
            }
            roles = spawn_roles_by_stage.get(stage)
            if roles is not None:
                resp["spawn_roles"] = roles
            return json.dumps(resp)
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok", "brief": "b", "role": "coder",
                "role_skill": None, "allowed_tools": [],
            })
        if name in ("musubi_complete_subagent", "musubi_finalize_pipeline_run"):
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    async def fake_run_unit(*args: Any, **kwargs: Any) -> tuple[str, int]:
        captured.append({"tools": args[2], **kwargs})
        return "stage done", 1

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    monkeypatch.setattr("agent.run.run_unit", fake_run_unit)
    return captured


_CATALOG = [
    {"name": "musubi_read_file"},
    {"name": "musubi_spawn_subagent"},
    {"name": "musubi_write_file"},
]


def _run(orchestration: Any, captured_via: list[dict[str, Any]]) -> None:
    from agent import pipeline_runner

    asyncio.run(pipeline_runner.run_pipeline(
        None,
        {"parent_session_id": "outer", "parent_agent_name": "agent",
         "pipeline_name": "feature-dev", "brief": "ship it"},
        PipelineRouter(), _CATALOG, io.StringIO(), strict=True,
        orchestration=orchestration,
    ))


def test_stage_with_spawn_roles_gets_spawn_tool_and_stage_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage whose server response declares spawn_roles is handed the spawn
    tool and an orchestration parented on the PIPELINE session (so the server
    narrows its spawns per pipeline — HI #5), one level deeper than the
    caller."""
    from agent.run import Orchestration

    captured = _nesting_fakes(monkeypatch, {"code": ["explorer"], "check": []})
    _run(Orchestration(parent_session_id="root-sid"), captured)

    coder, reviewer = captured
    coder_orch = coder["orchestration"]
    assert coder_orch is not None
    assert coder_orch.parent_session_id == "pipe-nest"   # NOT root-sid
    assert coder_orch.parent_agent_name == "coder"
    assert coder_orch.depth == 1                          # caller depth 0 + 1
    assert any(t["name"] == "musubi_spawn_subagent" for t in coder["tools"])
    assert coder["spawn_catalog"] == _CATALOG

    # check stage declared [] → leaf.
    assert reviewer["orchestration"] is None
    assert not any(
        t["name"] == "musubi_spawn_subagent" for t in reviewer["tools"]
    )


def test_stage_without_spawn_roles_field_stays_leaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older server that returns no spawn_roles field degrades to today's
    strict leaves — fail-closed compatibility."""
    from agent.run import Orchestration

    captured = _nesting_fakes(monkeypatch, {"code": None, "check": None})
    _run(Orchestration(parent_session_id="root-sid"), captured)

    for stage in captured:
        assert stage["orchestration"] is None
        assert stage["spawn_catalog"] is None
        assert not any(
            t["name"] == "musubi_spawn_subagent" for t in stage["tools"]
        )


def test_stage_leaf_when_caller_out_of_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller already at max depth cannot grant stages a deeper level, even
    when the server declares spawn_roles."""
    from agent.run import Orchestration

    captured = _nesting_fakes(
        monkeypatch, {"code": ["explorer"], "check": ["reviewer-aux"]},
    )
    _run(
        Orchestration(parent_session_id="root-sid", depth=2, max_depth=2),
        captured,
    )

    for stage in captured:
        assert stage["orchestration"] is None


def test_run_pipeline_without_orchestration_keeps_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default orchestration=None keeps every stage a strict leaf even when
    the server advertises spawn_roles — existing callers are unaffected."""
    captured = _nesting_fakes(
        monkeypatch, {"code": ["explorer"], "check": ["reviewer-aux"]},
    )
    _run(None, captured)

    for stage in captured:
        assert stage["orchestration"] is None
        assert not any(
            t["name"] == "musubi_spawn_subagent" for t in stage["tools"]
        )


# ── Feature A: end-to-end through the real server ───────────────────────────


class NestingRouter(LMRouter):
    """Canned responses in execution order; records each call's tool names."""

    name = "nesting"
    model = "nesting-1"

    def __init__(self, responses: list[LMResponse]) -> None:
        self._responses = list(responses)
        self.tool_surfaces: list[set[str]] = []

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        self.tool_surfaces.append({t["name"] for t in tools})
        if not self._responses:
            raise AssertionError("NestingRouter ran out of canned responses")
        return self._responses.pop(0)


def _spawn_worker(role: str, brief: str) -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": "sp-1", "name": "musubi_spawn_subagent",
        "input": {"role": role, "brief": brief},
    }])


def test_pipeline_stage_spawns_declared_worker_end_to_end() -> None:
    """feature-dev's evaluator declares spawns: [reviewer-aux]. Through the
    real server: the reviewer stage carries the spawn tool, its reviewer-aux
    child runs as a leaf (no spawn tool), and the aux verdict is fed back
    before the stage's final answer."""
    router = NestingRouter([
        _text("plan: step1"),                       # planner stage
        _text("design: moduleX"),                   # designer stage
        _text("code: wrote moduleX"),               # coder stage
        _spawn_worker("reviewer-aux", "check moduleX"),  # reviewer stage c0
        _text("aux: file verdict OK"),              # reviewer-aux child
        _text("review: PASS"),                      # reviewer stage c1
    ])
    answer = asyncio.run(run_agent(
        "ship it", router, _musubi_dir(), log=io.StringIO(),
        pipeline="feature-dev",
    ))

    assert "review: PASS" in answer
    assert len(router.tool_surfaces) == 6
    # planner/designer declare no spawns → leaves; coder declares
    # [explorer, investigator] and the evaluator [reviewer-aux] → both nest.
    for i in (0, 1):
        assert "musubi_spawn_subagent" not in router.tool_surfaces[i], i
    assert "musubi_spawn_subagent" in router.tool_surfaces[2]  # coder
    assert "musubi_spawn_subagent" in router.tool_surfaces[3]  # reviewer
    # The reviewer-aux child (call 4) sits at max depth → leaf.
    assert "musubi_spawn_subagent" not in router.tool_surfaces[4]


def test_spawn_pipeline_stage_returns_effective_spawn_roles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Server-level: spawn_roles = pipeline.yaml spawns ∩ firewall,
    fail-closed [] for stages that declare nothing."""
    import server
    from session import state
    from storage import db, subagent_audit

    p = tmp_path / "harness.db"
    db.init_db(p)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", p)
    monkeypatch.setattr(subagent_audit, "_DEFAULT_AUDIT_DB", p)

    outer = state.create_session("outer", p)
    spawned = json.loads(server.musubi_spawn_pipeline(
        parent_session_id=outer, parent_agent_name="agent",
        pipeline_name="feature-dev", brief="ship it",
    ))
    assert spawned["status"] == "spawned"
    psid = spawned["pipeline_session_id"]

    code = json.loads(server.musubi_spawn_pipeline_stage(
        pipeline_session_id=psid, pipeline_name="feature-dev",
        stage="code", brief="b",
    ))
    assert code["spawn_roles"] == ["explorer", "investigator"]

    plan = json.loads(server.musubi_spawn_pipeline_stage(
        pipeline_session_id=psid, pipeline_name="feature-dev",
        stage="plan", brief="b",
    ))
    assert plan["spawn_roles"] == []
