"""Agent CLI — drive the Musubi MCP server via a direct LLM API.

musubi-tier: substrate
expires-when: never — the agent is the model's native mode (per
  CLAUDE.md), and this is its vendor-agnostic Python entry point.
  Replaces the Copilot-Chat-only access path with one that works
  against any LLM whose Python SDK exposes a tool-use API.

Usage:
    agent-agent "your task"                      # uses .musubi/llm.json `default`
    agent-agent "your task" --profile azure.work # pick a profile from llm.json
    python -m agent.run "your task"              # equivalent

Endpoint selection precedence: --profile → the .musubi/llm.json `default`
profile → env-key detection (when no config file exists). The vendor, model,
endpoint, and api-key all live in `.musubi/llm.json` — `--profile` is the only
CLI switch; to change the vendor or model, edit the profile (see
`agent/config.py`).

Env vars:
    ANTHROPIC_API_KEY   used by the anthropic vendor
    OPENAI_API_KEY      used by the openai vendor
    DEEPSEEK_API_KEY    used by the deepseek vendor
    OLLAMA_HOST         optional; ollama base URL (default http://localhost:11434)

The Musubi MCP server is auto-located: same repo as this module by
default, overridable with --musubi or MUSUBI_ROOT.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import os
import re
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

_MUSUBI_MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(_MUSUBI_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_MUSUBI_MODULE_ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.context import (
    build_system_prompt,
    fit_context,
    fit_model_input,
    is_elided_tool_arg_marker,
    resolve_effort_bounds,
)
from agent.goal_state import GoalState, root_decision_tools
from agent.budget import (
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_tokens_from_chars,
)
from agent.boundary import (
    denied_tool_guidance,
    evaluate_tool_call,
    is_musubi_tool,
    json_args,
    record_policy_decision,
    record_tool_audit,
)
from agent.mcp_gateway import (
    McpGateway,
    find_mcp_config_path,
    load_mcp_servers,
    mcp_config_candidates,
)
from agent.scope import ScopeHint, classify_task
from agent.vendors import LMResponse, LMRouter, build_from_profile, build_vendor
from tool_surface import filter_tool_catalog, tool_names_for_surface

DEFAULT_MAX_CYCLES = 16

DEFAULT_AGENT_MAX_TOKENS = 200_000

#: Per-cycle fan-out width guard: at most this many workers of the SAME role may
#: be spawned in one model turn. Bounds runaway fan-out when workers run in
#: parallel. Mirrors `max_spawns_per_role_per_turn` in agent.agent.md.
DEFAULT_MAX_SPAWNS_PER_ROLE = 3
DEFAULT_MAX_ROOT_WORKERS = 3
DEFAULT_MAX_ROOT_RECOVERY_ANALYSIS_CYCLES = 2

#: No-progress budget breaker: if the root run has spent at least this fraction
#: of its token budget and no worker has delivered a completed artifact (a
#: `done` outcome with mutated files), stop instead of grinding the remaining
#: budget on more escalating workers. A weak driver model that never converges
#: otherwise burns the full ceiling (e.g. 200k) across the whole worker tree;
#: this caps that waste while never firing on a run that is actually producing.
DEFAULT_NO_PROGRESS_BUDGET_RATIO = 0.7

# Injected into the root prompt only when the caller passes --plan. Explicit
# opt-in replaces the retired regex force (a keyword guess used to refuse the
# coder and demand a planner at cycle 0); now the user declares plan-first.
_PLAN_FIRST_DIRECTIVE = (
    "The user explicitly requested a plan-first workflow (--plan). Before "
    "spawning any `coder` worker, spawn role `planner` and pass its summary to "
    "the coder; never let the coder both plan and implement."
)

ORDER_SENSITIVE_FILE_TOOLS: frozenset[str] = frozenset({
    "musubi_write_file",
    "musubi_append_file",
    "musubi_edit_file",
})

# C1 — deterministic record of the files a worker mutated, populated by the
# dispatch loop and read by `run_subagent` to drive the mechanical gate at the
# point the parent receives the worker (the root boundary). A ContextVar keeps
# the loop signatures untouched and isolates nested workers automatically: each
# `run_subagent` sets its own sink, so a child's writes never leak into the
# parent's set. `None` (the default) means "not collecting" — inert for the root
# worker and any non-spawned call.
_worker_touched_files: contextvars.ContextVar[set[str] | None] = (
    contextvars.ContextVar("musubi_worker_touched_files", default=None)
)

# O3 — a short label identifying whose cycle a log line belongs to. `run_subagent`
# sets `<role>#<handle>` for the worker it runs; the root leaves the default. Read
# by the cycle loggers so several "cycle 0" lines from different workers are
# distinguishable. Same ContextVar pattern as above — no loop-signature changes.
_worker_log_label: contextvars.ContextVar[str] = (
    contextvars.ContextVar("musubi_worker_log_label", default="root")
)


#: How deep workers may nest. depth 0 = root task; a worker at depth < max_depth
#: that is itself allowed to spawn may summon workers one level down. With the
#: default, the root and its direct workers can spawn; their workers are leaves.
DEFAULT_MAX_DEPTH = 2


class FailureKind(StrEnum):
    """Typed cause of a worker's terminal failure, derived from control flow
    (turn counters, marker branches, raised exceptions) — never from parsing
    summary prose."""

    TURN_CAP = "turn_cap"
    BLOCKED = "blocked"
    BUDGET = "budget"
    POLICY = "policy"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    AUTO_REPLACE = "auto_replace"
    ROOT_ANALYZE = "root_analyze"
    HALT = "halt"


@dataclass(frozen=True)
class WorkerOutcome:
    """Terminal state retained by the parent for a possible replacement."""

    role: str
    status: str
    summary: str
    touched_files: tuple[str, ...] = ()
    #: The firewalled brief this worker ran on — an automatic replacement
    #: re-runs the same contract, not a paraphrase of it.
    brief: str = ""
    #: None on success or on a legacy/untyped failure (which keeps the
    #: root-analysis path); set from control flow for typed failures.
    failure_kind: FailureKind | None = None
    #: The skill id the root pushed into this worker's spawn, if any. Replayed
    #: on an automatic replacement so the continuation runs the SAME worker
    #: contract — a direct worker carries no native skill tool, so dropping it
    #: would resume the artifact without the pushed procedure.
    pushed_skill_id: str | None = None


def decide_recovery(
    outcome: WorkerOutcome,
    *,
    same_role_failures: int,
    worker_slots: int,
) -> RecoveryAction:
    """Deterministic verdict for one terminal worker failure.

    Exhausted worker slots or a second same-role failure always halt —
    one audited continuation is the limit, never a replacement loop. A first
    turn-cap failure that left real artifacts behind is genuinely unfinished
    work: replace it automatically. Budget/policy failures stay fail-closed.
    Everything else (blocked, unknown, no surviving evidence) goes to the
    root's bounded analysis window.
    """
    if worker_slots <= 0 or same_role_failures >= 2:
        return RecoveryAction.HALT
    if outcome.failure_kind is FailureKind.TURN_CAP and outcome.touched_files:
        return RecoveryAction.AUTO_REPLACE
    if outcome.failure_kind in {FailureKind.BUDGET, FailureKind.POLICY}:
        return RecoveryAction.HALT
    return RecoveryAction.ROOT_ANALYZE


@dataclass
class Orchestration:
    """Context that lets a worker loop spawn further workers.

    `parent_session_id` owns the spawn parentage (always the ROOT session — the
    whole worker tree shares one session row); `parent_agent_name` is the
    firewall identity of THIS worker (the role whose `spawn_allowlist` gates what
    it may summon). `depth` is this worker's depth (0 = root). Disabled (no
    spawning) when `parent_session_id` is None.
    """

    parent_session_id: str | None
    parent_agent_name: str = "agent"
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    spawned_workers: int = 0
    max_root_workers: int = DEFAULT_MAX_ROOT_WORKERS
    root_recovery_analysis_cycles: int = 0
    worker_outcomes: list[WorkerOutcome] = field(default_factory=list)
    goal_state: GoalState | None = None

    @property
    def enabled(self) -> bool:
        return self.parent_session_id is not None

    def child(self, role: str) -> "Orchestration":
        """Orchestration for a worker this one spawns: same root session, the
        child's role as the new firewall identity, one level deeper."""
        return Orchestration(
            parent_session_id=self.parent_session_id,
            parent_agent_name=role,
            depth=self.depth + 1,
            max_depth=self.max_depth,
        )

    def stage_child(self, role: str, pipeline_session_id: str) -> "Orchestration":
        """Orchestration for one pipeline stage worker. Unlike `child`, the
        parentage moves to the PIPELINE session: the server resolves the
        pipeline from `parent_session_id` and narrows the stage's spawnable
        roles to pipeline.yaml `spawns:` ∩ firewall (HI #5). Handing a stage
        the root session instead would skip that narrowing. The pipeline
        envelope itself is a sequencer, not a worker — a stage sits one level
        below the worker that summoned the pipeline."""
        return Orchestration(
            parent_session_id=pipeline_session_id,
            parent_agent_name=role,
            depth=self.depth + 1,
            max_depth=self.max_depth,
        )

    @property
    def can_spawn_deeper(self) -> bool:
        """True if a worker at this depth is still allowed to nest."""
        return self.enabled and self.depth < self.max_depth

    def record_worker_outcome(
        self,
        *,
        role: str,
        status: str,
        summary: str,
        touched_files: set[str] | tuple[str, ...] | list[str],
        brief: str = "",
        failure_kind: FailureKind | None = None,
        pushed_skill_id: str | None = None,
    ) -> WorkerOutcome:
        """Retain a compact terminal record for parent-side recovery."""
        outcome = WorkerOutcome(
            role=role,
            status=status,
            summary=summary,
            touched_files=tuple(sorted(set(touched_files))),
            brief=brief,
            failure_kind=failure_kind,
            pushed_skill_id=pushed_skill_id,
        )
        self.worker_outcomes.append(outcome)
        if self.goal_state is not None:
            self.goal_state.record_outcome(
                role=role,
                status=status,
                summary=summary,
                touched_files=touched_files,
            )
            # Post-plan reclassification: a planner-led goal consumes the
            # planner's bounded change manifest the moment it lands. The
            # manifest verdict (not the lexical guess) then owns route, scope,
            # and the legal next mutation role; a missing/invalid manifest
            # fails closed to one clarification inside apply_planner_manifest.
            if role == "planner" and status == "done" and (
                self.goal_state.next_role == "planner"
            ):
                self.goal_state.apply_planner_manifest(summary)
        return outcome

    def latest_failed_outcome(self, role: str) -> WorkerOutcome | None:
        """Return the latest same-role failure, unless a later run recovered."""
        for outcome in reversed(self.worker_outcomes):
            if outcome.role == role:
                if outcome.status in {"failed", "escalated"}:
                    return outcome
                return None
        return None

    def latest_unrecovered_failure(self) -> WorkerOutcome | None:
        """Return the newest failure not superseded by a same-role success."""
        seen_roles: set[str] = set()
        for outcome in reversed(self.worker_outcomes):
            if outcome.role in seen_roles:
                continue
            seen_roles.add(outcome.role)
            if outcome.status in {"failed", "escalated"}:
                return outcome
        return None


def _replacement_brief(original_brief: str, outcome: WorkerOutcome) -> str:
    """Give a replacement worker the prior terminal state without parent history."""
    files = ", ".join(outcome.touched_files) or "none recorded"
    return (
        f"{original_brief}\n\n"
        "[worker-replacement]\n"
        "Continue the existing artifact from its current state; do not restart it.\n"
        f"Prior role: {outcome.role}\n"
        f"Prior status: {outcome.status}\n"
        f"Touched files: {files}\n"
        f"Prior summary: {outcome.summary}\n"
        "Complete missing acceptance criteria before optional enhancement.\n"
        "[/worker-replacement]"
    )


