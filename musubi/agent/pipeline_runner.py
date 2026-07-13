"""Pipeline runner for the standalone agent — a pipeline as a recipe of workers.

musubi-tier: ephemeral
expires-when: models orchestrate multi-step pipelines natively
cost-lever: deletes the driver-side stage sequencer (~90 lines)

A pipeline is an ordered chain of workers (composer reads the chain from
`.github/pipelines/<name>/pipeline.yaml`). When the model calls
`musubi_spawn_pipeline`, this runner:

    spawn_pipeline (open child session + plan)
      → for each stage in order:
          spawn_pipeline_stage (authorise by membership)
          → get_subagent_context (firewalled brief + role skill + tools — the
            same HI #2 push path every direct worker takes)
          → resolve the role prompt (workers/ first, then
            pipeline-stages/<pipeline>/; NO prompt → the stage fails closed,
            it never runs on an empty prompt)
          → run a turn-capped worker loop (run_unit)
          → complete_subagent (audit the worker)
      → return the final stage's summary to the summoning agent

The brief threads forward: generator stages see the request plus the prior
summaries; the evaluator (last stage) sees ONLY the immediately prior stage's
output — the HI #3 firewall, generalised to any pipeline.

Stage nesting: when the caller passes its `Orchestration` and the server's
stage response carries a non-empty `spawn_roles` (pipeline.yaml `spawns:` ∩
the role's firewall), the stage worker is handed the spawn tool one level
deeper — context offloading within a stage (coder → explorer, synthesizer →
reviewer-aux per file). No orchestration, an empty/missing `spawn_roles`, or
an exhausted depth budget all degrade to a strict leaf, fail-closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Per-stage cycle cap for a pipeline worker. Stages are single-purpose, so a
#: small budget keeps a runaway stage from burning the whole pipeline. In the
#: standalone runner a stage self-explores via grep/glob before doing its work
#: (there is no harness-injected workspace tree), so the cap leaves a few
#: cycles for discovery on top of the stage's real output. It is also the
#: upper bound a worker may raise its own cap to via `maxTurns:` frontmatter —
#: a stage never runs longer than this shared ceiling.
DEFAULT_STAGE_MAX_CYCLES = 12
MAX_STAGE_TURNS = DEFAULT_STAGE_MAX_CYCLES

# Pipeline stages are deliberately narrower than a root-agent turn. Keeping
# their context below the root default limits repeated glob/grep/file reads
# from consuming the shared run budget before later stages can execute.
PIPELINE_CONTEXT_BUDGET = 16_000


@dataclass(frozen=True)
class PipelineWorkerSpec:
    """Validated contract for one pipeline stage worker.

    Resolved once, before the stage is spawned, so a single cap flows through
    the spawn row, the runtime loop, and the completion audit. `prompt` is the
    canonical worker prompt (frontmatter intact; the system-prompt builder
    strips it). `max_cycles` is the declared `maxTurns` clamped to
    `[1, MAX_STAGE_TURNS]`; `worker_max_output` is the optional per-worker
    output-token cap.
    """

    role: str
    prompt: str
    max_cycles: int
    context_budget_chars: int = PIPELINE_CONTEXT_BUDGET
    worker_max_output: int | None = None


def _validated_max_turns(value: Any) -> int:
    """Clamp a declared `maxTurns` to `[1, MAX_STAGE_TURNS]`, fail-closed.

    Absent → the shared default. Present-but-invalid (non-int, bool, <=0, or
    above the shared ceiling) raises so a bad contract fails the stage before
    it ever spawns, rather than silently running an unbounded or zero-turn
    worker.
    """
    if value is None:
        return DEFAULT_STAGE_MAX_CYCLES
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(
            f"has invalid maxTurns {value!r}: expected an integer 1..{MAX_STAGE_TURNS}"
        )
    if not 1 <= value <= MAX_STAGE_TURNS:
        raise RuntimeError(
            f"has out-of-range maxTurns {value}: expected 1..{MAX_STAGE_TURNS}"
        )
    return value


def resolve_pipeline_worker_spec(
    role: str,
    pipeline_name: str,
    agents_dir: Path | None = None,
) -> PipelineWorkerSpec:
    """Resolve + validate one stage worker's contract before spawn.

    Raises `RuntimeError` (fail-closed) when the role has no prompt or declares
    invalid `maxTurns`. The message is role/prompt-specific so the runner can
    surface it verbatim and finalize the pipeline aborted.
    """
    from agent.subagent import _frontmatter_max_output_tokens, frontmatter_dict

    agent_md = _read_stage_agent_md(role, pipeline_name, agents_dir)
    if not agent_md.strip():
        raise RuntimeError(
            f"has no role prompt: expected .github/agents/workers/{role}.agent.md "
            f"or .github/agents/pipeline-stages/{pipeline_name}/{role}.agent.md"
        )
    max_cycles = _validated_max_turns(frontmatter_dict(agent_md).get("maxTurns"))
    return PipelineWorkerSpec(
        role=role,
        prompt=agent_md,
        max_cycles=max_cycles,
        context_budget_chars=PIPELINE_CONTEXT_BUDGET,
        worker_max_output=_frontmatter_max_output_tokens(agent_md),
    )


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
    orchestration: Any = None,
) -> str:
    """Summon and run one pipeline. Returns the final stage's summary text.

    `spawn_args` is the model's `musubi_spawn_pipeline` input with
    `parent_session_id` / `parent_agent_name` already injected by the caller.
    Any harness-side rejection is returned verbatim so the model can react —
    unless `strict` is set, in which case a rejected spawn or stage raises
    `RuntimeError`. Callers with no model loop to react (e.g. the deterministic
    `agent --pipeline` CLI) pass `strict=True` so a failure is a nonzero exit,
    not a "successful" answer.

    `orchestration` is the caller's `Orchestration`; when provided and the
    depth budget allows, stages whose server response declares `spawn_roles`
    may nest (see module docstring). `None` keeps every stage a strict leaf.
    """
    from agent.run import _call_tool_text, run_unit
    from agent.subagent import build_subagent_system_prompt, select_child_tools

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

        # Resolve + validate the worker contract BEFORE spawning: a missing
        # role prompt or a bad `maxTurns` fails the pipeline closed without ever
        # opening a stage handle. `spec.max_cycles` is the ONE cap that flows
        # into the spawn row, the runtime loop, and the completion audit — no
        # more runner-hard-codes-12 while the server defaults to eight.
        try:
            spec = resolve_pipeline_worker_spec(role, pname, agents_dir)
        except RuntimeError as exc:
            await _finalize_pipeline(session, psid, "aborted", False)
            msg = f"[pipeline {pname}] stage {stage!r} {exc}"
            if strict:
                raise RuntimeError(msg) from exc
            return msg

        stage_raw = await _call_tool_text(session, "musubi_spawn_pipeline_stage", {
            "pipeline_session_id": psid, "pipeline_name": pname,
            "stage": stage, "brief": brief, "max_turns": spec.max_cycles,
        })
        st = _loads(stage_raw)
        if st.get("status") != "spawned":
            msg = f"[pipeline {pname}] stage {stage!r} could not start: {stage_raw}"
            await _finalize_pipeline(session, psid, "aborted", False)
            if strict:
                raise RuntimeError(msg)
            return msg
        handle_id = str(st.get("handle_id", ""))
        # The server echoes the cap it recorded in the spawn row + audit. If it
        # ever diverges from the requested spec, the run would silently audit a
        # different cap than it enforces — fail the stage closed instead.
        recorded_cap = st.get("max_turns")
        if recorded_cap is not None and int(recorded_cap) != spec.max_cycles:
            await _call_tool_text(session, "musubi_complete_subagent", {
                "handle_id": handle_id,
                "summary": (
                    f"[stage {stage}] recorded cap {recorded_cap} != "
                    f"requested {spec.max_cycles}"
                ),
                "turns": 0,
                "status": "failed",
            })
            await _finalize_pipeline(session, psid, "aborted", False)
            msg = (
                f"[pipeline {pname}] stage {stage!r} cap mismatch: "
                f"server recorded {recorded_cap}, spec requested {spec.max_cycles}"
            )
            if strict:
                raise RuntimeError(msg)
            return msg

        # Same context path as a direct worker (agent/subagent.py): the
        # spawn context carries the firewalled brief, the role's pushed
        # skill (HI #2), and the effective tool allowlist.
        ctx_raw = await _call_tool_text(session, "musubi_get_subagent_context", {
            "handle_id": handle_id,
        })
        ctx = _loads(ctx_raw)
        if ctx.get("status") != "ok":
            await _call_tool_text(session, "musubi_complete_subagent", {
                "handle_id": handle_id,
                "summary": f"[stage {stage}] context fetch failed: {ctx_raw[:200]}",
                "turns": 0,
                "status": "failed",
            })
            await _finalize_pipeline(session, psid, "aborted", False)
            msg = f"[pipeline {pname}] stage {stage!r} context fetch failed: {ctx_raw}"
            if strict:
                raise RuntimeError(msg)
            return msg
        allowed = ctx.get("allowed_tools") or st.get("allowed_tools") or []
        role_skill = ctx.get("role_skill")

        system_prompt = build_subagent_system_prompt(spec.prompt, role_skill, brief)
        child_tools = select_child_tools(tools, allowed)

        # Stage nesting (mirrors agent/subagent.py's worker nesting): the
        # server's `spawn_roles` (pipeline.yaml spawns ∩ firewall) is the
        # gate — not frontmatter, which worker prompts don't declare. The
        # stage's orchestration parents on the PIPELINE session so the
        # server narrows its spawns per pipeline (HI #5); the server still
        # re-validates every spawn.
        stage_orch = None
        stage_spawn_catalog = None
        spawn_roles = st.get("spawn_roles") or []
        if (
            spawn_roles
            and psid
            and orchestration is not None
            and getattr(orchestration, "can_spawn_deeper", False)
        ):
            spawn_tool = [
                t for t in tools if t.get("name") == "musubi_spawn_subagent"
            ]
            if spawn_tool:
                child_tools = child_tools + spawn_tool
                stage_orch = orchestration.stage_child(role, psid)
                stage_spawn_catalog = tools

        print(
            f"[agent]     ⮑ stage {stage} (role={role}, "
            f"tools={len(child_tools)}, nests={stage_orch is not None})",
            file=log,
        )

        try:
            answer, turns = await run_unit(
                session, vendor, child_tools,
                system_prompt=system_prompt, user_message=None,
                max_cycles=spec.max_cycles, log=log,
                compression_db_path=compression_db_path,
                context_budget_chars=spec.context_budget_chars,
                role=role,
                stats=stats,
                budget=budget,
                audit_db_path=audit_db_path,
                orchestration=stage_orch,
                spawn_catalog=stage_spawn_catalog,
                worker_max_output=spec.worker_max_output,
                audit_session_id=psid,
                audit_worker_id=handle_id,
                audit_stage=stage,
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
        if _is_incomplete_tool_outcome(answer):
            await _call_tool_text(session, "musubi_complete_subagent", {
                "handle_id": handle_id,
                "summary": answer,
                "turns": turns,
                "status": "failed",
            })
            await _finalize_pipeline(session, psid, "aborted", False)
            if strict:
                raise RuntimeError(
                    f"pipeline stage {stage!r} ended with an incomplete tool call"
                )
            return answer
        status = "done" if answer is not None else "escalated"
        if answer is None:
            pipeline_escalated = True
            answer = f"[stage {stage}] exceeded {spec.max_cycles} cycles"

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


def _read_stage_agent_md(
    role: str, pipeline_name: str, agents_dir: Path | None,
) -> str:
    """Role prompt for one standalone pipeline stage.

    `workers/<role>.agent.md` first — a standalone stage is a worker acting
    on a brief, and the pipeline-stages/ variants keep the embedded host's
    JSON-manifest ceremony. `pipeline-stages/<pipeline>/<role>.agent.md`
    second, for roles that exist only as stage prompts (e.g. code-review's
    scoper/finder/synthesizer). Empty string when neither resolves — the
    caller fails the stage closed.
    """
    from agent.prompt_resolver import AgentPromptPurpose, read_agent_prompt
    from agent.subagent import _default_agents_dir

    base = agents_dir or _default_agents_dir()
    root = base.parent.parent if base.name == "agents" else base
    text = read_agent_prompt([root], role, purpose=AgentPromptPurpose.WORKER)
    if text.strip():
        return text
    return read_agent_prompt(
        [root], role,
        purpose=AgentPromptPurpose.PIPELINE_STAGE,
        pipeline_name=pipeline_name,
    )


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


def _is_incomplete_tool_outcome(answer: str | None) -> bool:
    """Recognize the typed max-token guard returned by the worker loop."""
    if not isinstance(answer, str) or not answer.startswith("[blocked] "):
        return False
    payload = _loads(answer.removeprefix("[blocked] "))
    return payload.get("reason") == "output_too_large_for_single_tool_call"
