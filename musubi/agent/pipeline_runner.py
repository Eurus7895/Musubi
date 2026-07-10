"""Pipeline runner for the standalone agent — a pipeline as a recipe of workers.

musubi-tier: ephemeral
expires-when: models orchestrate multi-step pipelines natively
cost-lever: deletes the driver-side stage sequencer (~90 lines)

A pipeline is an ordered chain of workers (composer reads the chain from
`.github/pipelines/<name>/pipeline.yaml`). When the model calls
`musubi_spawn_pipeline`, this runner:

    spawn_pipeline (open child session + plan)
      → for each stage in order:
          spawn_pipeline_stage (authorise by membership, get role + tools)
          → build the stage worker prompt from its brief
          → run a turn-capped worker loop (run_unit)
          → complete_subagent (audit the worker)
      → return the final stage's summary to the summoning agent

The brief threads forward: generator stages see the request plus the prior
summaries; the evaluator (last stage) sees ONLY the immediately prior stage's
output — the HI #3 firewall, generalised to any pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Per-stage cycle cap for a pipeline worker. Stages are single-purpose, so a
#: small budget keeps a runaway stage from burning the whole pipeline. In the
#: standalone runner a stage self-explores via grep/glob before doing its work
#: (there is no harness-injected workspace tree), so the cap leaves a few
#: cycles for discovery on top of the stage's real output.
DEFAULT_STAGE_MAX_CYCLES = 12


async def run_pipeline(
    session: Any,
    spawn_args: dict[str, Any],
    vendor: Any,
    tools: list[dict[str, Any]],
    log: Any,
    *,
    agents_dir: Path | None = None,
    compression_db_path: Path | None = None,
    budget: Any = None,
    stats: Any = None,
    audit_db_path: Path | None = None,
    strict: bool = False,
) -> str:
    """Summon and run one pipeline. Returns the final stage's summary text.

    `spawn_args` is the model's `musubi_spawn_pipeline` input with
    `parent_session_id` / `parent_agent_name` already injected by the caller.
    Any harness-side rejection is returned verbatim so the model can react —
    unless `strict` is set, in which case a rejected spawn or stage raises
    `RuntimeError`. Callers with no model loop to react (e.g. the deterministic
    `agent --pipeline` CLI) pass `strict=True` so a failure is a nonzero exit,
    not a "successful" answer.
    """
    from agent.run import _call_tool_text, run_unit
    from agent.subagent import (
        _read_agent_md,
        build_subagent_system_prompt,
        select_child_tools,
    )

    raw = await _call_tool_text(session, "musubi_spawn_pipeline", spawn_args)
    spawned = _loads(raw)
    if spawned.get("status") != "spawned":
        if strict:
            raise RuntimeError(f"pipeline spawn rejected: {raw}")
        return raw
    psid = str(spawned.get("pipeline_session_id", ""))
    pname = str(spawned.get("pipeline_name", ""))
    plan = spawned.get("plan") or []
    request = str(spawn_args.get("brief", ""))

    print(f"[agent]   ⇶ pipeline {pname} ({len(plan)} stages)", file=log)

    summaries: list[str] = []
    pipeline_escalated = False
    for i, step in enumerate(plan):
        stage = str(step.get("stage", ""))
        role = str(step.get("role", ""))
        brief = _stage_brief(request, summaries, i, len(plan))

        stage_raw = await _call_tool_text(session, "musubi_spawn_pipeline_stage", {
            "pipeline_session_id": psid, "pipeline_name": pname,
            "stage": stage, "brief": brief,
        })
        st = _loads(stage_raw)
        if st.get("status") != "spawned":
            msg = f"[pipeline {pname}] stage {stage!r} could not start: {stage_raw}"
            await _finalize_pipeline(session, psid, "aborted", False)
            if strict:
                raise RuntimeError(msg)
            return msg
        handle_id = str(st.get("handle_id", ""))
        allowed = st.get("allowed_tools") or []

        agent_md = _read_agent_md(role, agents_dir)
        system_prompt = build_subagent_system_prompt(agent_md, None, brief)
        child_tools = select_child_tools(tools, allowed)
        print(
            f"[agent]     ⮑ stage {stage} (role={role}, tools={len(child_tools)})",
            file=log,
        )

        try:
            answer, turns = await run_unit(
                session, vendor, child_tools,
                system_prompt=system_prompt, user_message=None,
                max_cycles=DEFAULT_STAGE_MAX_CYCLES, log=log,
                compression_db_path=compression_db_path,
                role=role,
                stats=stats,
                budget=budget,
                audit_db_path=audit_db_path,
            )
        except Exception as exc:
            is_budget = type(exc).__name__ in {
                "BudgetExhaustedError",
                "TokenBudgetExhaustedError",
            }
            if is_budget:
                await _call_tool_text(session, "musubi_complete_subagent", {
                    "handle_id": handle_id,
                    "summary": f"[stage {stage}] budget exhausted: {exc}",
                    "turns": 0,
                    "status": "escalated",
                })
            await _finalize_pipeline(
                session, psid,
                "escalated" if is_budget else "aborted",
                is_budget,
            )
            raise
        status = "done" if answer is not None else "escalated"
        if answer is None:
            pipeline_escalated = True
            answer = f"[stage {stage}] exceeded {DEFAULT_STAGE_MAX_CYCLES} cycles"

        await _call_tool_text(session, "musubi_complete_subagent", {
            "handle_id": handle_id, "summary": answer, "turns": turns, "status": status,
        })
        summaries.append(f"### {stage}\n{answer}")

    await _finalize_pipeline(
        session, psid,
        "escalated" if pipeline_escalated else "success",
        pipeline_escalated,
    )
    return summaries[-1] if summaries else f"[pipeline {pname}] produced no output"


async def _finalize_pipeline(
    session: Any,
    session_id: str,
    final_status: str,
    escalated: bool,
) -> None:
    from agent.run import _call_tool_text

    await _call_tool_text(session, "musubi_finalize_pipeline_run", {
        "session_id": session_id,
        "final_status": final_status,
        "escalated": escalated,
    })


def _stage_brief(request: str, summaries: list[str], idx: int, total: int) -> str:
    """Brief for stage `idx`. Stage 0 gets the request; the last stage (the
    evaluator) sees ONLY the prior stage's output (HI #3); middle stages see the
    request plus all prior summaries."""
    if idx == 0:
        return request
    if idx == total - 1:
        return (
            "Evaluate the output of the prior stage. You see only this stage — "
            "not the original request or earlier stages.\n\n" + summaries[-1]
        )
    joined = "\n\n".join(summaries)
    return f"{request}\n\n## Prior stage outputs\n\n{joined}"


def _loads(raw: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}