async def _auto_recovery_transition(
    session: ClientSession,
    log: Any,
    *,
    vendor: LMRouter | None,
    tools: list[dict[str, Any]],
    spawn_catalog: list[dict[str, Any]] | None,
    orchestration: Orchestration,
    gateway: McpGateway | None,
    compression_db_path: Path | None,
    budget: TokenBudgetEnforcer | None,
    stats: AgentRunStats | None,
    audit_db_path: Path | None,
    scope_hint: ScopeHint | None,
) -> str | None:
    """Deterministic recovery transitions before a root LM cycle.

    Evaluates the newest unrecovered TYPED failure in a small loop:
    AUTO_REPLACE synthesizes one `musubi_spawn_subagent` call and passes it
    through `_dispatch` — never straight to `run_subagent` — so the root
    worker ceiling, replacement brief injection, policy check, tool audit,
    subagent audit, and touched-file tracking all apply (HI #8). HALT returns
    the final `[incomplete]` text. ROOT_ANALYZE (and every untyped failure)
    returns None, leaving the legacy bounded analysis window untouched.

    Runs before the cycle counter and any model call: the transition itself
    never increments `cycles_used` and never writes an `agent_cycles` row,
    because no LM call occurred.
    """
    while True:
        outcome = orchestration.latest_unrecovered_failure()
        if outcome is None or outcome.failure_kind is None:
            return None
        same_role_failures = sum(
            1 for prior in orchestration.worker_outcomes
            if prior.role == outcome.role
            and prior.status in {"failed", "escalated"}
        )
        worker_slots = (
            orchestration.max_root_workers - orchestration.spawned_workers
        )
        action = decide_recovery(
            outcome,
            same_role_failures=same_role_failures,
            worker_slots=worker_slots,
        )
        if action is RecoveryAction.ROOT_ANALYZE:
            return None
        if action is RecoveryAction.HALT:
            if worker_slots <= 0:
                reason = (
                    f"root worker ceiling ({orchestration.max_root_workers}) "
                    "was exhausted before the artifact could be completed"
                )
            elif same_role_failures >= 2:
                reason = (
                    f"a second {outcome.role} failure "
                    f"({outcome.failure_kind.value}) ended the bounded "
                    "recovery — one audited continuation is the limit"
                )
            else:
                reason = (
                    f"{outcome.role} failed with a non-recoverable "
                    f"{outcome.failure_kind.value} failure"
                )
            print(f"[agent] recovery halt: {reason}", file=log)
            return _recovery_incomplete(outcome, reason)
        # AUTO_REPLACE, but `touched_files` is only a write HISTORY — it is not
        # pruned when a worker later removes a file (e.g. a Bash `rm` of a
        # scratch generator). Confirm at least one recorded path still exists
        # non-empty on disk before spending the single continuation on a
        # replacement whose brief says "continue from current state". If
        # nothing survives, defer to the bounded root-analysis window instead.
        from agent.subagent import surviving_nonempty_files

        if surviving_nonempty_files(set(outcome.touched_files)) is None:
            print(
                f"[agent] recovery: {outcome.role} turn_cap left no surviving "
                "artifact on disk; deferring to root analysis",
                file=log,
            )
            return None
        # The model did not make this tool call, so no synthetic assistant
        # message is appended; the spawn is synthesized and dispatched through
        # the exact path a model call would take. The pushed skill is replayed
        # so the replacement reruns the same worker contract (a direct worker
        # has no native skill tool to reload it otherwise).
        recovery_input: dict[str, Any] = {
            "role": outcome.role,
            "brief": outcome.brief,
        }
        if outcome.pushed_skill_id:
            recovery_input["pushed_skill_id"] = outcome.pushed_skill_id
        auto_tool_use = {
            "type": "tool_use",
            "id": f"auto-recovery-{len(orchestration.worker_outcomes)}",
            "name": "musubi_spawn_subagent",
            "input": recovery_input,
        }
        print(
            f"[agent] automatic recovery: {outcome.role} "
            f"{outcome.failure_kind.value} -> audited replacement",
            file=log,
        )
        outcomes_before = len(orchestration.worker_outcomes)
        await _dispatch(
            session, [auto_tool_use], log,
            vendor=vendor, tools=(spawn_catalog or tools),
            orchestration=orchestration, gateway=gateway,
            compression_db_path=compression_db_path,
            role="agent",
            scope_hint=scope_hint,
            budget=budget,
            stats=stats,
            audit_db_path=audit_db_path,
        )
        if len(orchestration.worker_outcomes) == outcomes_before:
            # The spawn was refused or errored without producing a terminal
            # outcome — retrying deterministically would loop, so fail closed.
            return _recovery_incomplete(
                outcome,
                "the automatic replacement worker could not be started",
            )
        # Loop: a done replacement clears the failure (return None next
        # pass); a failed one re-enters decide_recovery, where the second
        # same-role failure halts.


def _pipeline_recommendation(state: GoalState) -> str:
    """Deterministic final answer when request or manifest assessment is large.

    Large workflows stay user-invoked (policy locked decision #4): the root
    never auto-launches a pipeline, it hands the decision back with the exact
    command. Emitted with zero further model calls or worker spawns.
    """
    assessment = state.assessment
    evidence = (
        ", ".join(assessment.evidence) if assessment is not None else "manifest"
    )
    return (
        "[scope] The deterministic change assessment classified this request "
        f"as a large change ({evidence}).\n"
        "Large workflows are user-invoked and never auto-launched. To run it "
        "under the governed pipeline (plan → design → code → review with the "
        "evaluator firewall), start it explicitly:\n\n"
        '    agent "<your brief>" --pipeline feature-dev\n\n'
        "No pipeline was launched and no further workers were spawned."
    )


def _recovery_incomplete(outcome: WorkerOutcome, reason: str) -> str:
    """Deterministic root result when no bounded mutation recovery remains."""
    files = ", ".join(outcome.touched_files) or "none recorded"
    return (
        f"[incomplete] {reason}\n"
        f"Last failed worker: {outcome.role} ({outcome.status}).\n"
        f"Files touched: {files}.\n"
        f"Worker summary: {outcome.summary}"
    )


@dataclass
class AgentRunStats:
    """Cumulative telemetry for one CLI turn across root and workers."""

    cycles: int = 0
    lm_ms: int = 0
    tokens_in_estimate: int = 0
    tokens_out_estimate: int = 0

    def record_cycle(
        self,
        *,
        lm_ms: int,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        self.cycles += 1
        self.lm_ms += lm_ms
        self.tokens_in_estimate += tokens_in
        self.tokens_out_estimate += tokens_out


@dataclass
class EffortCallResult:
    """Final response plus every vendor call made to obtain it."""

    response: LMResponse
    attempts: list[LMResponse]


@dataclass(frozen=True)
class CycleTokenUsage:
    """Normalized provider usage for one logical loop cycle."""

    tokens_in: int
    cached_input_tokens: int
    tokens_out: int
    source: str


# ── Public entry ────────────────────────────────────────────────────────────


async def run_agent(
    task: str,
    vendor: LMRouter,
    musubi_dir: Path,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    log: Any = sys.stderr,
    mcp_config: str | os.PathLike[str] | None = None,
    vendor_source: str | None = None,
    chat_id: str | None = None,
    max_tokens: int | None = None,
    tool_surface: str | None = None,
    pipeline: str | None = None,
    plan_first: bool = False,
) -> str:
    """Drive one agent turn end-to-end. Returns the final assistant text.

    When `pipeline` is set, the root task runs that named pipeline directly
    (deterministically, via `pipeline_runner.run_pipeline`) instead of the
    model-routed `run_unit` loop — the same code path the model reaches through
    `musubi_spawn_pipeline`, but summoned by the caller rather than the LLM.

    Spawns the Musubi MCP server, optionally connects every external MCP
    server declared in an `mcp.json` (federating their tools into the
    catalog), hands the merged tools to the LLM via `vendor.call`,
    dispatches whatever tools the model asks for, feeds results back, and
    repeats until the model stops asking for tools (`stop_reason !=
    "tool_use"`) OR `max_cycles` is hit.
    """
    server_path = musubi_dir / "server.py"
    server_env = _server_env()
    context_compression_db_path = _server_db_path(musubi_dir, server_env)
    audit_db_path = _server_audit_db_path(musubi_dir, server_env)
    turn_started_at = time.time()
    stats = AgentRunStats()
    budget = _build_token_budget(max_tokens, log)
    scope_hint = classify_task(task)
    goal_state = GoalState.create(
        intent=task,
        scope=scope_hint.kind.value,
        route=scope_hint.route,
        assessment=scope_hint.assessment,
    )
    direct_answer = _deterministic_scope_answer(task, scope_hint, goal_state)
    if direct_answer is not None:
        print(f"[agent] {scope_hint.log_line()}", file=log)
        print(
            f"[agent] deterministic route={scope_hint.route}; no model call",
            file=log,
        )
        if chat_id:
            _append_chat_message(
                chat_id, "user", task,
                db_path=context_compression_db_path, log=log,
            )
            _append_chat_message(
                chat_id, "assistant", direct_answer,
                db_path=context_compression_db_path, log=log,
            )
            _record_agent_turn(
                chat_id=chat_id,
                parent_session_id=None,
                started_at=turn_started_at,
                ended_at=time.time(),
                model_family="deterministic",
                stats=stats,
                db_path=context_compression_db_path,
                log=log,
            )
        return direct_answer
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env=server_env,
    )

    # Track the result inside the MCP contexts but raise the
    # max-cycles error OUTSIDE them — anyio's TaskGroup wraps any
    # exception raised inside the stdio_client/ClientSession contexts
    # in a BaseExceptionGroup, which would defeat `except RuntimeError`
    # at every call site (including main()).
    final_answer: str | None = None
    loop_error: BaseException | None = None
    parent_session_id: str | None = None

    # One AsyncExitStack owns Musubi's session AND every federated external
    # session, so they all open in order and tear down (LIFO) together. This
    # is equivalent to the old nested `async with` for Musubi alone.
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        gateway = McpGateway()
        mcp_tools = (await session.list_tools()).tools
        local_tools = [_mcp_to_anthropic_tool(t) for t in mcp_tools]
        surface = _tool_surface(tool_surface)
        visible_local_tools = filter_tool_catalog(local_tools, surface)
        gateway.register_local(session, visible_local_tools)
        # External MCP servers are additive and fail-open (a bad entry is
        # logged and skipped). Surface *which* config was used (or that none
        # was found, and exactly where we looked) — that resolution is
        # otherwise invisible from the output.
        cfg_path = find_mcp_config_path(mcp_config)
        if cfg_path is None:
            looked = ", ".join(str(c) for c in mcp_config_candidates(mcp_config))
            print(
                f"[agent] no mcp.json found (looked at: {looked}); "
                f"external tools off",
                file=log,
            )
            specs = []
        else:
            try:
                specs = load_mcp_servers(cfg_path)
                print(
                    f"[agent] mcp config: {cfg_path} ({len(specs)} server(s))",
                    file=log,
                )
            except Exception as exc:  # noqa: BLE001 — bad config ≠ dead agent
                print(
                    f"[agent] mcp.json ignored ({cfg_path}): "
                    f"{type(exc).__name__}: {exc}",
                    file=log,
                )
                specs = []
        await gateway.connect_external(stack, specs, log)

        tools = gateway.tools()
        external_tools = [
            tool for tool in tools
            if not is_musubi_tool(str(tool.get("name", "")))
        ]
        worker_catalog = local_tools + external_tools
        n_external = len(tools) - len(visible_local_tools)
        profile_part = f"profile={vendor_source} " if vendor_source else ""
        print(
            f"[agent] vendor={vendor.name} model={vendor.model} {profile_part}"
            f"tool_surface={surface} tools={len(tools)} "
            f"(musubi_visible={len(visible_local_tools)}, "
            f"musubi_total={len(mcp_tools)}, external={n_external})",
            file=log,
        )

        # Open a parent session up front so the model's sub-agent spawns
        # have a valid parent. The "agent" identity short-circuits the
        # spawn firewall to MAIN_SUBAGENT_ALLOWLIST["agent"] regardless of
        # the session's pipeline tag (policy_engine `_effective_spawn_roles`).
        parent_session_id = await _open_parent_session(session, task, log, chat_id)
        orchestration = Orchestration(
            parent_session_id=parent_session_id,
            goal_state=goal_state,
        )
        print(f"[agent] {scope_hint.log_line()}", file=log)
        system_prompt = build_system_prompt(scope_hint.prompt_block())
        if plan_first:
            system_prompt = f"{system_prompt}\n\n{_PLAN_FIRST_DIRECTIVE}"
            print("[agent] plan-first requested (--plan)", file=log)
        initial_messages: list[dict[str, Any]] | None = None
        if chat_id:
            _append_chat_message(
                chat_id, "user", task,
                db_path=context_compression_db_path, log=log,
            )
            history = _load_chat_history(
                chat_id, db_path=context_compression_db_path, log=log,
            )
            initial_messages = _messages_from_chat_history(system_prompt, history)
            print(
                f"[agent] chat_id={chat_id} history loaded",
                file=log,
            )

        # Catch a loop failure (an LLM/network error from `vendor.call`, a
        # dispatch error) HERE, inside the `async with`, and stash it. Letting
        # it escape into the stack's `__aexit__` makes anyio re-wrap it in a
        # BaseExceptionGroup (the unreadable multi-page traceback) and defeats
        # `except RuntimeError` at every call site. We re-raise it cleanly
        # outside the contexts below.
        #
        # The root task is just the depth-0 worker: it runs through the same
        # `run_unit` entry every child worker does (see `run_unit`). The only
        # difference is its prompt shape (system + user) and that orchestration
        # is enabled so it may summon further workers.
        try:
            if pipeline:
                # Deterministic pipeline run: summon the named pipeline directly
                # rather than letting the model decide. Same runner the driver
                # reaches via musubi_spawn_pipeline (see the tool intercept
                # below), so stage firewalling and audit are identical.
                from agent import pipeline_runner

                spawn_args = {
                    "parent_session_id": parent_session_id,
                    "parent_agent_name": "agent",
                    "pipeline_name": pipeline,
                    "brief": task,
                }
                print(
                    f"[agent] running pipeline {pipeline!r} directly "
                    f"(no model routing)",
                    file=log,
                )
                # Stages select from the FULL worker catalog (not the
                # surface-filtered `tools`): the root `agent` surface hides
                # mutation tools, so a coder stage would otherwise be starved of
                # Write/Edit/Bash despite its policy allowing them. `strict`
                # turns a rejected spawn/stage into a nonzero exit — there is no
                # model loop here to react to an error return.
                final_answer = await pipeline_runner.run_pipeline(
                    session, spawn_args, vendor, worker_catalog, log,
                    compression_db_path=context_compression_db_path,
                    budget=budget, stats=stats, audit_db_path=audit_db_path,
                    strict=True,
                    orchestration=orchestration,
                )
            else:
                final_answer, _ = await run_unit(
                    session, vendor, tools,
                    system_prompt=system_prompt,
                    user_message=task,
                    max_cycles=max_cycles, log=log,
                    orchestration=orchestration, gateway=gateway,
                    spawn_catalog=worker_catalog,
                    salvage_on_exhaust=True,
                    compression_db_path=context_compression_db_path,
                    initial_messages=initial_messages,
                    role="agent",
                    scope_hint=scope_hint,
                    stats=stats,
                    budget=budget,
                    audit_db_path=audit_db_path,
                    audit_session_id=parent_session_id,
                    audit_worker_id="root",
                    audit_stage="agent",
                )
        except Exception as exc:  # noqa: BLE001 — surfaced cleanly outside
            loop_error = exc

    # Raise OUTSIDE the MCP contexts (see above): a clean message that
    # `main()` prints as `agent-agent: …`, and that `except RuntimeError`
    # callers can catch. `_run_loop` signals cycle exhaustion by returning
    # None rather than raising, for the same reason.
    if loop_error is not None:
        raise RuntimeError(_clean_error(loop_error)) from None
    if final_answer is None:
        raise RuntimeError(
            f"agent exceeded {max_cycles} cycles without a final answer"
        )
    if chat_id:
        _append_chat_message(
            chat_id, "assistant", final_answer,
            db_path=context_compression_db_path, log=log,
        )
        _record_agent_turn(
            chat_id=chat_id,
            parent_session_id=parent_session_id,
            started_at=turn_started_at,
            ended_at=time.time(),
            model_family=vendor.model,
            stats=stats,
            db_path=context_compression_db_path,
            log=log,
        )
    _log_turn_usage(log, stats, budget)
    return final_answer


