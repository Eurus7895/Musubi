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

from agent.boundary import ROOT_ROLE
from agent.jsonio import loads_dict

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


@dataclass(frozen=True)
class PipelineResumePlan:
    start_index: int
    completed_roles: tuple[str, ...]
    summaries: tuple[str, ...]
    retry_stage: str | None
    retry_attempt: int | None
    user_hint: str | None
    extra_budget: int
    force_no_spawns: bool


def _plan_pipeline_resume(
    plan: list[dict[str, Any]],
    stage_rows: list[dict[str, Any]],
    pending: dict[str, Any],
) -> PipelineResumePlan:
    """Resolve a single consumed decision against append-only stage outputs."""
    action = pending.get("action")
    if not action:
        raise RuntimeError("pipeline has no pending resume action")
    if action not in {
        "approve", "retry", "auto_approve_rest", "grant", "force",
    }:
        raise RuntimeError(f"Unknown pending pipeline action {action!r}")

    latest: dict[str, dict[str, Any]] = {}
    for row in stage_rows:
        if row.get("chunk_id") not in {None, ""}:
            continue
        stage = str(row.get("stage") or "")
        if not stage:
            continue
        if stage not in latest or int(row.get("attempt") or 0) > int(
            latest[stage].get("attempt") or 0
        ):
            latest[stage] = row

    completed_indexes = [
        index
        for index, step in enumerate(plan)
        if latest.get(str(step.get("stage") or ""), {}).get("output") is not None
    ]
    first_incomplete = next(
        (
            index
            for index, step in enumerate(plan)
            if latest.get(str(step.get("stage") or ""), {}).get("output") is None
        ),
        len(plan),
    )
    retry_stage: str | None = None
    retry_attempt: int | None = None
    start_index = first_incomplete
    if action == "retry":
        if not completed_indexes:
            raise RuntimeError("retry has no durable completed stage to reopen")
        start_index = completed_indexes[-1]
        retry_stage = str(plan[start_index].get("stage") or "")
        retry_attempt = int(latest[retry_stage].get("attempt") or 0) + 1
    elif action in {"grant", "force"}:
        if start_index >= len(plan):
            raise RuntimeError("budget resume has no incomplete stage to reopen")
        retry_stage = str(plan[start_index].get("stage") or "")
        retry_attempt = int(latest.get(retry_stage, {}).get("attempt") or 0) + 1

    summaries: list[str] = []
    completed_roles: list[str] = []
    for index, step in enumerate(plan[:start_index]):
        stage = str(step.get("stage") or "")
        row = latest.get(stage)
        if row is None or row.get("output") is None:
            raise RuntimeError(
                f"pipeline resume checkpoint is missing output for stage {stage!r}"
            )
        raw = row["output"]
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            value = raw
        rendered = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, separators=(",", ":"),
        )
        summaries.append(f"### {stage}\n{rendered}")
        completed_roles.append(str(step.get("role") or ""))

    hint = pending.get("user_hint")
    cleaned_hint = hint.strip() if isinstance(hint, str) and hint.strip() else None
    return PipelineResumePlan(
        start_index=start_index,
        completed_roles=tuple(completed_roles),
        summaries=tuple(summaries),
        retry_stage=retry_stage,
        retry_attempt=retry_attempt,
        user_hint=cleaned_hint,
        extra_budget=max(0, int(pending.get("extra_budget") or 0)),
        force_no_spawns=action == "force",
    )


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
    resume_session_id: str | None = None,
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
    from agent.budget import ChildTokenBudget, pipeline_stage_allowance
    from agent.run import (
        PolicyDeniedError,
        _call_tool_text,
        _policy_incomplete,
        _worker_touched_files,
        run_unit,
    )
    from agent.subagent import (
        build_subagent_system_prompt,
        select_child_tools,
        surviving_nonempty_files,
    )

    request = str(spawn_args.get("brief", ""))
    resume_plan: PipelineResumePlan | None = None
    if resume_session_id:
        from composer import active_stages, agent_for_stage
        from storage import db as state_db

        psid = resume_session_id
        pname = str(spawn_args.get("pipeline_name") or "")
        plan = [
            {"stage": stage, "role": agent_for_stage(pname, stage)}
            for stage in active_stages(pname)
            if agent_for_stage(pname, stage)
        ]
        if len(plan) < 2:
            raise RuntimeError(
                f"pipeline {pname!r} has no resumable registered stage plan"
            )
        consumed = loads_dict(await _call_tool_text(
            session,
            "musubi_consume_pending_action",
            {"session_id": psid},
        ))
        if consumed.get("status") != "ok":
            raise RuntimeError(
                f"pipeline resume action could not be consumed: {consumed}"
            )
        rows = state_db.get_all_stage_rows(psid, compression_db_path)
        resume_plan = _plan_pipeline_resume(plan, rows, consumed)
        if resume_plan.retry_stage:
            incremented = loads_dict(await _call_tool_text(
                session,
                "musubi_increment_attempt",
                {
                    "session_id": psid,
                    "stage": resume_plan.retry_stage,
                    "user_hint": resume_plan.user_hint,
                },
            ))
            if (
                incremented.get("status") != "incremented"
                or int(incremented.get("attempt") or 0)
                != resume_plan.retry_attempt
            ):
                raise RuntimeError(
                    f"pipeline retry checkpoint could not advance: {incremented}"
                )
    else:
        raw = await _call_tool_text(session, "musubi_spawn_pipeline", spawn_args)
        spawned = loads_dict(raw)
        if spawned.get("status") != "spawned":
            if spawned.get("error_kind") == "policy_denied":
                raise PolicyDeniedError(
                    role=str(spawn_args.get("parent_agent_name") or ROOT_ROLE),
                    tool="musubi_spawn_pipeline",
                    reason=str(spawned.get("error") or "pipeline spawn denied"),
                )
            if strict:
                raise RuntimeError(f"pipeline spawn rejected: {raw}")
            return raw
        psid = str(spawned.get("pipeline_session_id", ""))
        pname = str(spawned.get("pipeline_name", ""))
        plan = spawned.get("plan") or []

    print(f"[agent]   ⇶ pipeline {pname} ({len(plan)} stages)", file=log)

    summaries = list(resume_plan.summaries) if resume_plan else []
    pipeline_escalated = False
    for i, step in enumerate(plan):
        if resume_plan and i < resume_plan.start_index:
            continue
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

        # Skill selection for this stage (option 3 extended to pipelines):
        # ask the deterministic recommender (zero-LLM) for the single best
        # skill in the stage role's allowlist, then push it into the stage.
        # The role name is folded into the task text so a role-canonical skill
        # matches (e.g. "reviewer" fires code-review's "review" trigger). The
        # spawn re-validates the id against the role's allowlist (fail-closed).
        stage_skill = await _recommend_stage_skill(session, role, brief)

        stage_raw = await _call_tool_text(session, "musubi_spawn_pipeline_stage", {
            "pipeline_session_id": psid, "pipeline_name": pname,
            "stage": stage, "brief": brief, "max_turns": spec.max_cycles,
            "pushed_skill_id": stage_skill,
        })
        st = loads_dict(stage_raw)
        if st.get("status") != "spawned":
            msg = f"[pipeline {pname}] stage {stage!r} could not start: {stage_raw}"
            await _finalize_pipeline(session, psid, "aborted", False)
            if st.get("error_kind") == "policy_denied":
                raise PolicyDeniedError(
                    role=role,
                    tool="musubi_spawn_pipeline_stage",
                    reason=str(st.get("error") or "pipeline stage denied"),
                )
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
        ctx = loads_dict(ctx_raw)
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
            and not (
                resume_plan
                and resume_plan.force_no_spawns
                and i == resume_plan.start_index
            )
        ):
            spawn_tool = [
                t for t in tools if t.get("name") == "musubi_spawn_subagent"
            ]
            if spawn_tool:
                child_tools = child_tools + spawn_tool
                stage_orch = orchestration.stage_child(role, psid, pname)
                if (
                    resume_plan
                    and resume_plan.extra_budget
                    and i == resume_plan.start_index
                ):
                    stage_orch.max_root_workers += resume_plan.extra_budget
                stage_spawn_catalog = tools

        # Each stage runs against its own fair-share allowance of the shared run
        # budget (charged straight through to the parent). An early planner or
        # designer loop is capped at its slice and cannot spend coder/reviewer's
        # reserve; a stage that underspends hands the slack to later stages.
        stage_budget = budget
        if budget is not None:
            allowance = pipeline_stage_allowance(budget, len(plan) - i)
            stage_budget = ChildTokenBudget(budget, allowance)

        print(
            f"[agent]     ⮑ stage {stage} (role={role}, "
            f"tools={len(child_tools)}, nests={stage_orch is not None}, "
            f"allowance={getattr(stage_budget, 'max_tokens', None)})",
            file=log,
        )

        # Deterministic record of the files THIS stage mutates (same ContextVar
        # sink a direct worker gets in agent/subagent.py). Needed so a stage
        # that finishes exactly on its last allowed turn can send an artifact
        # manifest with its completion — without it the substrate's turn-cap
        # coercion marks a successful stage `escalated` in the audit DB.
        touched: set[str] = set()
        touched_token = _worker_touched_files.set(touched)
        try:
            answer, turns = await run_unit(
                session, vendor, child_tools,
                system_prompt=system_prompt, user_message=None,
                max_cycles=spec.max_cycles, log=log,
                compression_db_path=compression_db_path,
                context_budget_chars=spec.context_budget_chars,
                role=role,
                stats=stats,
                budget=stage_budget,
                audit_db_path=audit_db_path,
                orchestration=stage_orch,
                spawn_catalog=stage_spawn_catalog,
                worker_max_output=spec.worker_max_output,
                audit_session_id=psid,
                audit_worker_id=handle_id,
                audit_stage=stage,
            )
        except PolicyDeniedError as exc:
            policy_summary = _policy_incomplete(exc)
            await _call_tool_text(session, "musubi_complete_subagent", {
                "handle_id": handle_id,
                "summary": policy_summary,
                "turns": 0,
                "status": "escalated",
            })
            await _finalize_pipeline(session, psid, "aborted", False)
            raise
        except Exception as exc:
            is_budget = type(exc).__name__ in {
                "BudgetExhaustedError",
                "TokenBudgetExhaustedError",
            }
            if is_budget:
                print(
                    f"[agent]     ⚠ stage {stage} halted: token allowance "
                    f"exhausted (used {getattr(stage_budget, 'tokens_used', '?')}"
                    f"/{getattr(stage_budget, 'max_tokens', '?')}, run remaining "
                    f"{getattr(budget, 'remaining', '?')})",
                    file=log,
                )
                await _call_tool_text(session, "musubi_complete_subagent", {
                    "handle_id": handle_id,
                    "summary": f"[stage {stage}] budget exhausted: {exc}",
                    "turns": 0,
                    "status": "escalated",
                })
                paused = loads_dict(await _call_tool_text(
                    session,
                    "musubi_pause_session",
                    {
                        "session_id": psid,
                        "stage": stage,
                        "reason": "budget_exhausted",
                    },
                ))
                if paused.get("status") != "paused":
                    await _finalize_pipeline(session, psid, "escalated", True)
            else:
                await _finalize_pipeline(session, psid, "aborted", False)
            raise
        finally:
            _worker_touched_files.reset(touched_token)
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

        complete_payload: dict[str, Any] = {
            "handle_id": handle_id, "summary": answer, "turns": turns, "status": status,
        }
        if status == "done" and turns >= spec.max_cycles:
            # The stage finished ON its last allowed turn. Without a manifest
            # the substrate's turn-cap rule (sub_sessions.complete) would
            # coerce this success to `escalated` in the audit DB. Attach the
            # surviving mutated files; the substrate verifies them itself and
            # keeps the coercion when they don't verify (or when the stage
            # mutated nothing — a text-only claim stays unverifiable, so a
            # read-only stage at its cap still audits as escalated by design).
            stage_artifacts = surviving_nonempty_files(touched)
            if stage_artifacts:
                complete_payload["artifacts"] = stage_artifacts
        await _call_tool_text(
            session, "musubi_complete_subagent", complete_payload,
        )
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
    from agent.subagent import _agents_root, read_worker_prompt

    text = read_worker_prompt(role, agents_dir)
    if text.strip():
        return text
    return read_agent_prompt(
        [_agents_root(agents_dir)], role,
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


async def _recommend_stage_skill(session: Any, role: str, brief: str) -> str | None:
    """Return the single best catalog skill id for a pipeline stage, or None.

    Deterministic (the recommender is pure scoring, zero-LLM — HI #1). Ranks
    only skills in the stage role's allowlist (`for_role`); the role name is
    folded into the task text so a role-canonical skill matches even when the
    brief itself does not (e.g. "reviewer" fires code-review's "review"
    trigger). Any failure degrades to None — a stage without a matched skill
    simply runs without one, never blocking the pipeline.
    """
    from agent.run import _call_tool_text

    try:
        raw = await _call_tool_text(session, "musubi_recommend_skills", {
            "task": f"{role} {brief}",
            "agent_name": role,
            "for_role": role,
            "limit": 1,
        })
        recommended = loads_dict(raw).get("recommended") or []
        if recommended:
            skill_id = str(recommended[0].get("skill_id") or "").strip()
            return skill_id or None
    except Exception:
        return None
    return None


def _is_incomplete_tool_outcome(answer: str | None) -> bool:
    """Recognize the typed max-token guard returned by the worker loop."""
    if not isinstance(answer, str) or not answer.startswith("[blocked] "):
        return False
    payload = loads_dict(answer.removeprefix("[blocked] "))
    return payload.get("reason") == "output_too_large_for_single_tool_call"