def _compact_root_goal_messages(
    messages: list[dict[str, Any]],
    state: GoalState,
) -> list[dict[str, Any]]:
    """Keep the stable system contract plus one bounded decision delta."""
    stable_system = next(
        (message for message in messages if message.get("role") == "system"),
        None,
    )
    compacted = [stable_system] if stable_system is not None else []
    compacted.append({"role": "user", "content": state.render_decision_block()})
    return compacted


async def _run_loop(
    session: ClientSession,
    vendor: LMRouter,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    max_cycles: int,
    log: Any,
    orchestration: Orchestration | None = None,
    gateway: McpGateway | None = None,
    spawn_catalog: list[dict[str, Any]] | None = None,
    salvage_on_exhaust: bool = False,
    compression_db_path: Path | None = None,
    context_budget_chars: int | None = None,
    role: str = "agent",
    scope_hint: ScopeHint | None = None,
    stats: AgentRunStats | None = None,
    budget: TokenBudgetEnforcer | None = None,
    audit_db_path: Path | None = None,
    worker_max_output: int | None = None,
    model_output_override: int | None = None,
    audit_session_id: str | None = None,
    audit_worker_id: str = "root",
    audit_stage: str | None = None,
) -> tuple[str | None, int]:
    """Drive the reason→act→observe loop. Returns (final_text_or_None, cycles).

    Shared by the top-level agent and every sub-agent. Returns None for the
    text when `max_cycles` is hit without a final answer — the caller decides
    how to surface that (the parent raises outside the MCP context; a sub-agent
    records an escalation). When set, `orchestration` makes a
    `musubi_spawn_subagent` tool call run to completion in-process, its summary
    fed back as the tool result. `gateway`, when set, routes each tool call to
    its owning session (Musubi or a federated external server); when None,
    every call goes to `session` by its exact name (the sub-agent path).

    `tools` is what THIS loop's model sees. `spawn_catalog` (defaults to `tools`)
    is the full catalog a spawned worker draws its own surface from — they differ
    only for a nesting worker, whose model surface is its restricted tools plus
    the spawn tool, while its children still need the whole catalog.
    """
    final_answer: str | None = None
    last_text = ""  # most recent non-empty assistant text, for salvage
    cycles_used = 0
    base_floor, ceiling = resolve_effort_bounds(
        can_mutate=any(
            tool.get("name") in ORDER_SENSITIVE_FILE_TOOLS for tool in tools
        ),
        worker_max_output=worker_max_output,
        model_output_override=(
            model_output_override
            if model_output_override is not None
            else getattr(vendor, "max_output_tokens", None)
        ),
    )
    effort_escalated = False
    for cycle in range(max_cycles):
        # Deterministic recovery transition (root only), BEFORE the cycle
        # counter and any LM cost: a typed recoverable failure gets its one
        # audited same-role continuation through `_dispatch`; a typed
        # non-recoverable one halts. No LM call happens in the transition, so
        # it neither increments `cycles_used` nor writes an `agent_cycles` row.
        if (
            role == "agent"
            and orchestration is not None
            and orchestration.depth == 0
        ):
            transition_outcomes_before = len(orchestration.worker_outcomes)
            transition_halt = await _auto_recovery_transition(
                session, log,
                vendor=vendor, tools=tools, spawn_catalog=spawn_catalog,
                orchestration=orchestration, gateway=gateway,
                compression_db_path=compression_db_path,
                budget=budget, stats=stats, audit_db_path=audit_db_path,
                scope_hint=scope_hint,
            )
            if transition_halt is not None:
                final_answer = transition_halt
                break
            if (
                orchestration.goal_state is not None
                and len(orchestration.worker_outcomes)
                > transition_outcomes_before
            ):
                # A replacement ran: rebuild the compact goal-state delta from
                # the newly recorded outcome so the SAME root LM cycle can
                # conclude from fresh evidence.
                messages = _compact_root_goal_messages(
                    messages, orchestration.goal_state,
                )
        cycles_used = cycle + 1
        root_state = (
            orchestration.goal_state
            if role == "agent"
            and orchestration is not None
            and orchestration.depth == 0
            else None
        )
        recovery_outcome = (
            orchestration.latest_unrecovered_failure()
            if role == "agent"
            and orchestration is not None
            and orchestration.depth == 0
            else None
        )
        recovery_decision_only = bool(
            recovery_outcome is not None
            and orchestration is not None
            and orchestration.root_recovery_analysis_cycles
            >= DEFAULT_MAX_ROOT_RECOVERY_ANALYSIS_CYCLES
        )
        # Manifest-driven halts (root only), BEFORE any budget or model call:
        # a pending clarification goes straight back to the user, and a
        # manifest-reclassified large change ends with the pipeline
        # recommendation — both deterministic, zero further tokens.
        if root_state is not None:
            if root_state.pending_clarification:
                print(
                    "[agent] planner manifest requires clarification; "
                    "no model call",
                    file=log,
                )
                final_answer = root_state.pending_clarification
                break
            if (
                root_state.assessment is not None
                and root_state.route == "plan_design_workflow"
            ):
                print(
                    "[agent] planner manifest reclassified the goal as large; "
                    "recommending a user-invoked pipeline",
                    file=log,
                )
                final_answer = _pipeline_recommendation(root_state)
                break
        # No-progress budget breaker (root only). Checked between workers, so a
        # mid-flight worker is never interrupted: it fires when the completed
        # workers have failed and most of the run budget is already gone, before
        # the next model call spends more on a run that will not converge.
        if root_state is not None and budget is not None and orchestration is not None:
            budget_trip = _no_progress_budget_trip(budget, orchestration)
            if budget_trip is not None:
                print(
                    "[agent] no-progress budget breaker: "
                    f"{getattr(budget, 'tokens_used', 0)}/"
                    f"{getattr(budget, 'max_tokens', 0)} tokens spent, "
                    "no delivered artifact — stopping",
                    file=log,
                )
                final_answer = budget_trip
                break
        cycle_tools = tools
        spawn_exhausted = False
        if root_state is not None:
            # The root spawns workers to make progress; once it has spent its
            # worker ceiling (and no worker failure is pending recovery, which
            # has its own halt below) it cannot spawn again, so force it to
            # conclude instead of spinning refused spawns to the cycle cap.
            spawn_exhausted = (
                recovery_outcome is None
                and orchestration is not None
                and orchestration.spawned_workers
                >= orchestration.max_root_workers
            )
            cycle_tools = root_decision_tools(
                tools,
                root_state,
                recovery_outcome=recovery_outcome is not None,
                decision_only=recovery_decision_only,
                spawn_exhausted=spawn_exhausted,
            )
            if spawn_exhausted and orchestration is not None:
                # No tools are offered this cycle; make the intent explicit so
                # the model answers the user rather than restating an intent to
                # spawn a worker it can no longer summon.
                messages.append({
                    "role": "user",
                    "content": (
                        "[worker budget spent] You have summoned all "
                        f"{orchestration.max_root_workers} available workers and "
                        "cannot spawn more. Give your final answer to the user "
                        "now: summarize what the workers accomplished and state "
                        "any remaining gap plainly."
                    ),
                })
        elif recovery_decision_only:
            cycle_tools = [
                tool for tool in tools
                if tool.get("name") == "musubi_spawn_subagent"
            ]
        # IntelligentContext: trim an over-budget conversation deterministically
        # before the call (oldest/largest tool results elided, pairing intact).
        if context_budget_chars is None:
            messages = fit_context(messages, compression_db_path=compression_db_path)
        else:
            # A worker with an explicit budget (every pipeline stage) gets the
            # HARD cap: the serialized messages PLUS tool definitions are fit
            # under the budget or the cycle raises before the model call, so a
            # runaway stage cannot quietly send a 200k-char input.
            messages = fit_model_input(
                messages,
                cycle_tools,
                budget_chars=context_budget_chars,
                compression_db_path=compression_db_path,
            )
        input_tokens_est = _estimate_input_tokens(messages, cycle_tools)
        try:
            _check_budget_preflight(budget, input_tokens_est, log)
        except TokenBudgetExhaustedError as exc:
            if not salvage_on_exhaust:
                raise
            print(
                f"[agent] token budget exhausted before cycle {cycle}; "
                "returning the best available answer",
                file=log,
            )
            if last_text:
                final_answer = (
                    "[incomplete] token budget exhausted before the next "
                    f"model call: {exc}\n\n"
                    "Last assistant text before the halt:\n"
                    f"{last_text}"
                )
            else:
                final_answer = (
                    "[incomplete] token budget exhausted before the next "
                    f"model call: {exc}"
                )
            break
        # `vendor.call` is synchronous (blocking network I/O). Run it off the
        # event loop so that when several worker loops run concurrently (parent
        # `_dispatch` gathers their spawns), siblings actually overlap on the LM
        # round-trip instead of serializing. Single-loop cost is one thread hop.
        cycle_started_at = time.time()
        lm_started = time.perf_counter()
        effort = await asyncio.to_thread(
            _call_with_effort,
            vendor,
            messages,
            cycle_tools,
            floor=ceiling if effort_escalated else base_floor,
            ceiling=ceiling,
        )
        lm_ms = int((time.perf_counter() - lm_started) * 1000)
        resp = effort.response
        cycle_ended_at = time.time()
        if resp.stop_reason == "max_tokens" or len(effort.attempts) > 1:
            effort_escalated = True
        usage = _cycle_token_usage(
            effort.attempts, input_tokens_est,
        )
        if (
            role == "agent"
            and orchestration is not None
            and orchestration.depth == 0
            and orchestration.goal_state is not None
        ):
            orchestration.goal_state.record_root_usage(
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
            )
        if stats is not None:
            stats.record_cycle(
                lm_ms=lm_ms,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
            )
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
        _log_cycle(
            log, cycle, resp.stop_reason, tool_uses, resp.usage,
            tokens_out=usage.tokens_out,
            attempt_count=len(effort.attempts),
        )
        _log_cycle_cost(
            log, cycle, lm_ms, usage.tokens_in, usage.tokens_out, budget,
        )

        text = _extract_text(resp.content)
        if text:
            last_text = text  # remember even when the model also called a tool

        try:
            _charge_budget_postflight(
                budget, usage.tokens_in + usage.tokens_out, log,
            )
        except TokenBudgetExhaustedError:
            _safe_record_agent_cycle(
                db_path=compression_db_path,
                session_id=audit_session_id,
                worker_id=audit_worker_id,
                stage=audit_stage,
                cycle_idx=cycle,
                started_at=cycle_started_at,
                ended_at=cycle_ended_at,
                lm_ms=lm_ms,
                usage=usage,
                tool_names=[],
                text_chars=len(text),
                cycle_status="budget_halt",
                log=log,
            )
            raise

        if not tool_uses and recovery_outcome is not None:
            _safe_record_agent_cycle(
                db_path=compression_db_path,
                session_id=audit_session_id,
                worker_id=audit_worker_id,
                stage=audit_stage,
                cycle_idx=cycle,
                started_at=cycle_started_at,
                ended_at=cycle_ended_at,
                lm_ms=lm_ms,
                usage=usage,
                tool_names=[],
                text_chars=len(text),
                cycle_status="recovery_halt",
                log=log,
            )
            final_answer = _recovery_incomplete(
                recovery_outcome,
                "root ended recovery without a successful replacement worker",
            )
            break

        if not tool_uses:
            _safe_record_agent_cycle(
                db_path=compression_db_path,
                session_id=audit_session_id,
                worker_id=audit_worker_id,
                stage=audit_stage,
                cycle_idx=cycle,
                started_at=cycle_started_at,
                ended_at=cycle_ended_at,
                lm_ms=lm_ms,
                usage=usage,
                tool_names=[],
                text_chars=len(text),
                cycle_status="final",
                log=log,
            )
            final_answer = text
            break

        if resp.stop_reason == "max_tokens":
            _safe_record_agent_cycle(
                db_path=compression_db_path,
                session_id=audit_session_id,
                worker_id=audit_worker_id,
                stage=audit_stage,
                cycle_idx=cycle,
                started_at=cycle_started_at,
                ended_at=cycle_ended_at,
                lm_ms=lm_ms,
                usage=usage,
                tool_names=[],
                text_chars=len(text),
                cycle_status="truncated",
                log=log,
            )
            dropped = ", ".join(
                _dropped_tool_target(tu) for tu in tool_uses
            )
            print(
                "[agent] max_tokens truncated the response; dropped "
                f"{dropped} (args may be incomplete). For a large artifact, "
                "write it in ordered append_file chunks or a compact generator.",
                file=log,
            )
            blocked = _truncated_tool_call_answer(tool_uses)
            if cycle + 1 >= max_cycles:
                final_answer = blocked
                break
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.get("id", ""),
                        "content": blocked,
                    }
                    for tu in tool_uses
                ],
            })
            continue

        if recovery_decision_only and any(
            tu.get("name") != "musubi_spawn_subagent" for tu in tool_uses
        ):
            _safe_record_agent_cycle(
                db_path=compression_db_path,
                session_id=audit_session_id,
                worker_id=audit_worker_id,
                stage=audit_stage,
                cycle_idx=cycle,
                started_at=cycle_started_at,
                ended_at=cycle_ended_at,
                lm_ms=lm_ms,
                usage=usage,
                tool_names=[],
                text_chars=len(text),
                cycle_status="recovery_halt",
                log=log,
            )
            final_answer = _recovery_incomplete(
                recovery_outcome,
                "root used its two recovery-analysis cycles without "
                "starting a replacement worker",
            )
            break

        outcomes_before = len(root_state.outcomes) if root_state is not None else 0
        tool_results = await _dispatch(
            session, tool_uses, log,
            vendor=vendor, tools=(spawn_catalog or tools),
            orchestration=orchestration, gateway=gateway,
            compression_db_path=compression_db_path,
            role=role,
            scope_hint=scope_hint,
            cycle_index=cycle,
            budget=budget,
            stats=stats,
            audit_db_path=audit_db_path,
        )
        recovery_halt: str | None = None
        if role == "agent" and orchestration is not None and orchestration.depth == 0:
            requested_replacement = any(
                tu.get("name") == "musubi_spawn_subagent" for tu in tool_uses
            )
            pending_failure = orchestration.latest_unrecovered_failure()
            if requested_replacement or pending_failure is None:
                orchestration.root_recovery_analysis_cycles = 0
            else:
                orchestration.root_recovery_analysis_cycles += 1
                if (
                    orchestration.root_recovery_analysis_cycles
                    == DEFAULT_MAX_ROOT_RECOVERY_ANALYSIS_CYCLES
                ):
                    tool_results.append({
                        "type": "text",
                        "text": (
                            "[recovery decision required] The bounded analysis "
                            "window is exhausted. On the next cycle, either "
                            "spawn a replacement worker or return a final "
                            "incomplete status; analysis tools are unavailable."
                        ),
                    })

            last_outcome = (
                orchestration.worker_outcomes[-1]
                if orchestration.worker_outcomes else None
            )
            if (
                last_outcome is not None
                and last_outcome.status in {"failed", "escalated"}
                and orchestration.spawned_workers >= orchestration.max_root_workers
            ):
                recovery_halt = _recovery_incomplete(
                    last_outcome,
                    f"root worker ceiling ({orchestration.max_root_workers}) "
                    "was exhausted before the artifact could be completed",
                )
        _safe_record_agent_cycle(
            db_path=compression_db_path,
            session_id=audit_session_id,
            worker_id=audit_worker_id,
            stage=audit_stage,
            cycle_idx=cycle,
            started_at=cycle_started_at,
            ended_at=cycle_ended_at,
            lm_ms=lm_ms,
            usage=usage,
            tool_names=[str(tu.get("name", "")) for tu in tool_uses],
            text_chars=len(text),
            cycle_status="recovery_halt" if recovery_halt else "ok",
            log=log,
        )
        if root_state is not None and len(root_state.outcomes) > outcomes_before:
            messages = _compact_root_goal_messages(messages, root_state)
            print(
                "[agent] root goal-state compacted "
                f"outcomes={len(root_state.outcomes)} "
                f"chars={sum(len(str(item)) for item in messages)} "
                f"tools={len(cycle_tools)}",
                file=log,
            )
        else:
            messages.append({"role": "user", "content": tool_results})
        if recovery_halt is not None:
            final_answer = recovery_halt
            break

    # Salvage (root only — sub-agents signal exhaustion via None → escalate).
    # A model that calls a tool on EVERY cycle never hits the break path, so
    # `final_answer` stays None. Recover rather than hard-failing the turn:
    if final_answer is None and salvage_on_exhaust:
        if last_text:
            # It produced text alongside its tool calls — return the last of it.
            print(
                f"[agent] cycles exhausted ({max_cycles}); salvaging last "
                f"assistant text",
                file=log,
            )
            final_answer = last_text
        else:
            # It only ever tool-called, never spoke. Make ONE final call with no
            # tools offered so the model is forced to answer in words.
            print(
                f"[agent] cycles exhausted ({max_cycles}); forcing a no-tools "
                f"final answer",
                file=log,
            )
            try:
                if context_budget_chars is None:
                    final_messages = fit_context(
                        messages, compression_db_path=compression_db_path,
                    )
                else:
                    # No-tools final answer: the whole budget is for messages.
                    final_messages = fit_model_input(
                        messages,
                        [],
                        budget_chars=context_budget_chars,
                        compression_db_path=compression_db_path,
                    )
                input_tokens_est = _estimate_input_tokens(final_messages, [])
                try:
                    _check_budget_preflight(budget, input_tokens_est, log)
                except TokenBudgetExhaustedError as exc:
                    final_answer = (
                        "[incomplete] token budget exhausted before the final "
                        f"no-tools answer: {exc}"
                    )
                    print(final_answer, file=log)
                    raise
                cycle_started_at = time.time()
                lm_started = time.perf_counter()
                effort = await asyncio.to_thread(
                    _call_with_effort,
                    vendor,
                    final_messages,
                    [],
                    floor=ceiling if effort_escalated else base_floor,
                    ceiling=ceiling,
                )
                lm_ms = int((time.perf_counter() - lm_started) * 1000)
                resp = effort.response
                usage = _cycle_token_usage(
                    effort.attempts, input_tokens_est,
                )
                if stats is not None:
                    stats.record_cycle(
                        lm_ms=lm_ms,
                        tokens_in=usage.tokens_in,
                        tokens_out=usage.tokens_out,
                    )
                _log_cycle_cost(
                    log, max_cycles, lm_ms,
                    usage.tokens_in, usage.tokens_out, budget,
                )
                final_candidate = _extract_text(resp.content) or None
                cycle_ended_at = time.time()
                try:
                    _charge_budget_postflight(
                        budget, usage.tokens_in + usage.tokens_out, log,
                    )
                except TokenBudgetExhaustedError:
                    _safe_record_agent_cycle(
                        db_path=compression_db_path,
                        session_id=audit_session_id,
                        worker_id=audit_worker_id,
                        stage=audit_stage,
                        cycle_idx=max_cycles,
                        started_at=cycle_started_at,
                        ended_at=cycle_ended_at,
                        lm_ms=lm_ms,
                        usage=usage,
                        tool_names=[],
                        text_chars=len(final_candidate or ""),
                        cycle_status="budget_halt",
                        log=log,
                    )
                    raise
                _safe_record_agent_cycle(
                    db_path=compression_db_path,
                    session_id=audit_session_id,
                    worker_id=audit_worker_id,
                    stage=audit_stage,
                    cycle_idx=max_cycles,
                    started_at=cycle_started_at,
                    ended_at=cycle_ended_at,
                    lm_ms=lm_ms,
                    usage=usage,
                    tool_names=[],
                    text_chars=len(final_candidate or ""),
                    cycle_status="final",
                    log=log,
                )
                final_answer = final_candidate
            except Exception as exc:  # noqa: BLE001 — fall through to the raise
                print(
                    f"[agent] forced final call failed: "
                    f"{type(exc).__name__}: {exc}",
                    file=log,
                )
            if final_answer is None:
                final_answer = (
                    f"[incomplete] agent reached {max_cycles} cycles without "
                    "a final answer. The model kept requesting tools, so "
                    "Musubi stopped the loop instead of continuing indefinitely."
                )

    return final_answer, cycles_used


async def run_unit(
    session: ClientSession,
    vendor: LMRouter,
    tools: list[dict[str, Any]],
    *,
    system_prompt: str,
    user_message: str | None,
    max_cycles: int,
    log: Any,
    orchestration: Orchestration | None = None,
    gateway: McpGateway | None = None,
    spawn_catalog: list[dict[str, Any]] | None = None,
    salvage_on_exhaust: bool = False,
    compression_db_path: Path | None = None,
    context_budget_chars: int | None = None,
    initial_messages: list[dict[str, Any]] | None = None,
    role: str = "agent",
    scope_hint: ScopeHint | None = None,
    stats: AgentRunStats | None = None,
    budget: TokenBudgetEnforcer | None = None,
    audit_db_path: Path | None = None,
    worker_max_output: int | None = None,
    model_output_override: int | None = None,
    audit_session_id: str | None = None,
    audit_worker_id: str = "root",
    audit_stage: str | None = None,
) -> tuple[str | None, int]:
    """Run one *worker* on a prepared prompt. Returns (answer_or_None, cycles).

    A "worker" is the single unit of agentic work — there is no "main agent"
    vs "sub-agent" distinction, only workers at different depths. The root task
    is the depth-0 worker; every spawned child is the same object one level
    down. This is the one entry both go through; `_run_loop` is its engine.

    Prompt shape is the only thing that varies by depth:
      - root worker: `system_prompt` = the agent identity, `user_message` = the
        task → seeds messages as [system, user].
      - child worker: `system_prompt` already embeds the firewalled brief (built
        by `build_subagent_system_prompt`) and `user_message` is None → seeds a
        single user turn, preserving the child message shape exactly.

    `orchestration`/`gateway`/`spawn_catalog` are forwarded to `_run_loop`: a
    leaf worker passes orchestration=None and a restricted tool surface; a
    nesting worker passes an orchestration one level deeper plus the full
    catalog so its own children can be sized.
    """
    if initial_messages is not None:
        messages = [dict(message) for message in initial_messages]
    elif user_message is None:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": system_prompt}
        ]
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    return await _run_loop(
        session, vendor, tools, messages,
        max_cycles=max_cycles, log=log,
        orchestration=orchestration, gateway=gateway,
        spawn_catalog=spawn_catalog,
        salvage_on_exhaust=salvage_on_exhaust,
        compression_db_path=compression_db_path,
        context_budget_chars=context_budget_chars,
        role=role,
        scope_hint=scope_hint,
        stats=stats,
        budget=budget,
        audit_db_path=audit_db_path,
        worker_max_output=worker_max_output,
        model_output_override=model_output_override,
        audit_session_id=audit_session_id,
        audit_worker_id=audit_worker_id,
        audit_stage=audit_stage,
    )


async def _open_parent_session(
    session: ClientSession,
    task: str,
    log: Any,
    chat_id: str | None = None,
) -> str | None:
    """Create the agent's owning session; None if it can't (spawns disabled)."""
    try:
        args = {"request": task[:500]}
        if chat_id:
            args["chat_id"] = chat_id
        raw = await _call_tool_text(session, "musubi_new_session", args)
        sid = json.loads(raw).get("session_id")
        print(f"[agent] parent session={sid}", file=log)
        return sid if isinstance(sid, str) else None
    except Exception as exc:  # noqa: BLE001 — degrade to no-spawn, don't crash
        print(f"[agent] could not open parent session ({exc}); sub-agents disabled", file=log)
        return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def _force_utf8_streams() -> None:
    """Make stdout/stderr encode any character the model emits.

    Windows consoles default to a legacy code page (e.g. cp1252) that cannot
    encode emoji or other non-Latin-1 characters, so ``print(answer)`` raises
    ``UnicodeEncodeError`` and crashes the CLI. Force UTF-8 with a replacement
    fallback on both streams; a stream that cannot be reconfigured is left as-is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    ap = argparse.ArgumentParser(
        prog="agent-agent",
        description=(
            "Drive the Musubi MCP server via a direct LLM API "
            "(no Copilot Chat required)."
        ),
    )
    ap.add_argument("task", help="The user task to run.")
    ap.add_argument(
        "--profile",
        default=None,
        help=(
            "Named endpoint from .musubi/llm.json as <family>.<name> "
            "(e.g. azure.work). The only endpoint switch — vendor, model, and "
            "api-key are set in the profile. Omit to use the file's `default`."
        ),
    )
    ap.add_argument(
        "--musubi",
        type=Path,
        default=None,
        help=(
            "Path to the Musubi package directory (the one with server.py). "
            "Defaults to this module's parent — i.e. the installed package."
        ),
    )
    ap.add_argument(
        "--max-cycles",
        type=int,
        default=DEFAULT_MAX_CYCLES,
        help=f"Cycle-loop cap. Default {DEFAULT_MAX_CYCLES}.",
    )
    ap.add_argument(
        "--mcp-config",
        type=Path,
        default=None,
        help=(
            "Path to an mcp.json (standard `mcpServers` schema) declaring "
            "external MCP servers to federate. Defaults to $MUSUBI_MCP_CONFIG, "
            "then ./.mcp.json, ./.musubi/mcp.json, ~/.musubi/mcp.json "
            "(the feature is off when none exists)."
        ),
    )
    ap.add_argument(
        "--chat-id",
        default=None,
        help=(
            "Persist and replay this CLI conversation id across turns. "
            "Omit for a one-shot turn."
        ),
    )
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help=(
            "Per-turn total token cap. Defaults to MUSUBI_AGENT_MAX_TOKENS, "
            f"then {DEFAULT_AGENT_MAX_TOKENS}. Use 0 to disable."
        ),
    )
    ap.add_argument(
        "--tool-surface",
        choices=["agent", "operator", "full"],
        default=None,
        help="Local Musubi tool catalog exposed to the model; default agent.",
    )
    ap.add_argument(
        "--pipeline",
        default=None,
        metavar="NAME",
        help=(
            "Run the named pipeline directly (a linear recipe under "
            ".github/pipelines/<name>, e.g. feature-dev or dev-lite) with the "
            "task as its brief, instead of the model-routed single-agent loop. "
            "Pipelines needing per-file fan-out (e.g. code-review) are not "
            "supported by this deterministic runner."
        ),
    )
    ap.add_argument(
        "--plan",
        action="store_true",
        help=(
            "Request a plan-first workflow: the root spawns a planner before any "
            "coder and passes its summary along. Explicit opt-in intent — the "
            "loop no longer forces a planner by guessing scope from keywords."
        ),
    )
    args = ap.parse_args(argv)

    try:
        vendor, vendor_source = _resolve_vendor(args.profile)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"agent-agent: {exc}", file=sys.stderr)
        return 2

    musubi_dir = args.musubi or _default_musubi_dir()
    if not (musubi_dir / "server.py").is_file():
        print(
            f"agent-agent: server.py not found under {musubi_dir} "
            f"(set --musubi or MUSUBI_ROOT)",
            file=sys.stderr,
        )
        return 2

    try:
        answer = asyncio.run(
            run_agent(
                args.task, vendor, musubi_dir,
                max_cycles=args.max_cycles, mcp_config=args.mcp_config,
                vendor_source=vendor_source,
                chat_id=args.chat_id,
                max_tokens=args.max_tokens,
                tool_surface=args.tool_surface,
                pipeline=args.pipeline,
                plan_first=args.plan,
            )
        )
    except KeyboardInterrupt:
        print("\n[agent] cancelled.", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"agent-agent: {exc}", file=sys.stderr)
        return 1

    print(answer)
    return 0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_vendor(profile: str | None) -> tuple[LMRouter, str]:
    """Pick the LMRouter and a human label of *how* it was selected.

    Precedence: --profile → the llm.json `default` → env-key detection (only
    when no config file exists). `--profile` is the single endpoint switch; the
    vendor, model, endpoint, and api-key all come from the selected profile in
    `.musubi/llm.json`. The label (e.g. `genai_farm.default (llm.json default)`)
    is logged at startup so the active profile is visible.
    """
    from agent.config import (
        find_config_path,
        load_profile,
        resolve_model_output_override,
    )

    def from_profile(prof: dict[str, Any]) -> LMRouter:
        resolved = build_from_profile(prof)
        resolved.max_output_tokens = resolve_model_output_override(prof)
        return resolved

    if profile:
        prof = load_profile(profile)
        label = f"{prof['family']}.{prof['profile']} (--profile)"
        return from_profile(prof), label

    if find_config_path() is not None:
        prof = load_profile(None)  # the file's `default`
        label = f"{prof['family']}.{prof['profile']} (llm.json default)"
        return from_profile(prof), label

    return build_vendor(None), "env-key auto-detect"


def _tool_surface(cli_value: str | None = None) -> str:
    raw = (cli_value or os.environ.get("MUSUBI_TOOL_SURFACE") or "agent").strip().lower()
    if raw == "pipeline":
        raise ValueError("standalone agent supports tool surfaces: agent, operator, full")
    tool_names_for_surface(raw)
    return raw


def _server_env() -> dict[str, str]:
    """Env for the spawned Musubi server: safe defaults + forwarded MUSUBI_* vars.

    The MCP stdio client passes only a safe allowlist to the child when
    `env=None` (PATH/HOME/… — no arbitrary vars), which silently dropped
    every `MUSUBI_*` flag the user set in their shell. The most visible
    casualty was `MUSUBI_COMPRESS`: it is read *inside* the server subprocess
    (`server.py::_compression_enabled`), so with it filtered out the flag had
    no effect on the standalone path no matter how it was set.

    Forward `MUSUBI_*` vars explicitly, on top of the safe defaults, so the
    server sees Musubi's own config without inheriting unrelated parent-env
    secrets.
    """
    from mcp.client.stdio import get_default_environment

    passthrough = {k: v for k, v in os.environ.items() if k.startswith("MUSUBI_")}
    return {**get_default_environment(), **passthrough}


def _server_db_path(musubi_dir: Path, server_env: dict[str, str]) -> Path:
    """Return the SQLite DB path used by the spawned Musubi server."""
    root = server_env.get("MUSUBI_ROOT")
    if root:
        return Path(root) / "data" / "musubi.db"
    return musubi_dir / "storage" / "musubi.db"


def _server_audit_db_path(musubi_dir: Path, server_env: dict[str, str]) -> Path:
    """Return the append-only audit DB path used by the spawned server."""
    root = server_env.get("MUSUBI_ROOT")
    if root:
        return Path(root) / "data" / "audit.db"
    return musubi_dir / "storage" / "audit.db"


def _default_audit_db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "storage" / "audit.db"


def _default_musubi_dir() -> Path:
    """Resolve the Musubi server dir.

    Preference order:
      1. $MUSUBI_ROOT (matches the extension's convention).
      2. The directory containing this very module — works for the
         installed-wheel case (server.py ships alongside agent/).
    """
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _build_token_budget(
    max_tokens: int | None,
    log: Any,
) -> TokenBudgetEnforcer | None:
    cap = max_tokens
    if cap is None:
        raw = os.environ.get("MUSUBI_AGENT_MAX_TOKENS", "").strip()
        if raw:
            try:
                cap = int(raw)
            except ValueError as exc:
                raise RuntimeError(
                    f"MUSUBI_AGENT_MAX_TOKENS must be an integer, got {raw!r}"
                ) from exc
        else:
            cap = DEFAULT_AGENT_MAX_TOKENS

    if cap <= 0:
        print("[agent] token budget: disabled", file=log)
        return None
    budget = TokenBudgetEnforcer(cap)
    print(
        f"[agent] token budget: {budget.max_tokens} tokens "
        f"(warn at {int(budget.warn_at_ratio * 100)}%)",
        file=log,
    )
    return budget


def _deterministic_scope_answer(
    task: str,
    scope_hint: ScopeHint,
    goal_state: GoalState | None = None,
) -> str | None:
    if scope_hint.route == "direct_answer":
        return "Hi! How can I help?"
    if scope_hint.route == "ask_scope":
        # High ambiguity halts BEFORE any parent session, model call, or worker
        # spawn: one deterministic clarifying question, zero tokens spent.
        assessment = scope_hint.assessment
        if assessment is not None and assessment.clarifying_question:
            return assessment.clarifying_question
        return "What exact target and acceptance criteria should this change satisfy?"
    if scope_hint.route == "plan_design_workflow":
        state = goal_state or GoalState.create(
            intent=task,
            scope=scope_hint.kind.value,
            route=scope_hint.route,
            assessment=scope_hint.assessment,
        )
        return _pipeline_recommendation(state)

    if scope_hint.route == "manual_destructive":
        return (
            "I cannot safely delete files from this route because deletion is "
            "destructive and there is no interactive confirmation step here.\n\n"
            "To delete them manually from the workspace root, use one of these:\n"
            "- In VS Code Explorer: select the matching files and press Delete.\n"
            "- In PowerShell: `Remove-Item -Force *-dashboard.html`\n"
            "- In cmd: `del /f *-dashboard.html`\n\n"
            f"Requested pattern/task: `{task}`"
        )
    return None


def _ensure_core_import_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _append_chat_message(
    chat_id: str,
    role: str,
    content: str,
    *,
    db_path: Path,
    log: Any,
) -> None:
    try:
        _ensure_core_import_path()
        from session import conversations
        from storage import db

        db.init_db(db_path)
        conversations.append_message(
            chat_id, role, content, db_path=db_path,
        )
    except Exception as exc:  # noqa: BLE001 - persistence must not hide answer
        print(
            f"[agent] conversation append failed: "
            f"{type(exc).__name__}: {exc}",
            file=log,
        )


def _load_chat_history(chat_id: str, *, db_path: Path, log: Any) -> dict[str, Any]:
    try:
        _ensure_core_import_path()
        from session import conversations
        from storage import db

        db.init_db(db_path)
        return conversations.get_history(
            chat_id,
            max_tokens=_chat_history_tokens(),
            db_path=db_path,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to current turn only
        print(
            f"[agent] conversation replay failed: "
            f"{type(exc).__name__}: {exc}",
            file=log,
        )
        return {"messages": [], "total_tokens": 0, "truncated": False}


def _chat_history_tokens() -> int:
    raw = os.environ.get("MUSUBI_CHAT_HISTORY_TOKENS", "").strip()
    if raw.isdigit():
        return int(raw)
    return 50_000


def _messages_from_chat_history(
    system_prompt: str,
    history: dict[str, Any],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for row in history.get("messages") or []:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        content = str(row.get("content", ""))
        if not content:
            continue
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": content})
        elif role == "tool":
            # C3 — a prior turn's large tool output (an artifact dump, a wide
            # grep) has no reason to re-enter this turn's seed verbatim; the root
            # accepts on the worker summary + mechanical signal, not a re-ingest.
            # Cap it so replay carries the shape, not the whole payload.
            body = _elide_replayed_tool_row(content)
            messages.append({"role": "user", "content": f"[prior tool result]\n{body}"})
        elif role == "system":
            messages.append({"role": "user", "content": f"[prior system note]\n{content}"})
    return messages


# Cap for a single prior tool result re-injected as replay seed. Large payloads
# (artifact contents, wide greps) carry no goal-acceptance value on the next
# turn — the root already got the worker's summary and mechanical verdict.
REPLAY_TOOL_ROW_MAX_CHARS = 2000


def _elide_replayed_tool_row(content: str) -> str:
    if len(content) <= REPLAY_TOOL_ROW_MAX_CHARS:
        return content
    elided = len(content) - REPLAY_TOOL_ROW_MAX_CHARS
    return (
        content[:REPLAY_TOOL_ROW_MAX_CHARS]
        + f"\n…[{elided} chars elided on replay]"
    )


def _record_agent_turn(
    *,
    chat_id: str,
    parent_session_id: str | None,
    started_at: float,
    ended_at: float,
    model_family: str,
    stats: AgentRunStats,
    db_path: Path,
    log: Any,
) -> None:
    try:
        _ensure_core_import_path()
        from storage import db

        db.init_db(db_path)
        db.insert_agent_turn(
            chat_id=chat_id,
            parent_session_id=parent_session_id or "unavailable",
            started_at=started_at,
            ended_at=ended_at,
            model_family=model_family,
            cycles=stats.cycles,
            tokens_in_estimate=stats.tokens_in_estimate,
            tokens_out_estimate=stats.tokens_out_estimate,
            lm_ms=stats.lm_ms,
            total_ms=int((ended_at - started_at) * 1000),
            db_path=db_path,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry is non-fatal
        print(
            f"[agent] agent_turn write failed: {type(exc).__name__}: {exc}",
            file=log,
        )


def _mcp_to_anthropic_tool(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


def _extract_text(content_blocks: list[dict[str, Any]]) -> str:
    parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "".join(parts).strip()


def _clean_error(exc: BaseException) -> str:
    """A readable one-line message for a loop failure.

    Unwraps an anyio/Exception group to its first leaf so a vendor error
    (e.g. a curl proxy 407) reads as one line instead of a nested group dump.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _call_with_effort(
    vendor: LMRouter,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    floor: int,
    ceiling: int,
) -> EffortCallResult:
    """Effort routing: start at a low output-token cap, escalate only on need.

    Most cycles emit a small tool_use block, so the floor cap costs nothing
    they needed. If a call truncates (`stop_reason == "max_tokens"`), re-issue
    the same request once at the ceiling so a real answer is never cut off.
    """
    resp = vendor.call(messages, tools, max_tokens=floor)
    attempts = [resp]
    if resp.stop_reason == "max_tokens" and floor < ceiling:
        resp = vendor.call(messages, tools, max_tokens=ceiling)
        attempts.append(resp)
    return EffortCallResult(response=resp, attempts=attempts)


def _estimate_input_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> int:
    chars = len(json.dumps(messages, default=str, ensure_ascii=False))
    chars += len(json.dumps(tools, default=str, ensure_ascii=False))
    return estimate_tokens_from_chars(chars)


def _cycle_token_usage(
    responses: LMResponse | list[LMResponse],
    input_estimate: int,
) -> CycleTokenUsage:
    attempts = responses if isinstance(responses, list) else [responses]
    totals = [
        _single_response_token_usage(resp, input_estimate)
        for resp in attempts
    ]
    return CycleTokenUsage(
        tokens_in=sum(item.tokens_in for item in totals),
        cached_input_tokens=sum(item.cached_input_tokens for item in totals),
        tokens_out=sum(item.tokens_out for item in totals),
        source=(
            "provider"
            if all(item.source == "provider" for item in totals)
            else "estimated"
        ),
    )


def _single_response_token_usage(
    resp: LMResponse,
    input_estimate: int,
) -> CycleTokenUsage:
    usage = resp.usage or {}
    provider_input = _usage_int(usage, "input_tokens", "prompt_tokens")
    tokens_in = provider_input if provider_input is not None else input_estimate
    output_estimate = estimate_tokens_from_chars(
        len(json.dumps(resp.content, default=str, ensure_ascii=False))
    )
    provider_output = _usage_int(usage, "output_tokens", "completion_tokens")
    tokens_out = provider_output if provider_output is not None else output_estimate
    cached = (
        _usage_int(usage, "cache_read_input_tokens", "cached_input_tokens")
        or _nested_usage_int(usage, ("prompt_tokens_details", "cached_tokens"))
        or 0
    )
    return CycleTokenUsage(
        tokens_in=max(0, tokens_in),
        cached_input_tokens=max(0, min(cached, tokens_in)),
        tokens_out=max(0, tokens_out),
        source=(
            "provider"
            if provider_input is not None and provider_output is not None
            else "estimated"
        ),
    )


def _safe_record_agent_cycle(
    *,
    db_path: Path | None,
    session_id: str | None,
    worker_id: str,
    stage: str | None,
    cycle_idx: int,
    started_at: float,
    ended_at: float,
    lm_ms: int,
    usage: CycleTokenUsage,
    tool_names: list[str],
    text_chars: int,
    cycle_status: str,
    log: Any,
) -> None:
    if db_path is None or session_id is None or stage is None:
        return
    try:
        _ensure_core_import_path()
        from storage import db

        db.init_db(db_path)
        db.insert_agent_cycle(
            session_id,
            stage,
            attempt=1,
            cycle_idx=cycle_idx,
            started_at=started_at,
            ended_at=ended_at,
            db_path=db_path,
            worker_id=worker_id,
            lm_ms=lm_ms,
            tokens_in=usage.tokens_in,
            cached_input_tokens=usage.cached_input_tokens,
            tokens_out=usage.tokens_out,
            token_source=usage.source,
            tool_calls_json=json.dumps(tool_names),
            text_chars=text_chars,
            cycle_status=cycle_status,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry is non-fatal
        print(
            f"[agent] cycle audit write failed: {type(exc).__name__}: {exc}",
            file=log,
        )


def _usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None


def _nested_usage_int(usage: dict[str, Any], path: tuple[str, str]) -> int | None:
    value = usage.get(path[0])
    if not isinstance(value, dict):
        return None
    nested = value.get(path[1])
    if isinstance(nested, int):
        return nested
    if isinstance(nested, float):
        return int(nested)
    return None


def _no_progress_budget_trip(
    budget: Any,
    orchestration: "Orchestration",
    *,
    ratio: float = DEFAULT_NO_PROGRESS_BUDGET_RATIO,
) -> str | None:
    """Return an incomplete message if the root run should stop early.

    Fires only when ALL of these hold, so it never aborts a productive run:
      - a token budget exists with a positive ceiling;
      - at least `ratio` of that ceiling is already spent;
      - at least one worker has terminated failed/escalated;
      - NO worker has delivered a completed artifact (a `done` outcome that
        actually mutated files).
    The remaining budget will not fund a fresh successful worker when the spent
    majority produced only failures, so stopping here caps the waste a
    non-converging driver model would otherwise spend up to the hard ceiling.
    """
    max_tokens = int(getattr(budget, "max_tokens", 0) or 0)
    if max_tokens <= 0:
        return None
    if int(getattr(budget, "tokens_used", 0) or 0) < ratio * max_tokens:
        return None
    outcomes = orchestration.worker_outcomes
    if any(o.status == "done" and o.touched_files for o in outcomes):
        return None
    if not any(o.status in {"failed", "escalated"} for o in outcomes):
        return None
    return (
        "[incomplete] run stopped early: "
        f"{int(ratio * 100)}% of the token budget was spent without any worker "
        "delivering a completed artifact. The driver model did not converge — "
        "retry with a stronger model or a narrower request."
    )


def _check_budget_preflight(
    budget: TokenBudgetEnforcer | None,
    input_tokens: int,
    log: Any,
) -> None:
    if budget is None:
        return
    estimated_output = max(1, int(input_tokens * 0.25))
    estimated_tokens = input_tokens + estimated_output
    status = budget.preflight(estimated_tokens)
    if status == "allow":
        return
    projected = budget.tokens_used + estimated_tokens
    print(
        f"[agent] token budget {status}: projected={projected}/"
        f"{budget.max_tokens} tokens this_call={estimated_tokens}",
        file=log,
    )
    if status == "halt":
        raise TokenBudgetExhaustedError(
            phase="preflight",
            tokens_used=projected,
            max_tokens=budget.max_tokens,
            this_call_tokens=estimated_tokens,
        )


def _charge_budget_postflight(
    budget: TokenBudgetEnforcer | None,
    tokens: int,
    log: Any,
) -> None:
    if budget is None:
        return
    status = budget.charge(tokens)
    if status == "allow":
        return
    print(
        f"[agent] token budget {status}: used={budget.tokens_used}/"
        f"{budget.max_tokens} tokens this_call={tokens}",
        file=log,
    )
    if status == "halt":
        raise TokenBudgetExhaustedError(
            phase="postflight",
            tokens_used=budget.tokens_used,
            max_tokens=budget.max_tokens,
            this_call_tokens=tokens,
        )


async def _dispatch(
    session: ClientSession,
    tool_uses: list[dict[str, Any]],
    log: Any,
    *,
    vendor: LMRouter | None = None,
    tools: list[dict[str, Any]] | None = None,
    orchestration: Orchestration | None = None,
    gateway: McpGateway | None = None,
    compression_db_path: Path | None = None,
    role: str = "agent",
    scope_hint: ScopeHint | None = None,
    cycle_index: int = 0,
    budget: TokenBudgetEnforcer | None = None,
    stats: AgentRunStats | None = None,
    audit_db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Run every tool call in the batch CONCURRENTLY, returning tool_results in
    the original `tool_uses` order.

    Workers in one model turn run in parallel (`asyncio.gather`); a batch of N
    spawns runs N worker loops at once, each overlapping on its LM round-trip
    (see the `to_thread` in `_run_loop`). `gather` preserves input order, so the
    `tool_use_id` ↔ `tool_result` pairing the model expects stays exact no
    matter which worker finishes first.

    A per-role width guard (`DEFAULT_MAX_SPAWNS_PER_ROLE`) refuses overflow
    spawns BEFORE launch so a single turn cannot fan out without bound. Direct
    root runs also share a classifier-independent cumulative worker ceiling.
    """
    refused = _spawn_overflow_reasons(
        tool_uses,
        log,
        role=role,
        scope_hint=scope_hint,
        cycle_index=cycle_index,
        orchestration=orchestration,
    )
    if _has_order_sensitive_file_tool(tool_uses):
        settled = []
        for tu in tool_uses:
            try:
                settled.append(await _dispatch_one(
                    tu, session, log,
                    vendor=vendor, tools=tools,
                    orchestration=orchestration, gateway=gateway,
                    refused_reason=refused.get(tu.get("id", "")),
                    compression_db_path=compression_db_path,
                    role=role,
                    budget=budget,
                    stats=stats,
                    audit_db_path=audit_db_path,
                ))
            except Exception as exc:  # noqa: BLE001 - match gather semantics
                settled.append(exc)
    else:
        coros = [
            _dispatch_one(
                tu, session, log,
                vendor=vendor, tools=tools,
                orchestration=orchestration, gateway=gateway,
                refused_reason=refused.get(tu.get("id", "")),
                compression_db_path=compression_db_path,
                role=role,
                budget=budget,
                stats=stats,
                audit_db_path=audit_db_path,
            )
            for tu in tool_uses
        ]
        settled = await asyncio.gather(*coros, return_exceptions=True)

    results: list[dict[str, Any]] = []
    for tu, outcome in zip(tool_uses, settled):
        if isinstance(outcome, BaseException):
            content = f"[dispatch error] {type(outcome).__name__}: {outcome}"
        else:
            content = outcome
        results.append({
            "type": "tool_result",
            "tool_use_id": tu.get("id", ""),
            "content": content,
        })
    return results


def _has_order_sensitive_file_tool(tool_uses: list[dict[str, Any]]) -> bool:
    return any(tu.get("name") in ORDER_SENSITIVE_FILE_TOOLS for tu in tool_uses)


def _dropped_tool_target(tu: dict[str, Any]) -> str:
    """`tool(path)` for a truncated call, so the log names what was discarded."""
    name = _short_tool_name(str(tu.get("name") or "<unknown>"))
    path = (tu.get("input") or {}).get("path")
    return f"{name}({path})" if isinstance(path, str) and path else name


def _truncated_tool_call_answer(tool_uses: list[dict[str, Any]]) -> str:
    names = sorted({str(tu.get("name") or "<unknown>") for tu in tool_uses})
    payload = {
        "status": "blocked",
        "reason": "output_too_large_for_single_tool_call",
        "attempted_tools": names,
        "retry_same_strategy": False,
        "recommended_strategies": [
            "compact_artifact",
            "split_files",
            "append_chunks",
            "ask_scope",
        ],
        "message": (
            "Model output hit max_tokens while emitting tool calls, so Musubi "
            "did not dispatch possibly truncated arguments. For requested HTML "
            "or dashboard artifacts, prefer a compact direct HTML file first; "
            "use ordered musubi_append_file chunks when one file is unavoidable; "
            "do not switch to a generator script unless the user asked for one "
            "or explicitly accepts that fallback."
        ),
    }
    return "[blocked] " + json.dumps(payload, separators=(",", ":"))


def _spawn_overflow_reasons(
    tool_uses: list[dict[str, Any]],
    log: Any,
    *,
    role: str,
    scope_hint: ScopeHint | None,
    cycle_index: int = 0,
    orchestration: Orchestration | None = None,
) -> dict[str, str]:
    """tool_use ids of spawn calls that exceed the flat per-role width cap.

    The flat per-role cap bounds one batch for every worker. At root depth, a
    generic cumulative ceiling bounds the whole direct run independently of
    task classification. Scope hints remain advisory; explicit plan-first
    intent still comes from `--plan`. `cycle_index` remains for log context.
    """
    seen: dict[str, int] = {}
    overflow: dict[str, str] = {}
    for tu in tool_uses:
        if tu.get("name") != "musubi_spawn_subagent":
            continue
        spawn_role = str((tu.get("input") or {}).get("role", ""))
        seen[spawn_role] = seen.get(spawn_role, 0) + 1
        if seen[spawn_role] > DEFAULT_MAX_SPAWNS_PER_ROLE:
            reason = (
                f"per-turn spawn cap ({DEFAULT_MAX_SPAWNS_PER_ROLE}) reached "
                f"for role {spawn_role!r}"
            )
            overflow[tu.get("id", "")] = reason
            print(
                f"[agent]   ⨯ refused extra worker(role={spawn_role!r}): "
                f"{reason}",
                file=log,
            )
            continue
        # Deterministic role order (root only): on a planner-led goal the
        # coder gate opens ONLY after the planner's manifest reclassifies the
        # change. This is goal-state enforcement of an assessed route, not the
        # retired keyword guess — next_role comes from the assessment cascade,
        # and the refusal names the legal next role so the model can comply.
        if (
            role == "agent"
            and orchestration is not None
            and orchestration.depth == 0
            and orchestration.goal_state is not None
            and spawn_role == "coder"
            and orchestration.goal_state.next_role not in (None, "coder")
        ):
            legal = orchestration.goal_state.next_role
            reason = (
                f"role order: {legal!r} is the legal next role on this route; "
                f"spawn {legal!r} first — its change manifest decides whether "
                "a coder may follow"
            )
            overflow[tu.get("id", "")] = reason
            print(
                f"[agent]   ⨯ refused worker(role={spawn_role!r}): {reason}",
                file=log,
            )
            continue
        if (
            role == "agent"
            and orchestration is not None
            and orchestration.depth == 0
            and orchestration.spawned_workers >= orchestration.max_root_workers
        ):
            reason = (
                f"root worker ceiling ({orchestration.max_root_workers}) reached"
            )
            overflow[tu.get("id", "")] = reason
            print(
                f"[agent] refused extra worker(role={spawn_role!r}): {reason}",
                file=log,
            )
            continue
        if orchestration is not None and role == "agent" and orchestration.depth == 0:
            orchestration.spawned_workers += 1
    return overflow


async def _dispatch_one(
    tu: dict[str, Any],
    session: ClientSession,
    log: Any,
    *,
    vendor: LMRouter | None,
    tools: list[dict[str, Any]] | None,
    orchestration: Orchestration | None,
    gateway: McpGateway | None,
    refused_reason: str | None = None,
    refused: bool = False,
    compression_db_path: Path | None = None,
    role: str = "agent",
    budget: TokenBudgetEnforcer | None = None,
    stats: AgentRunStats | None = None,
    audit_db_path: Path | None = None,
) -> str:
    """Run a single tool call and return its result text.

    When `orchestration` is enabled and the model calls
    `musubi_spawn_subagent`, the spawn is run to completion in-process and the
    worker's summary becomes the result — so the model just spawns and gets the
    answer back. Every other tool is routed via `gateway` (when set) to its
    owning session and original name; with no gateway, the call goes to
    `session` verbatim.
    """
    name = tu.get("name", "")
    args = tu.get("input") or {}
    if refused and refused_reason is None:
        refused_role = args.get("role", "")
        refused_reason = (
            f"per-turn spawn cap ({DEFAULT_MAX_SPAWNS_PER_ROLE}) reached "
            f"for role {refused_role!r}"
        )
    session_id = orchestration.parent_session_id if orchestration else None
    audit_path = audit_db_path or _default_audit_db_path()
    call_role = (
        orchestration.parent_agent_name
        if orchestration is not None and orchestration.parent_agent_name
        else role
    )
    should_audit = is_musubi_tool(name)
    if should_audit:
        decision = evaluate_tool_call(call_role, name)
        _safe_record_policy(decision, db_path=audit_path, log=log)
        if not decision.allowed:
            denied = (
                f"[policy denied] {decision.reason}"
                f"{denied_tool_guidance(call_role, name)}"
            )
            print(f"[agent]   policy denied {name}: {decision.reason}", file=log)
            _safe_record_tool_audit(
                session_id=session_id,
                role=call_role,
                tool=name,
                args=json_args(args),
                status="denied",
                db_path=audit_path,
                result_text=denied,
                log=log,
            )
            return denied

    arg_error = _file_tool_argument_error(name, args)
    if arg_error is not None:
        result = f"[tool error] invalid arguments for {name}: {arg_error}"
        print(f"[agent]   invalid args for {name}: {arg_error}", file=log)
        if should_audit:
            _safe_record_tool_audit(
                session_id=session_id,
                role=call_role,
                tool=name,
                args=json_args(args),
                status="error",
                db_path=audit_path,
                result_text=result,
                log=log,
            )
        return result

    if (
        name == "musubi_spawn_pipeline"
        and orchestration is not None
        and orchestration.enabled
        and vendor is not None
        and tools is not None
    ):
        injected = {
            **args,
            "parent_session_id": orchestration.parent_session_id,
            "parent_agent_name": orchestration.parent_agent_name,
        }
        print(f"[agent]   → summon pipeline({args.get('pipeline_name')!r})", file=log)
        try:
            from agent import pipeline_runner

            result = await pipeline_runner.run_pipeline(
                session, injected, vendor, tools, log,
                compression_db_path=compression_db_path,
                budget=budget, stats=stats, audit_db_path=audit_db_path,
                orchestration=orchestration,
            )
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="ok", db_path=audit_path,
                result_text=result, log=log,
            )
            return result
        except Exception as exc:  # noqa: BLE001 — surface to the model
            result = f"[pipeline error] {type(exc).__name__}: {exc}"
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="error", db_path=audit_path,
                result_text=result, log=log,
            )
            return result

    if (
        name == "musubi_spawn_subagent"
        and orchestration is not None
        and orchestration.enabled
        and vendor is not None
        and tools is not None
    ):
        if refused_reason:
            result = (
                '{"status": "refused", "reason": '
                f"{json.dumps(refused_reason)}" + "}"
            )
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="refused", db_path=audit_path,
                result_text=result, log=log,
            )
            return result
        worker_args = args
        spawn_role = str(args.get("role", ""))
        prior_outcome = orchestration.latest_failed_outcome(spawn_role)
        if prior_outcome is not None:
            worker_args = {
                **args,
                "brief": _replacement_brief(
                    str(args.get("brief", "")), prior_outcome,
                ),
            }
        injected = {
            **worker_args,
            "parent_session_id": orchestration.parent_session_id,
            "parent_agent_name": orchestration.parent_agent_name,
        }
        print(f"[agent]   → spawn worker(role={args.get('role')!r})", file=log)
        try:
            from agent import subagent

            result = await subagent.run_subagent(
                session, injected, vendor, tools, log, orchestration=orchestration,
                compression_db_path=compression_db_path,
                budget=budget, stats=stats, audit_db_path=audit_db_path,
            )
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="ok", db_path=audit_path,
                result_text=result, log=log,
            )
            return result
        except Exception as exc:  # noqa: BLE001 — surface to the model
            result = f"[subagent error] {type(exc).__name__}: {exc}"
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="error", db_path=audit_path,
                result_text=result, log=log,
            )
            return result

    print(f"[agent]   → {name}({_truncate(json.dumps(args), 60)})", file=log)
    target = gateway.route(name) if gateway is not None else (session, name)
    if target is None:
        result = f"[tool error] no MCP server owns tool {name!r}"
        if should_audit:
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="error", db_path=audit_path,
                result_text=result, log=log,
            )
        return result
    target_session, original_name = target
    try:
        result = await target_session.call_tool(original_name, arguments=args)
        text = normalize_tool_result_text(_first_text(result))
        if name == "musubi_get_skill" and _skill_loaded_successfully(text):
            skill_id = str(args.get("skill_id") or "<unknown>")
            agent_name = str(args.get("agent_name") or call_role)
            print(
                f"[agent]   skill used={skill_id} agent={agent_name}",
                file=log,
            )
        if should_audit:
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="ok", db_path=audit_path,
                result_text=text, log=log,
            )
        _record_touched_file(name, args, text)
        return text
    except Exception as exc:  # noqa: BLE001 — surface errors to the model
        result = f"[tool error] {type(exc).__name__}: {exc}"
        if should_audit:
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="error", db_path=audit_path,
                result_text=result, log=log,
            )
        return result


def _tool_wrote_ok(text: str) -> bool:
    """True when an fs tool's JSON result reports a successful write."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("status") == "ok"


def _record_touched_file(name: str, args: dict[str, Any], text: str) -> None:
    """Record a successful file mutation into the active worker's sink.

    No-op unless a `run_subagent` upstream is collecting (sink is not None) and
    the call is a file-mutating tool that reported success.
    """
    sink = _worker_touched_files.get()
    if sink is None or name not in ORDER_SENSITIVE_FILE_TOOLS:
        return
    if not _tool_wrote_ok(text):
        return
    path = args.get("path")
    if isinstance(path, str) and path:
        sink.add(path)


def _file_tool_argument_error(name: str, args: Any) -> str | None:
    if name not in ORDER_SENSITIVE_FILE_TOOLS:
        return None
    if not isinstance(args, dict):
        return "arguments must be an object"

    errors: list[str] = []
    _require_string(args, "path", errors)
    if name in {"musubi_write_file", "musubi_append_file"}:
        _require_string(args, "content", errors)
        if isinstance(args.get("content"), str) and not args["content"].strip():
            errors.append(
                "content is empty; regenerate the full file content "
                "(an empty write is almost always a truncation artifact)"
            )
        _reject_elided_marker(args, "content", errors)
        _optional_bool(args, "create_parents", errors)
    elif name == "musubi_edit_file":
        _require_string(args, "old_string", errors)
        _require_string(args, "new_string", errors)
        _reject_elided_marker(args, "old_string", errors)
        _reject_elided_marker(args, "new_string", errors)
        _optional_bool(args, "replace_all", errors)

    if name == "musubi_append_file" and "expected_offset" in args:
        offset = args.get("expected_offset")
        if (
            offset is not None
            and (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0)
        ):
            errors.append("expected_offset must be a non-negative integer")

    return "; ".join(errors) if errors else None


def _require_string(args: dict[str, Any], key: str, errors: list[str]) -> None:
    if not isinstance(args.get(key), str):
        errors.append(f"{key} must be a string")


def _reject_elided_marker(
    args: dict[str, Any], key: str, errors: list[str]
) -> None:
    if is_elided_tool_arg_marker(args.get(key)):
        errors.append(
            f"{key} is an elided tool argument marker; regenerate the original "
            "content instead of copying replay-only context"
        )


def _optional_bool(args: dict[str, Any], key: str, errors: list[str]) -> None:
    if key in args and not isinstance(args.get(key), bool):
        errors.append(f"{key} must be a boolean")


async def _call_tool_text(
    session: ClientSession, name: str, args: dict[str, Any]
) -> str:
    """Call an MCP tool and return its first text chunk (raises on transport error)."""
    result = await session.call_tool(name, arguments=args)
    return _first_text(result)


def _first_text(call_result: Any) -> str:
    """Pull the first text chunk out of an MCP CallToolResult."""
    for c in getattr(call_result, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            return text
    return ""


def normalize_tool_result_text(text: str) -> str:
    """Return a compact, deterministic tool result string for the next LM call."""
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        parsed = json.loads(stripped)
    except (TypeError, json.JSONDecodeError):
        return re.sub(r"\n{3,}", "\n\n", stripped)
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _skill_loaded_successfully(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return True
    return not (isinstance(payload, dict) and "error" in payload)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_record_policy(decision: Any, *, db_path: Path, log: Any) -> None:
    try:
        record_policy_decision(decision, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - audit must not crash tool result
        print(
            f"[agent] policy audit write failed: {type(exc).__name__}: {exc}",
            file=log,
        )


def _safe_record_tool_audit(
    *,
    session_id: str | None,
    role: str,
    tool: str,
    args: dict[str, Any],
    status: str,
    db_path: Path,
    result_text: str | None,
    log: Any,
) -> None:
    try:
        record_tool_audit(
            session_id=session_id,
            role=role,
            tool=tool,
            args=args,
            status=status,
            db_path=db_path,
            result_text=result_text,
        )
    except Exception as exc:  # noqa: BLE001 - audit must not hide tool result
        print(
            f"[agent] tool audit write failed: {type(exc).__name__}: {exc}",
            file=log,
        )


# Name-substring hints used to bucket a tool call into a coarse kind, so a
# cycle that only reads/greps (a verification loop) is visible at a glance
# versus one that mutates files or spawns a worker.
_MUTATE_HINTS = ("write", "edit", "patch", "bash", "delete", "create", "move", "mkdir")
_READ_HINTS = ("read", "grep", "glob", "retrieve", "list", "get", "search", "find")


def _short_tool_name(name: str) -> str:
    """Drop a leading ``musubi_`` prefix for readable logs; leave others intact."""
    return name[len("musubi_"):] if name.startswith("musubi_") else name


def _tool_kind(name: str) -> str:
    if name in ("musubi_spawn_subagent", "musubi_spawn_pipeline"):
        return "spawn"
    low = name.lower()
    if any(h in low for h in _MUTATE_HINTS):
        return "mutate"
    if any(h in low for h in _READ_HINTS):
        return "read"
    return "other"


def _tool_kind_summary(tool_uses: list[dict[str, Any]]) -> str:
    kinds = {_tool_kind(str(tu.get("name", ""))) for tu in tool_uses}
    if len(kinds) == 1:
        return next(iter(kinds))
    if "mutate" in kinds:
        return "mutate"
    if "spawn" in kinds:
        return "spawn"
    return "mixed"


def _tool_use_names(tool_uses: list[dict[str, Any]]) -> str:
    """Compact ``[grep×3, read_file×2]`` breakdown, insertion-ordered."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for tu in tool_uses:
        name = _short_tool_name(str(tu.get("name", "")) or "?")
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1
    parts = [f"{n}×{counts[n]}" if counts[n] > 1 else n for n in order]
    return "[" + ", ".join(parts) + "]"


def _model_action(stop_reason: str, tool_uses: list[dict[str, Any]]) -> str:
    if stop_reason == "max_tokens":
        return "truncated"
    if tool_uses:
        return f"tool_calls:{_tool_kind_summary(tool_uses)}"
    if stop_reason == "end_turn":
        return "final"
    return "empty"


def _log_cycle(
    log: Any,
    cycle: int,
    stop_reason: str,
    tool_uses: list[dict[str, Any]],
    usage: dict[str, Any] | None,
    *,
    tokens_out: int | None = None,
    attempt_count: int = 1,
) -> None:
    parts = [
        f"[agent] [{_worker_log_label.get()}] cycle {cycle}: "
        f"model_action={_model_action(stop_reason, tool_uses)}",
        f"stop={stop_reason}",
        f"tools={len(tool_uses)}",
    ]
    if tool_uses:
        parts.append(f"names={_tool_use_names(tool_uses)}")
    if attempt_count > 1:
        parts.append(f"attempts={attempt_count}")
    if tokens_out is not None:
        parts.append(f"out_tokens={tokens_out}")
    if usage:
        # CacheAligner measurement: how much of the prefix was served from the
        # prompt cache vs. (re)written this cycle.
        cache_read = usage.get("cache_read_input_tokens")
        cache_write = usage.get("cache_creation_input_tokens")
        if cache_read:
            parts.append(f"cache_read={cache_read}")
        if cache_write:
            parts.append(f"cache_write={cache_write}")
    print(" ".join(parts), file=log)


def _log_cycle_cost(
    log: Any,
    cycle: int,
    lm_ms: int,
    tokens_in: int,
    tokens_out: int,
    budget: TokenBudgetEnforcer | None,
) -> None:
    parts = [
        f"[agent] [{_worker_log_label.get()}] cycle {cycle}: lm_ms={lm_ms}",
        f"in_tokens={tokens_in}",
        f"out_tokens={tokens_out}",
    ]
    if budget is not None:
        parts.append(
            f"token_budget={budget.tokens_used}/{budget.max_tokens}"
        )
    print(" ".join(parts), file=log)


def _log_turn_usage(
    log: Any,
    stats: AgentRunStats,
    budget: TokenBudgetEnforcer | None,
) -> None:
    if stats.cycles <= 0:
        return
    parts = [
        f"[agent] usage cycles={stats.cycles}",
        f"lm_ms={stats.lm_ms}",
        f"in_tokens={stats.tokens_in_estimate}",
        f"out_tokens={stats.tokens_out_estimate}",
    ]
    if budget is not None:
        parts.append(
            f"token_budget={budget.tokens_used}/{budget.max_tokens}"
        )
    print(" ".join(parts), file=log)


if __name__ == "__main__":
    raise SystemExit(main())
