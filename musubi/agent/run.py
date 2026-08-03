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
from workspace.grants import (
    MANIFEST_ENV,
    FolderGrant,
    RootRegistry,
    derive_alias,
)

from agent.context import (
    build_system_prompt,
    fit_context,
    fit_model_input,
    is_elided_tool_arg_marker,
    resolve_effort_bounds,
)
from agent.goal_state import (
    MUTATION_ROLES,
    NO_PROGRESS_TURN_THRESHOLD,
    ORDERED_ROLES,
    GoalState,
    root_decision_tools,
)
from agent.blast_radius import (
    DestructiveGate,
    approved_keys_from,
    covered_by,
    describe,
    encode_pending,
    exceeds_threshold,
    grant_token,
    measure,
)
from agent.budget import (
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_tokens_from_chars,
)
from agent.runtime_log import RuntimeLogWriter, emit_runtime_log
from agent.boundary import (
    ROOT_ROLE,
    PolicyDecision,
    denied_tool_guidance,
    evaluate_tool_call,
    evaluate_argument_policy,
    is_musubi_tool,
    json_args,
    record_policy_decision,
    normalize_role,
    record_tool_audit,
)
from agent.mcp_gateway import (
    McpGateway,
    find_mcp_config_path,
    load_mcp_servers,
    mcp_config_candidates,
    mcp_tool_to_schema,
)
from agent.manifest import (
    ROOT_PLAN_CHANGE_SIZES,
    ROOT_PLAN_WORKER_ROLES,
    manifest_schema,
    parse_change_manifest_object,
)
from agent.evidence import collect as collect_evidence
from agent.planning_artifacts import (
    goal_artifact_key,
    persist_planning_artifacts,
    persist_planning_contract,
)
from agent.routes import RouteKind
from agent.textfmt import bounded
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
#: Ceiling once a manifest reclassifies the goal as large. The chain is
#: planner → designer → coder → reviewer (four workers, one more than the
#: default), plus headroom for a single recovery replacement.
DEFAULT_MAX_ROOT_WORKERS_LARGE = 6
MAX_ROOT_WORKERS_HARD = 8
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
    "The user explicitly requested Planning mode (--plan). Your first tool "
    "call must be `musubi_begin_plan`; `musubi_begin_direct` is invalid. Read "
    "the bounded workspace facts yourself, then commit plan.md, manifest.json, "
    "change size, and the ordered worker chain with `musubi_commit_plan`."
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

# Same sink pattern for the one thing a worker can say ABOUT its own contract
# rather than about the workspace: that the skill pushed into it does not fit
# the brief it was given. HI #2 still holds — the push happened, the worker
# still runs under it, and it cannot swap its own skill. What changes is that
# the mismatch becomes control flow the parent can read, instead of a fact only
# the worker knew and had no way to state.
_worker_skill_reports: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("musubi_worker_skill_reports", default=None)
)

# O3 — a short label identifying whose cycle a log line belongs to. `run_subagent`
# sets `<role>#<handle>` for the worker it runs; the root leaves the default. Read
# by the cycle loggers so several "cycle 0" lines from different workers are
# distinguishable. Same ContextVar pattern as above — no loop-signature changes.
_worker_log_label: contextvars.ContextVar[str] = (
    contextvars.ContextVar("musubi_worker_log_label", default="root")
)

# The destructive gate's state for one run. Unlike the two above, this sink is
# set ONCE by `run_agent` and deliberately NOT re-set per worker: every worker
# shares it. That is the point — the overwrite ceiling counts across the run,
# an approval the user grants covers the coder the root dispatches, and a
# refusal raised inside a leaf reaches the turn record that persists its token.
# Threading it as a parameter would mean touching ten call sites and would
# still miss leaves, which are handed `orchestration=None` by design.
_destructive_gate: contextvars.ContextVar[DestructiveGate | None] = (
    contextvars.ContextVar("musubi_destructive_gate", default=None)
)

#: How deep workers may nest. depth 0 = root task; a worker at depth < max_depth
#: that is itself allowed to spawn may summon workers one level down. With the
#: default, the root and its direct workers can spawn; their workers are leaves.
DEFAULT_MAX_DEPTH = 2


class PolicyDeniedError(RuntimeError):
    """Terminal policy control flow; never expose it as a tool-result string."""

    def __init__(self, *, role: str, tool: str, reason: str) -> None:
        self.role = role
        self.tool = tool
        self.reason = reason
        super().__init__(f"{role} denied {tool}: {reason}")


def _policy_incomplete(error: PolicyDeniedError) -> str:
    return (
        f"[incomplete] policy denied for role {error.role!r} while calling "
        f"{error.tool!r}: {error.reason}"
    )


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
    parent_agent_name: str = ROOT_ROLE
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    spawned_workers: int = 0
    max_root_workers: int = DEFAULT_MAX_ROOT_WORKERS
    root_recovery_analysis_cycles: int = 0
    worker_outcomes: list[WorkerOutcome] = field(default_factory=list)
    goal_state: GoalState | None = None
    pipeline_name: str | None = None
    planning_artifact_dir: Path | None = None
    # The destructive gate's state deliberately does NOT live here. See
    # `_destructive_gate` below: it is run-scoped, and an Orchestration
    # describes a position in the spawn tree — the one thing the gate must be
    # blind to, since leaf workers carry no Orchestration at all.

    @property
    def enabled(self) -> bool:
        return self.parent_session_id is not None

    def child(self, role: str) -> "Orchestration":
        """Orchestration for a worker this one spawns: same root session, the
        child's role as the new firewall identity, one level deeper."""
        return Orchestration(
            parent_session_id=self.parent_session_id,
            parent_agent_name=role,
            pipeline_name=self.pipeline_name,
            planning_artifact_dir=self.planning_artifact_dir,
            depth=self.depth + 1,
            max_depth=self.max_depth,
        )

    def stage_child(
        self, role: str, pipeline_session_id: str,
        pipeline_name: str | None = None,
    ) -> "Orchestration":
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
            pipeline_name=pipeline_name,
            planning_artifact_dir=self.planning_artifact_dir,
            depth=self.depth + 1,
            max_depth=self.max_depth,
        )

    @property
    def can_spawn_deeper(self) -> bool:
        """True if a worker at this depth is still allowed to nest."""
        return self.enabled and self.depth < self.max_depth

    @property
    def delivered_artifact(self) -> bool:
        """True when some worker this turn finished with files on disk.

        Persisted per turn so a LATER turn in the same conversation can see a
        run of turns that spent tokens and produced nothing.
        """
        return any(
            outcome.status == "done" and outcome.touched_files
            for outcome in self.worker_outcomes
        )

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
            # Post-plan reclassification: a planner-led goal persists the
            # plan/manifest pair the moment it lands. The manifest verdict
            # (not the lexical guess) then owns route, scope, and the legal
            # next mutation role; a missing/invalid pair fails closed before
            # a coder can start.
            if role == "planner" and status == "done" and (
                self.goal_state.next_role == "planner"
            ):
                paths = None
                if self.planning_artifact_dir is not None:
                    try:
                        paths = persist_planning_artifacts(
                            summary,
                            self.planning_artifact_dir,
                        )
                    except OSError as exc:
                        self.goal_state.reject_planning_artifacts(
                            "The planner produced a valid plan, but Musubi "
                            f"could not persist it: {type(exc).__name__}. "
                            "Resolve the workspace write error before retrying."
                        )
                if self.planning_artifact_dir is None:
                    # Unit-level orchestration callers may omit persistence;
                    # production run_agent always supplies the directory.
                    self.goal_state.apply_planner_manifest(summary)
                elif paths is None and self.goal_state.pending_clarification is None:
                    self.goal_state.reject_planning_artifacts(
                        "The planner must produce both a non-empty <plan> "
                        "block and one valid <change_manifest> block before "
                        "implementation can start."
                    )
                elif paths is not None:
                    self.goal_state.planning_artifacts = tuple(
                        str(path) for path in paths
                    )
                    self.goal_state.apply_planner_manifest(summary)
                if self.goal_state.role_chain or (
                    self.goal_state.route == RouteKind.PLAN_DESIGN_WORKFLOW
                ):
                    # A large change owes designer → coder → reviewer after the
                    # planner. That is four workers, one more than the default
                    # ceiling, so the chain would be refused on its last step.
                    # Raise the ceiling to fit the chain plus headroom for one
                    # recovery replacement.
                    self.max_root_workers = max(
                        self.max_root_workers, DEFAULT_MAX_ROOT_WORKERS_LARGE,
                    )
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
            role=ROOT_ROLE,
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
    request_id: str | None = None,
    max_tokens: int | None = None,
    tool_surface: str | None = None,
    pipeline: str | None = None,
    resume_pipeline_session: str | None = None,
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
    harness_root = _harness_root(musubi_dir)
    registry = _current_root_registry(harness_root)
    server_env = _server_env()
    server_env["MUSUBI_ROOT"] = str(harness_root)
    server_env[MANIFEST_ENV] = registry.to_json()
    context_compression_db_path = _server_db_path(musubi_dir, server_env)
    server_env["MUSUBI_STATE_DB"] = str(context_compression_db_path)
    audit_db_path = _server_audit_db_path(musubi_dir, server_env)
    server_env["MUSUBI_AUDIT_DB"] = str(audit_db_path)
    server_env["MUSUBI_DB"] = str(audit_db_path)
    turn_started_at = time.time()
    stats = AgentRunStats()
    budget = _build_token_budget(max_tokens, log)
    # One gate for the whole run, published before any tool can be dispatched.
    # Every worker reads this same object — root, nested, and the leaf coder
    # that carries no Orchestration at all.
    destructive_gate = DestructiveGate()
    _destructive_gate.set(destructive_gate)
    has_conversation = _chat_has_history(
        chat_id, db_path=context_compression_db_path, log=log,
    )
    # No pre-model routing survives. `classify_task` reads one question — does
    # this sentence read like a deletion — and answers with a warning; the
    # shape of the turn is the root's to declare (`agent/triage.py`) from facts
    # the substrate can check (`agent/evidence.py`).
    scope_hint = classify_task(task)
    effective_task = task
    goal_state = GoalState.create(
        intent=effective_task,
        scope=scope_hint.kind.value,
        route=scope_hint.route,
        assessment=scope_hint.assessment,
    )
    goal_state.plan_required = plan_first
    chat_usage = _chat_turn_usage(
        chat_id, db_path=context_compression_db_path, log=log,
    )
    goal_state.chat_turns = chat_usage["turns"]
    goal_state.chat_tokens = chat_usage["tokens"]
    goal_state.chat_barren_turns = chat_usage["barren_turns"]
    if chat_usage["turns"]:
        print(
            f"[agent] conversation usage: turns={chat_usage['turns']} "
            f"tokens={chat_usage['tokens']} "
            f"turns_without_a_file={chat_usage['barren_turns']}",
            file=log,
        )
    if chat_usage["barren_turns"] >= NO_PROGRESS_TURN_THRESHOLD:
        print(
            "[agent] conversation no-progress warning: "
            f"{chat_usage['barren_turns']} turns without a file",
            file=log,
        )
    # What the RECORD establishes, as distinct from what the sentence suggests.
    # Measured against EVERY root this request was granted, not just the
    # harness root. `registry` already holds the folders the operator attached
    # to this session; without it the vector reported each of them as
    # unreachable and `prompt_block` told the root agent to refuse the folder
    # the operator had just handed it, one block above the registry listing
    # that said the opposite.
    evidence = collect_evidence(
        effective_task,
        has_conversation=has_conversation,
        explorer_findings=_has_explorer_findings(goal_state),
        barren_turns=chat_usage["barren_turns"],
        roots=tuple((grant.alias, grant.path) for grant in registry.grants),
    )
    print(evidence.log_line(), file=log)
    # One fact crosses from observation into enforcement: did the request name
    # a path inside the workspace? It is static for the turn, so the goal state
    # carries it, and `GoalState.evidence_gap` combines it with the two facts
    # that are not — worker outcomes and an accepted manifest — to decide
    # whether a mutation worker may be summoned at all.
    goal_state.target_named = evidence.names_workspace_path
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
    orchestration: Orchestration | None = None

    # One AsyncExitStack owns Musubi's session AND every federated external
    # session, so they all open in order and tear down (LIFO) together. This
    # is equivalent to the old nested `async with` for Musubi alone.
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        gateway = McpGateway()
        mcp_tools = (await session.list_tools()).tools
        local_tools = [mcp_tool_to_schema(t) for t in mcp_tools]
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
        # The destructive gate measures the calls it can resolve, and it can
        # only resolve tools whose argument shape it knows. Saying so out loud
        # is the honest alternative to inferring "is this tool destructive?"
        # from a schema — an inference nothing could check, and the exact
        # species of guess this design removes.
        if external_tools:
            print(
                f"[agent] destructive gate covers musubi_write_file and "
                f"musubi_run_command; {len(external_tools)} external tool(s) "
                f"are outside it: "
                f"{', '.join(sorted(str(t.get('name', '?')) for t in external_tools)[:8])}",
                file=log,
            )

        # Open a parent session up front so the model's sub-agent spawns
        # have a valid parent. The "agent" identity short-circuits the
        # spawn firewall to MAIN_SUBAGENT_ALLOWLIST["agent"] regardless of
        # the session's pipeline tag (policy_engine `_effective_spawn_roles`).
        parent_session_id = await _open_parent_session(
            session, effective_task, log, chat_id,
        )
        planning_key = (
            chat_id
            or parent_session_id
            or request_id
            or f"run-{time.time_ns()}"
        )
        orchestration = Orchestration(
            parent_session_id=parent_session_id,
            goal_state=goal_state,
            planning_artifact_dir=(
                musubi_dir.parent
                / ".musubi"
                / "goals"
                / goal_artifact_key(chat_id, planning_key)
            ),
        )
        # Consent is matched against the RAW user message. A model cannot
        # author a user turn, so a token found here is proof a human typed it —
        # and the match is string equality against a value the harness itself
        # minted, not an interpretation of what the sentence means.
        approved = approved_keys_from(
            _pending_destructive(
                chat_id, db_path=context_compression_db_path, log=log,
            ),
            task,
        )
        if approved:
            destructive_gate.approved = approved
            print(
                f"[agent] destructive approval accepted for {len(approved)} "
                f"path(s)",
                file=log,
            )
        print(f"[agent] {scope_hint.log_line()}", file=log)
        # Hint, then evidence, then the ask. The hint is an opinion the root
        # may override, the evidence is the record it must not contradict, and
        # the triage line is where it says which of the two it acted on.
        system_prompt = build_system_prompt(
            scope_hint.prompt_block()
            + "\n\n"
            + evidence.prompt_block()
            + "\n\n"
            + registry.prompt_block()
        )
        if plan_first:
            system_prompt = f"{system_prompt}\n\n{_PLAN_FIRST_DIRECTIVE}"
            print("[agent] plan-first requested (--plan)", file=log)
        initial_messages: list[dict[str, Any]] | None = None
        if chat_id:
            # The RAW message, not `effective_task`: the pending request the
            # merge folded in is already on record as its own earlier row, and
            # replaying it twice would seed the model with a duplicate.
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
                    "parent_session_id": parent_session_id or planning_key,
                    "parent_agent_name": ROOT_ROLE,
                    "pipeline_name": pipeline,
                    "brief": effective_task,
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
                    resume_session_id=resume_pipeline_session,
                )
            else:
                final_answer, _ = await run_unit(
                    session, vendor, tools,
                    system_prompt=system_prompt,
                    user_message=effective_task,
                    max_cycles=max_cycles, log=log,
                    orchestration=orchestration, gateway=gateway,
                    spawn_catalog=worker_catalog,
                    salvage_on_exhaust=True,
                    compression_db_path=context_compression_db_path,
                    initial_messages=initial_messages,
                    role=ROOT_ROLE,
                    scope_hint=scope_hint,
                    stats=stats,
                    budget=budget,
                    audit_db_path=audit_db_path,
                    audit_session_id=parent_session_id,
                    audit_worker_id=ROOT_ROLE,
                    audit_stage=ROOT_ROLE,
                )
        except PolicyDeniedError as exc:
            final_answer = _policy_incomplete(exc)
        except Exception as exc:  # noqa: BLE001 — surfaced cleanly outside
            loop_error = exc

    # Raise OUTSIDE the MCP contexts (see above): a clean message that
    # `main()` prints as `agent-agent: …`, and that `except RuntimeError`
    # callers can catch. `_run_loop` signals cycle exhaustion by returning
    # None rather than raising, for the same reason.
    # A turn that died still happened, and it is the one most worth reading
    # afterwards. The success path below is the only place a row was ever
    # written, so a crashed turn left no `root_triage`, no cycle count, and
    # nothing for `chat_turn_usage` to see — a conversation could fail three
    # times in a row and the no-progress breaker would count zero turns.
    if chat_id and (loop_error is not None or final_answer is None):
        _record_agent_turn(
            chat_id=chat_id,
            request_id=request_id,
            parent_session_id=parent_session_id,
            started_at=turn_started_at,
            ended_at=time.time(),
            model_family=vendor.model,
            stats=stats,
            db_path=context_compression_db_path,
            log=log,
            delivered_artifact=(
                orchestration is not None and orchestration.delivered_artifact
            ),
            root_triage=None,
        )
    if loop_error is not None:
        raise RuntimeError(_clean_error(loop_error)) from None
    if final_answer is None:
        raise RuntimeError(
            f"agent exceeded {max_cycles} cycles without a final answer"
        )
    if destructive_gate.pending:
        final_answer = _ensure_grant_visible(
            final_answer, destructive_gate.pending
        )
    if chat_id:
        _append_chat_message(
            chat_id, "assistant", final_answer,
            db_path=context_compression_db_path, log=log,
        )
        _record_agent_turn(
            chat_id=chat_id,
            request_id=request_id,
            parent_session_id=parent_session_id,
            started_at=turn_started_at,
            ended_at=time.time(),
            model_family=vendor.model,
            stats=stats,
            db_path=context_compression_db_path,
            log=log,
            delivered_artifact=(
                orchestration is not None and orchestration.delivered_artifact
            ),
            pending_destructive=(
                encode_pending(destructive_gate.pending)
                if destructive_gate.pending
                else None
            ),
            root_triage=None,
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
    role: str = ROOT_ROLE,
    scope_hint: ScopeHint | None = None,
    stats: AgentRunStats | None = None,
    budget: TokenBudgetEnforcer | None = None,
    audit_db_path: Path | None = None,
    worker_max_output: int | None = None,
    model_output_override: int | None = None,
    audit_session_id: str | None = None,
    audit_worker_id: str = ROOT_ROLE,
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
            normalize_role(role) == ROOT_ROLE
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
            if normalize_role(role) == ROOT_ROLE
            and orchestration is not None
            and orchestration.depth == 0
            else None
        )
        recovery_outcome = (
            orchestration.latest_unrecovered_failure()
            if normalize_role(role) == ROOT_ROLE
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
                    "[agent] committed plan requires clarification; "
                    "no model call",
                    file=log,
                )
                final_answer = root_state.pending_clarification
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
            normalize_role(role) == ROOT_ROLE
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
        # An empty assistant turn is not a message. Several OpenAI-compatible
        # endpoints reject a content-less assistant entry on the NEXT request,
        # so a vendor that returns nothing would poison the whole conversation
        # rather than just wasting its own cycle.
        if resp.content:
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
        if text and _looks_like_vendor_tool_markup(text):
            print(
                f"[agent] {role}: vendor tool-call markup in the text channel; "
                "discarded (not an answer)",
                file=log,
            )
            text = ""
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

        # Truncation is checked BEFORE the "no tool calls → this is the final
        # answer" branch. A response cut off at the output cap is never final,
        # whether it was cut mid-tool-call or mid-sentence; ordering these the
        # other way meant a truncated TEXT answer was recorded as a clean
        # `final` cycle and handed downstream as a complete result.
        if resp.stop_reason == "max_tokens" and not tool_uses:
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
            print(
                "[agent] max_tokens truncated the text answer "
                f"({len(text)} chars kept, none dispatched). Answer shorter: "
                "lead with the required output and drop narrative.",
                file=log,
            )
            if cycle + 1 >= max_cycles:
                final_answer = _truncated_text_answer(text)
                break
            messages.append({
                "role": "user",
                "content": _TRUNCATED_TEXT_RETRY,
            })
            continue

        if not tool_uses and not text.strip():
            # No tool call, no words, and not a truncation — the vendor
            # returned an empty turn. Accepting it as the final answer is how
            # an empty string reaches a completion boundary and is recorded as
            # a success; fail it here where the cause is still visible.
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
                text_chars=0,
                cycle_status="empty",
                log=log,
            )
            print(
                f"[agent] {role}: empty model turn "
                f"(stop={resp.stop_reason}, out_tokens={usage.tokens_out}); "
                "not an answer",
                file=log,
            )
            if cycle + 1 >= max_cycles:
                final_answer = _empty_response_answer(resp.stop_reason)
                break
            messages.append({
                "role": "user",
                "content": _EMPTY_RESPONSE_RETRY,
            })
            continue

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
        try:
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
        except PolicyDeniedError as exc:
            is_root = (
                normalize_role(role) == ROOT_ROLE
                and (orchestration is None or orchestration.depth == 0)
            )
            if not is_root:
                raise
            _safe_record_agent_cycle(
                db_path=compression_db_path,
                session_id=audit_session_id,
                worker_id=audit_worker_id,
                stage=audit_stage,
                cycle_idx=cycle,
                started_at=cycle_started_at,
                ended_at=time.time(),
                lm_ms=lm_ms,
                usage=usage,
                tool_names=[str(tu.get("name", "")) for tu in tool_uses],
                text_chars=len(text),
                cycle_status="policy_halt",
                log=log,
            )
            final_answer = _policy_incomplete(exc)
            break
        recovery_halt: str | None = None
        if normalize_role(role) == ROOT_ROLE and orchestration is not None and orchestration.depth == 0:
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
                if final_candidate and _looks_like_vendor_tool_markup(
                    final_candidate
                ):
                    # The no-tools call did not produce prose either. Fail
                    # closed to the "[incomplete]" message below rather than
                    # handing markup on as this worker's plan.
                    print(
                        f"[agent] {role}: forced final answer was vendor "
                        "tool-call markup; rejected",
                        file=log,
                    )
                    final_candidate = None
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
    role: str = ROOT_ROLE,
    scope_hint: ScopeHint | None = None,
    stats: AgentRunStats | None = None,
    budget: TokenBudgetEnforcer | None = None,
    audit_db_path: Path | None = None,
    worker_max_output: int | None = None,
    model_output_override: int | None = None,
    audit_session_id: str | None = None,
    audit_worker_id: str = ROOT_ROLE,
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
            ".github/pipelines/<name>, e.g. feature-dev or code-review) with the "
            "task as its brief, instead of the model-routed single-agent loop. "
            "Pipelines needing per-file fan-out (e.g. code-review) are not "
            "supported by this deterministic runner."
        ),
    )
    ap.add_argument(
        "--resume-pipeline-session",
        default=None,
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--add-folder",
        action="append",
        default=[],
        metavar="[ALIAS=]PATH",
        help=(
            "Grant this session access to an additional folder. Repeat for "
            "multiple folders. Paths remain separate roots; --musubi always "
            "selects the fixed Musubi harness root."
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
    if args.resume_pipeline_session and not args.pipeline:
        print(
            "agent-agent: --resume-pipeline-session requires --pipeline",
            file=sys.stderr,
        )
        return 2
    request_id = os.environ.get("MUSUBI_REQUEST_ID", "").strip() or None
    runtime_log: Any = sys.stderr
    if (
        request_id is not None
        and os.environ.get("MUSUBI_RUNTIME_LOG_PROTOCOL", "").strip() == "1"
    ):
        runtime_log = RuntimeLogWriter(sys.stderr, request_id)

    musubi_dir = (args.musubi or _default_musubi_dir()).resolve()
    if not (musubi_dir / "server.py").is_file():
        print(
            f"agent-agent: server.py not found under {musubi_dir} "
            f"(set --musubi or MUSUBI_ROOT)",
            file=sys.stderr,
        )
        return 2
    if args.resume_pipeline_session:
        try:
            checkpoint = _load_pipeline_resume_checkpoint(
                args.resume_pipeline_session,
                _server_db_path(musubi_dir, _server_env()),
            )
            for label, supplied, stored in [
                ("task", args.task, checkpoint["task"]),
                ("pipeline", args.pipeline, checkpoint["pipeline_name"]),
                ("profile", args.profile, checkpoint["profile"]),
                ("chat", args.chat_id, checkpoint["chat_id"]),
            ]:
                if supplied not in {None, "", stored}:
                    raise RuntimeError(
                        f"resume {label} does not match the original checkpoint"
                    )
            args.task = checkpoint["task"]
            args.pipeline = checkpoint["pipeline_name"]
            args.profile = checkpoint["profile"]
            args.chat_id = checkpoint["chat_id"]
            _validate_resume_folder_manifest(
                checkpoint["request_id"],
                _server_audit_db_path(musubi_dir, _server_env()),
            )
        except RuntimeError as exc:
            print(f"agent-agent: {exc}", file=sys.stderr)
            return 2
    try:
        vendor, vendor_source = _resolve_vendor(args.profile)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"agent-agent: {exc}", file=sys.stderr)
        return 2
    previous_root = os.environ.get("MUSUBI_ROOT")
    previous_manifest = os.environ.get(MANIFEST_ENV)
    harness_root = _harness_root(musubi_dir)
    os.environ["MUSUBI_ROOT"] = str(harness_root)
    try:
        try:
            if args.add_folder:
                _build_folder_registry(harness_root, args.add_folder)
            else:
                _current_root_registry(harness_root)
        except ValueError as exc:
            print(f"agent-agent: invalid folder grant: {exc}", file=sys.stderr)
            return 2

        try:
            answer = asyncio.run(
                run_agent(
                    args.task, vendor, musubi_dir,
                    max_cycles=args.max_cycles, mcp_config=args.mcp_config,
                    log=runtime_log,
                    vendor_source=vendor_source,
                    chat_id=args.chat_id,
                    request_id=request_id,
                    max_tokens=args.max_tokens,
                    tool_surface=args.tool_surface,
                    pipeline=args.pipeline,
                    resume_pipeline_session=args.resume_pipeline_session,
                    plan_first=args.plan,
                )
            )
        except KeyboardInterrupt:
            print("\n[agent] cancelled.", file=sys.stderr)
            return 130
        except RuntimeError as exc:
            print(f"agent-agent: {exc}", file=sys.stderr)
            return 1
    finally:
        _restore_environment_value("MUSUBI_ROOT", previous_root)
        _restore_environment_value(MANIFEST_ENV, previous_manifest)

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
        resolve_model_context_window,
        resolve_model_output_override,
    )

    def from_profile(prof: dict[str, Any]) -> LMRouter:
        resolved = build_from_profile(prof)
        resolved.max_output_tokens = resolve_model_output_override(prof)
        resolved.context_window_tokens = resolve_model_context_window(prof)
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
    configured = server_env.get("MUSUBI_STATE_DB")
    if configured:
        return Path(configured)
    root = server_env.get("MUSUBI_ROOT")
    if root:
        return Path(root) / "data" / "musubi.db"
    return musubi_dir / "storage" / "musubi.db"


def _server_audit_db_path(musubi_dir: Path, server_env: dict[str, str]) -> Path:
    """Return the append-only audit DB path used by the spawned server."""
    configured = server_env.get("MUSUBI_DB")
    if configured:
        return Path(configured)
    root = server_env.get("MUSUBI_ROOT")
    if root:
        return Path(root) / "data" / "audit.db"
    return musubi_dir / "storage" / "audit.db"


def _load_pipeline_resume_checkpoint(
    session_id: str,
    db_path: Path,
) -> dict[str, str]:
    from session import state
    from storage import db

    db.init_db(db_path)
    run = db.get_pipeline_run(session_id, db_path)
    session = state.get_session(session_id, db_path)
    if run is None or session is None:
        raise RuntimeError(f"pipeline resume checkpoint {session_id!r} was not found")
    if run.get("ended_at") is not None:
        raise RuntimeError(f"pipeline resume checkpoint {session_id!r} is already final")
    if not session.get("pending_action"):
        raise RuntimeError(f"pipeline resume checkpoint {session_id!r} has no pending action")
    checkpoint = {
        "session_id": session_id,
        "pipeline_name": str(run.get("pipeline_name") or "").strip(),
        "chat_id": str(run.get("chat_id") or "").strip(),
        "request_id": str(run.get("request_id") or "").strip(),
        "profile": str(run.get("profile") or "").strip(),
        "task": str(run.get("task") or "").strip(),
    }
    missing = [name for name, value in checkpoint.items() if not value]
    if missing:
        raise RuntimeError(
            "pipeline resume checkpoint is incomplete: " + ", ".join(missing)
        )
    return checkpoint


def _validate_resume_folder_manifest(request_id: str, audit_db_path: Path) -> None:
    import sqlite3

    raw = os.environ.get(MANIFEST_ENV, "").strip()
    if not raw:
        raise RuntimeError("pipeline resume is missing its original folder manifest")
    try:
        current = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pipeline resume folder manifest is invalid: {exc}") from exc
    try:
        with sqlite3.connect(audit_db_path) as conn:
            rows = conn.execute(
                "SELECT grant_id,alias,canonical_path "
                "FROM request_folder_grants "
                "WHERE request_id=? "
                "ORDER BY ordinal,grant_id",
                (request_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"pipeline resume cannot read the original folder snapshot: {exc}"
        ) from exc
    if not rows:
        raise RuntimeError("pipeline resume has no immutable folder snapshot")

    def key(path: object) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))

    expected = [(str(gid), str(alias), key(path)) for gid, alias, path in rows]
    try:
        actual = [
            (
                str(item["grantId"]),
                str(item["alias"]),
                key(item["canonicalPath"]),
            )
            for item in current
        ]
    except (TypeError, KeyError) as exc:
        raise RuntimeError("pipeline resume folder manifest has an invalid shape") from exc
    if actual != expected:
        raise RuntimeError(
            "pipeline resume folder manifest differs from the original request snapshot"
        )


def _current_root_registry(musubi_dir: Path) -> RootRegistry:
    raw = os.environ.get(MANIFEST_ENV, "").strip()
    return (
        RootRegistry.from_json(raw, musubi_dir)
        if raw
        else RootRegistry.build(musubi_dir)
    )


def _build_folder_registry(
    musubi_dir: Path,
    raw_folders: list[str],
) -> RootRegistry:
    used: set[str] = set()
    grants: list[FolderGrant] = []
    for index, raw in enumerate(raw_folders):
        value = raw.strip()
        if not value:
            raise ValueError("folder argument must be non-empty")
        if "=" in value:
            alias, path_text = value.split("=", 1)
            alias = alias.strip().lower()
        else:
            path_text = value
            alias = derive_alias(Path(path_text), used)
        path = Path(path_text.strip()).expanduser()
        if not path.is_dir():
            raise ValueError(f"folder grant is not an existing directory: {path}")
        used.add(alias)
        grants.append(FolderGrant(f"cli-{index + 1}", alias, path))
    registry = RootRegistry.build(musubi_dir, grants)
    os.environ[MANIFEST_ENV] = registry.to_json()
    return registry


def _restore_environment_value(name: str, previous: str | None) -> None:
    """Restore an entrypoint-scoped environment value for embedded callers."""
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _default_audit_db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "storage" / "audit.db"


def _harness_root(musubi_dir: Path) -> Path:
    """Return the fixed checkout/install root, not its Python package."""
    configured = os.environ.get("MUSUBI_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    package = musubi_dir.resolve()
    parent = package.parent
    if package.name.lower() == "musubi" and (
        (parent / "CLAUDE.md").is_file() or (parent / ".github").is_dir()
    ):
        return parent
    return package


def _default_musubi_dir() -> Path:
    """Resolve the Musubi server dir.

    Preference order:
      1. $MUSUBI_ROOT (matches the extension's convention).
      2. The directory containing this very module — works for the
         installed-wheel case (server.py ships alongside agent/).
    """
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        root = Path(env).expanduser()
        if (root / "server.py").is_file():
            return root
        package = root / "musubi"
        if (package / "server.py").is_file():
            return package
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


#: Roles whose outcome establishes a fact about the workspace rather than
#: changing it. A coder's report is not evidence that the target was found —
#: it is evidence that something was written, which is a different claim.
_READ_ONLY_EVIDENCE_ROLES = frozenset({"explorer", "investigator", "finder"})


def _has_explorer_findings(goal_state: GoalState) -> bool:
    """True once a read-only worker has reported into this turn.

    Empty at turn start, by construction: a fresh process holds a fresh
    `GoalState`. That is correct rather than a limitation — evidence gathered
    in a PREVIOUS turn is only usable if it was written down, and what was
    written down is the conversation, which `has_conversation` already covers.
    """
    return any(
        outcome.role in _READ_ONLY_EVIDENCE_ROLES
        and outcome.status not in {"failed", "error"}
        for outcome in goal_state.outcomes
    )


def _chat_has_history(
    chat_id: str | None, *, db_path: Path, log: Any,
) -> bool:
    """True when this chat already has prior turns on record.

    Probed BEFORE `classify_task` so a bare follow-up ("Okta") is read as
    conversation rather than as a standalone work order. Fails to False, which
    is the pre-existing behaviour — a missing DB must never block a turn.
    """
    if not chat_id:
        return False
    try:
        _ensure_core_import_path()
        from session import conversations
        from storage import db

        db.init_db(db_path)
        return conversations.has_history(chat_id, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - probe must not break the turn
        print(
            f"[agent] chat history probe failed: "
            f"{type(exc).__name__}: {exc}",
            file=log,
        )
        return False


#: Cap on the pending request carried across turns. A merged brief still has to
#: fit the per-call sizing rule, and the full original text remains in
#: `conversation_messages` for replay either way.
def _pending_destructive(
    chat_id: str | None, *, db_path: Path, log: Any,
) -> str | None:
    """Approval tokens this chat is waiting on, or None on any failure.

    None is the safe direction: an unreadable grant leaves the gate shut.
    """
    if not chat_id:
        return None
    try:
        _ensure_core_import_path()
        from storage import db

        db.init_db(db_path)
        return db.pending_destructive(chat_id, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - probe must not break the turn
        print(
            f"[agent] pending destructive read failed: "
            f"{type(exc).__name__}: {exc}",
            file=log,
        )
        return None


def _chat_turn_usage(
    chat_id: str | None, *, db_path: Path, log: Any,
) -> dict[str, int]:
    """Conversation-scoped cost so far, or zeros when unavailable."""
    empty = {"turns": 0, "tokens": 0, "barren_turns": 0}
    if not chat_id:
        return empty
    try:
        _ensure_core_import_path()
        from storage import db

        db.init_db(db_path)
        return db.chat_turn_usage(chat_id, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the turn
        print(
            f"[agent] conversation usage read failed: "
            f"{type(exc).__name__}: {exc}",
            file=log,
        )
        return empty


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
    return bounded(content, REPLAY_TOOL_ROW_MAX_CHARS, collapse=False)


def _record_agent_turn(
    *,
    chat_id: str,
    request_id: str | None,
    parent_session_id: str | None,
    started_at: float,
    ended_at: float,
    model_family: str,
    stats: AgentRunStats,
    db_path: Path,
    log: Any,
    delivered_artifact: bool = False,
    pending_destructive: str | None = None,
    root_triage: str | None = None,
) -> None:
    try:
        _ensure_core_import_path()
        from storage import db

        db.init_db(db_path)
        db.insert_agent_turn(
            chat_id=chat_id,
            request_id=request_id,
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
            delivered_artifact=delivered_artifact,
            pending_destructive=pending_destructive,
            root_triage=root_triage,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry is non-fatal
        print(
            f"[agent] agent_turn write failed: {type(exc).__name__}: {exc}",
            file=log,
        )


def _extract_text(content_blocks: list[dict[str, Any]]) -> str:
    parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "".join(parts).strip()


#: Vendor-native tool-call syntax that leaked into the TEXT channel. When the
#: loop exhausts its cycles it makes one final call with NO tools offered, on
#: the assumption that a model with nothing to call will answer in words. Not
#: every vendor honours that: DeepSeek emitted `<｜｜DSML｜｜tool_calls>…` as
#: prose (note the FULL-WIDTH bars, U+FF5C, not ASCII pipes). Such text is not
#: an answer — accepting it puts machine markup in front of the user, into the
#: audit DB, and into `parse_change_manifest`, where a routing decision would
#: then be made from garbage.
_VENDOR_TOOL_MARKUP_RE = re.compile(
    r"(?i)(\bDSML\b|<[|｜]+\s*tool[_▁]?calls?|<tool_call\b|"
    r"</?function_calls?\b|<invoke\s+name\s*=|\bantml:invoke\b|"
    r"<[|｜]python_tag[|｜]>)"
)


def _looks_like_vendor_tool_markup(text: str) -> bool:
    """True when `text` is a vendor's tool-call syntax rather than prose.

    Fail-closed by design: the caller discards the text and reports that the
    worker did not answer, rather than trying to salvage a plan out of markup.
    """
    return _VENDOR_TOOL_MARKUP_RE.search(text or "") is not None


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
    state = orchestration.goal_state
    if state is not None and state.planning_contract_failures >= 3:
        return (
            "[incomplete] run stopped: three consecutive planning-contract "
            "failures occurred before any worker was spawned. Correct the "
            "closed plan declaration and retry the request."
        )
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


#: Grant that lets a run past the destructive gate. The OPERATOR sets it — a
#: worker cannot set its own env, so this is a human's decision by
#: construction, not something the model can talk its way into.
DESTRUCTIVE_GRANT_ENV = "MUSUBI_ALLOW_DESTRUCTIVE"


def _destructive_grant() -> bool:
    return os.environ.get(DESTRUCTIVE_GRANT_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _preflight_destructive_batch(
    tool_uses: list[dict[str, Any]],
    *,
    log: Any,
) -> dict[str, str]:
    """Measure each call's blast radius; return refusals keyed by tool_use id.

    This is the hard stop the old lexical guard could never be. It reads the
    CALL, not the user's sentence, so it sees `rm -rf build` — which the old
    guard let through to a coder holding `musubi_run_command` — and it counts
    the files rather than asserting a mood.

    Measurement failures do not silently allow: `measure` never raises, and a
    delete whose targets cannot be attributed comes back `unanalyzable`, which
    is over threshold.

    State comes from the run-scoped `_destructive_gate`, never from the caller's
    `Orchestration`. A leaf worker has no Orchestration, and reading one meant
    its refusals were recorded nowhere — the user could echo the token and the
    same deletion would be refused again, indefinitely.
    """
    gate = _destructive_gate.get() or DestructiveGate()
    totals = gate.totals
    granted = _destructive_grant()
    refusals: dict[str, str] = {}
    for tu in tool_uses:
        name = str(tu.get("name", ""))
        raw_args = tu.get("input") or {}
        args = raw_args if isinstance(raw_args, dict) else {}
        radius = measure(name, args)
        if radius.is_empty:
            continue
        if exceeds_threshold(radius, totals) and not granted:
            if covered_by(radius, gate.approved):
                print(
                    f"[agent] destructive gate: {name} allowed — user approved "
                    f"these exact paths",
                    file=log,
                )
            else:
                token = grant_token(radius.keys)
                reason = (
                    f"{describe(radius, totals)}\n\n"
                    f"To approve exactly this and nothing else, reply with: "
                    f"{token}"
                )
                refusals[str(tu.get("id") or "")] = reason
                gate.pending.append((token, radius.keys))
                print(
                    f"[agent] destructive gate: {name} refused ({token}) — "
                    f"deletes={radius.delete_count} "
                    f"overwrites={radius.overwrite_count} "
                    f"unanalyzable={radius.unanalyzable}",
                    file=log,
                )
                continue
        if granted and exceeds_threshold(radius, totals):
            print(
                f"[agent] destructive gate: {name} allowed by "
                f"{DESTRUCTIVE_GRANT_ENV}",
                file=log,
            )
        totals.add(radius)
    return refusals


def _ensure_grant_visible(
    answer: str,
    pending: list[tuple[str, tuple[str, ...]]],
) -> str:
    """Guarantee every un-echoed approval token reaches the user.

    The gate's refusal is a TOOL RESULT: the model reads it and then writes the
    user's answer in its own words. A model that paraphrases the refusal — or
    judges it not worth mentioning — leaves the user holding no token, and so
    no way to approve, from either surface. Consent must not depend on the
    model's diligence, so the harness appends whatever the model dropped.

    `dict.fromkeys` deduplicates while keeping the order the refusals happened
    in, so two calls hitting the same radius print one line, not two.
    """
    missing = [token for token, _ in pending if token not in answer]
    if not missing:
        return answer
    lines = "\n".join(
        f"To approve exactly this and nothing else, reply with: {token}"
        for token in dict.fromkeys(missing)
    )
    return f"{answer.rstrip()}\n\n{lines}"


def _destructive_refusal_answer(reason: str) -> str:
    payload = {
        "status": "blocked",
        "reason": "destructive_change_needs_user_confirmation",
        "retry_same_strategy": False,
        "message": reason,
    }
    return "[blocked] " + json.dumps(payload, separators=(",", ":"))


def _preflight_policy_batch(
    tool_uses: list[dict[str, Any]],
    *,
    role: str,
    orchestration: Orchestration | None,
    audit_db_path: Path | None,
    log: Any,
) -> dict[str, str]:
    """Refuse the batch before any sibling launches — but only where warranted.

    Returns `{tool_use_id: reason}` for calls whose ARGUMENTS were refused;
    those flow into the same per-call refusal channel the spawn caps use, so
    the model reads the reason as a tool result and can correct it on the next
    cycle. An authorization denial still raises `PolicyDeniedError` and ends
    the turn, because no retry can make the caller a different role.

    Both are recorded to `policy_audit` as denials. The split is about what the
    caller can do next, never about what the ledger says happened.
    """
    # Canonicalised before it is written anywhere. `policy_audit.role` folds
    # through `evaluate_tool_call` and `tool_audit.agent` does not, so an
    # un-normalised value here made the two ledgers disagree about the same
    # call — one saying `root`, the other `agent`.
    call_role = normalize_role(
        orchestration.parent_agent_name
        if orchestration is not None and orchestration.parent_agent_name
        else role
    )
    session_id = orchestration.parent_session_id if orchestration else None
    audit_path = audit_db_path or _default_audit_db_path()
    refused: dict[str, str] = {}
    for tu in tool_uses:
        name = str(tu.get("name", ""))
        if not is_musubi_tool(name):
            continue
        raw_args = tu.get("input") or {}
        args = raw_args if isinstance(raw_args, dict) else {}
        decision = evaluate_tool_call(call_role, name)
        if decision.allowed:
            argument_decision = evaluate_argument_policy(
                call_role,
                name,
                args,
                pipeline_name=(
                    orchestration.pipeline_name if orchestration else None
                ),
            )
            if argument_decision is not None:
                decision = argument_decision
        if decision.allowed:
            continue
        reason = decision.reason
        denied = (
            f"[policy denied] {reason}"
            f"{denied_tool_guidance(call_role, name)}"
        )
        emit_runtime_log(
            log,
            f"[agent]   policy denied {name}: {reason}",
            category="policy",
        )
        _safe_record_policy(decision, db_path=audit_path, log=log)
        if decision.recoverable:
            # Refuse THIS call, not the batch: a sibling spawn with sound
            # arguments has done nothing wrong, and the model needs the
            # reason back as a tool result to correct the one that has.
            # `_dispatch_one` writes the tool_audit row for the refusal.
            print(
                f"[agent]   ⨯ refused worker: {reason}",
                file=log,
            )
            refused[str(tu.get("id", ""))] = reason
            continue
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
        raise PolicyDeniedError(
            role=call_role,
            tool=name,
            reason=reason,
        )
    return refused


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
    role: str = ROOT_ROLE,
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
    tool_uses = _normalize_root_spawn_tool_uses(
        tool_uses,
        role=role,
        orchestration=orchestration,
        log=log,
    )
    argument_refusals = _preflight_policy_batch(
        tool_uses,
        role=role,
        orchestration=orchestration,
        audit_db_path=audit_db_path,
        log=log,
    )
    destructive = _preflight_destructive_batch(tool_uses, log=log)
    refused = _spawn_overflow_reasons(
        tool_uses,
        log,
        role=role,
        scope_hint=scope_hint,
        cycle_index=cycle_index,
        orchestration=orchestration,
    )
    # An argument refusal outranks a width or role-order one: it is the reason
    # THIS call cannot run as written, and the model needs it back verbatim.
    refused.update(argument_refusals)
    if _has_order_sensitive_file_tool(tool_uses):
        settled = []
        for tu in tool_uses:
            try:
                settled.append(await _dispatch_one(
                    tu, session, log,
                    vendor=vendor, tools=tools,
                    orchestration=orchestration, gateway=gateway,
                    refused_reason=refused.get(tu.get("id", "")),
                    destructive_reason=destructive.get(tu.get("id", "")),
                    compression_db_path=compression_db_path,
                    role=role,
                    budget=budget,
                    stats=stats,
                    audit_db_path=audit_db_path,
                ))
            except PolicyDeniedError:
                raise
            except Exception as exc:  # noqa: BLE001 - match gather semantics
                settled.append(exc)
    else:
        coros = [
            _dispatch_one(
                tu, session, log,
                vendor=vendor, tools=tools,
                orchestration=orchestration, gateway=gateway,
                refused_reason=refused.get(tu.get("id", "")),
                destructive_reason=destructive.get(tu.get("id", "")),
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
        if isinstance(outcome, PolicyDeniedError):
            raise outcome
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


def _normalize_root_spawn_tool_uses(
    tool_uses: list[dict[str, Any]],
    *,
    role: str,
    orchestration: Orchestration | None,
    log: Any,
) -> list[dict[str, Any]]:
    """Remove model-owned tool narrowing from depth-zero Root spawns.

    Worker capabilities are owned by ``SUBAGENT_POLICIES[role]``. The Root
    model chooses a worker role and skill, but it does not own that role's tool
    surface. In particular, MCP tool names such as ``musubi_write_file`` are
    not the symbolic capability names (``Write``) accepted by the substrate;
    forwarding a model-authored ``allowed_tools`` list can therefore intersect
    to an empty set and starve an otherwise valid Coder.

    Nested workers and direct substrate callers retain explicit narrowing.
    """
    if (
        normalize_role(role) != ROOT_ROLE
        or orchestration is None
        or orchestration.depth != 0
    ):
        return tool_uses

    normalized: list[dict[str, Any]] = []
    for tool_use in tool_uses:
        raw_input = tool_use.get("input")
        if (
            tool_use.get("name") != "musubi_spawn_subagent"
            or not isinstance(raw_input, dict)
            or "allowed_tools" not in raw_input
        ):
            normalized.append(tool_use)
            continue
        clean_input = dict(raw_input)
        clean_input.pop("allowed_tools", None)
        normalized.append({**tool_use, "input": clean_input})
        emit_runtime_log(
            log,
            "[agent] ignored model allowed_tools on root spawn; "
            "the worker role policy owns its tool surface",
            category="policy",
        )
    return normalized


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
            RouteKind.ASK_SCOPE,
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


#: Fed back to a model whose TEXT answer was cut at the output cap. Short on
#: purpose: it is prepended to a conversation that is already at the cap.
_TRUNCATED_TEXT_RETRY = (
    "[harness] Your previous answer hit the output-token cap before it "
    "finished, so none of it was kept. Answer again and fit the cap: lead "
    "with the required output fields, keep reasoning out of the response, "
    "and cut supporting prose rather than required sections."
)

#: Fed back to a model that returned no content at all.
_EMPTY_RESPONSE_RETRY = (
    "[harness] Your previous turn contained no tool call and no text. "
    "Respond with either one tool call or your final answer."
)


def _truncated_text_answer(partial: str) -> str:
    """Typed marker for a text answer cut off at the output cap.

    Shares the `[blocked]` prefix with `_truncated_tool_call_answer` so every
    caller that already treats that prefix as non-final (`agent/subagent.py`'s
    failure typing, the pipeline runner's incomplete check) keeps working
    without knowing which channel was truncated.
    """
    payload = {
        "status": "blocked",
        "reason": "output_too_large_for_single_response",
        "retry_same_strategy": False,
        "recommended_strategies": [
            "compact_answer",
            "drop_supporting_prose",
            RouteKind.ASK_SCOPE,
        ],
        "partial_chars": len(partial),
        "message": (
            "The model hit max_tokens while writing its answer, so the "
            "partial text was not accepted as a result. A reasoning model "
            "spends this same cap on its thinking channel, so raise the "
            "role's maxOutputTokens or reduce what the answer must contain."
        ),
    }
    return "[blocked] " + json.dumps(payload, separators=(",", ":"))


def _empty_response_answer(stop_reason: str) -> str:
    """Typed marker for a vendor turn that carried neither text nor a call."""
    payload = {
        "status": "blocked",
        "reason": "empty_model_response",
        "retry_same_strategy": False,
        "stop_reason": str(stop_reason or "unknown"),
        "message": (
            "The vendor returned no content blocks. Nothing was recorded as "
            "an answer."
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
        # Deterministic role order (root only). On a planner-led goal the coder
        # gate opens only after the planner's manifest reclassifies the change;
        # on a large one the manifest also queues designer → coder → reviewer,
        # and each step is gated the same way. Only these four roles are
        # ordered — an explorer or investigator the root summons for evidence
        # is never blocked. `next_role` comes from the assessment cascade, not
        # the retired keyword guess, and the refusal names the legal role so
        # the model can comply.
        if (
            normalize_role(role) == ROOT_ROLE
            and orchestration is not None
            and orchestration.depth == 0
            and orchestration.goal_state is not None
            and spawn_role in ORDERED_ROLES
            and orchestration.goal_state.next_role is not None
            and spawn_role != orchestration.goal_state.next_role
        ):
            legal = orchestration.goal_state.next_role
            queued = orchestration.goal_state.role_chain
            tail = f" then {' → '.join(queued)}" if queued else ""
            reason = (
                f"role order: {legal!r} is the legal next role on this route"
                f"{tail}; spawn {legal!r} first"
            )
            overflow[tu.get("id", "")] = reason
            print(
                f"[agent]   ⨯ refused worker(role={spawn_role!r}): {reason}",
                file=log,
            )
            continue
        # Evidence sufficiency (root only, mutation roles only). The role-order
        # gate above asks "is this the right role NEXT"; this one asks "does
        # anyone know what this turn is about yet". They are different
        # questions: a simple route sets no `next_role` at all, so a coder on
        # "make it faster" passed the order gate and wrote files at a guess.
        # Read fresh — an explorer summoned earlier THIS turn clears it.
        if (
            normalize_role(role) == ROOT_ROLE
            and orchestration is not None
            and orchestration.depth == 0
            and orchestration.goal_state is not None
            and spawn_role in MUTATION_ROLES
        ):
            # Two questions, asked in order of how much they already know.
            # `overrun_stop` reads an ACCEPTED declaration against files
            # actually touched, so when it fires it is the more specific
            # refusal and the more useful one to report.
            for check in (
                orchestration.goal_state.overrun_stop,
                orchestration.goal_state.evidence_gap,
            ):
                reason = check()
                if reason is not None:
                    overflow[tu.get("id", "")] = reason
                    print(
                        f"[agent]   ⨯ refused worker(role={spawn_role!r}): "
                        f"{reason}",
                        file=log,
                    )
                    break
            if tu.get("id", "") in overflow:
                continue
        if (
            normalize_role(role) == ROOT_ROLE
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
        if orchestration is not None and normalize_role(role) == ROOT_ROLE and orchestration.depth == 0:
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
    destructive_reason: str | None = None,
    compression_db_path: Path | None = None,
    role: str = ROOT_ROLE,
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
    # Canonicalised before it is written anywhere. `policy_audit.role` folds
    # through `evaluate_tool_call` and `tool_audit.agent` does not, so an
    # un-normalised value here made the two ledgers disagree about the same
    # call — one saying `root`, the other `agent`.
    call_role = normalize_role(
        orchestration.parent_agent_name
        if orchestration is not None and orchestration.parent_agent_name
        else role
    )
    if destructive_reason:
        # The hard stop, ahead of the tool and ahead of the spawn branch: a
        # measured, irreversible change does not run until a human says so.
        blocked = _destructive_refusal_answer(destructive_reason)
        emit_runtime_log(
            log, f"[agent]   destructive gate blocked {name}", category="tools",
        )
        _safe_record_tool_audit(
            session_id=session_id, role=call_role, tool=str(name),
            args=json_args(args), status="refused", db_path=audit_path,
            result_text=blocked, log=log,
        )
        return blocked
    if refused_reason:
        # A refusal is a decision not to make this call, so it lands ahead of
        # the tool and ahead of every branch below.
        #
        # It used to live INSIDE the spawn-with-orchestration branch, which
        # held only because the sole source of refusals — the per-role width
        # and role-order caps — cannot fire with orchestration off. Argument
        # refusals can fire in any configuration, and on that path a refused
        # spawn fell straight through to the MCP server: the call the harness
        # had just declined to make was made anyway.
        result = json.dumps({"status": "refused", "reason": refused_reason})
        _safe_record_tool_audit(
            session_id=session_id, role=call_role, tool=str(name),
            args=json_args(args), status="refused", db_path=audit_path,
            result_text=result, log=log,
        )
        return result
    should_audit = is_musubi_tool(name)
    if should_audit:
        decision = evaluate_tool_call(call_role, name)
        _safe_record_policy(decision, db_path=audit_path, log=log)
        if decision.allowed:
            # Allows were recorded to `policy_audit` but never emitted, so the
            # only policy line that ever reached the runtime ledger was a
            # denial. A console filtered to Policy therefore read empty on
            # every clean run — the gate proving itself exactly when it has
            # nothing to refuse is what the operator needs to see, and
            # `policy_audit` cannot supply it per-turn (it carries no session
            # or request column, and the console reads only its last 50 rows).
            #
            # Suppressed for an already-refused call: role/tool authorization
            # did pass, but the preflight has just logged why the ARGUMENTS
            # did not, and an "allow" line under that denial reads as a
            # contradiction rather than as the two checks it actually is.
            emit_runtime_log(
                log,
                f"[agent]   policy allow {name} (role={call_role})",
                category="policy",
            )
        if not decision.allowed:
            denied = (
                f"[policy denied] {decision.reason}"
                f"{denied_tool_guidance(call_role, name)}"
            )
            emit_runtime_log(
                log,
                f"[agent]   policy denied {name}: {decision.reason}",
                category="policy",
            )
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
            raise PolicyDeniedError(
                role=call_role,
                tool=name,
                reason=decision.reason,
            )

    arg_error = _file_tool_argument_error(name, args)
    if arg_error is not None:
        result = f"[tool error] invalid arguments for {name}: {arg_error}"
        emit_runtime_log(
            log,
            f"[agent]   invalid args for {name}: {arg_error}",
            category="tools",
        )
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
        name in {
            "musubi_begin_direct",
            "musubi_begin_plan",
            "musubi_commit_plan",
        }
        and orchestration is not None
        and orchestration.depth == 0
        and orchestration.goal_state is not None
    ):
        result = _handle_root_control_tool(name, args, orchestration)
        # Root control calls are request-scoped (no worker runtime scope is
        # active here). Keep the Console ledger useful without copying the
        # model's raw plan or schema-shaped correction payload into it.
        emit_runtime_log(
            log,
            sanitize_control_result(result, name),
            category="tools",
        )
        _safe_record_tool_audit(
            session_id=session_id,
            role=call_role,
            tool=name,
            args=json_args(args),
            status=_tool_result_audit_status(result),
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
        emit_runtime_log(
            log,
            f"[agent]   → summon pipeline({args.get('pipeline_name')!r})",
            category="tools",
        )
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
        except PolicyDeniedError:
            raise
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
        # `refused_reason` is handled above, before any branch — a refused call
        # must not reach the server on any path, orchestrated or not.
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
        emit_runtime_log(
            log,
            f"[agent]   → spawn worker(role={args.get('role')!r})",
            category="tools",
        )
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
        except PolicyDeniedError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface to the model
            result = f"[subagent error] {type(exc).__name__}: {exc}"
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(args), status="error", db_path=audit_path,
                result_text=result, log=log,
            )
            return result

    emit_runtime_log(
        log,
        f"[agent]   → {name}({_truncate(json.dumps(args), 60)})",
        category="tools",
    )
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
            emit_runtime_log(
                log,
                f"[agent]   skill used={skill_id} agent={agent_name}",
                category="skills",
            )
        if should_audit:
            _safe_record_tool_audit(
                session_id=session_id, role=call_role, tool=name,
                args=json_args(_tool_audit_args(args, text)),
                status=_tool_result_audit_status(text),
                db_path=audit_path,
                result_text=text, log=log,
            )
        _record_touched_file(name, args, text)
        _record_skill_mismatch(name, args, text, log)
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


class _PlanningContractError(ValueError):
    """A model-correctable Root plan declaration error."""

    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind


def _root_control_error(
    error_kind: str,
    message: str,
    state: GoalState,
) -> str:
    """Return one closed correction envelope for a bad plan declaration."""
    failures = state.record_planning_contract_failure(error_kind)
    terminal = failures >= 3
    if terminal:
        state.pending_clarification = (
            "[incomplete] run stopped: three consecutive planning-contract "
            "failures occurred before any worker was spawned. Correct the "
            "closed plan declaration and retry the request."
        )
        state.next_role = None
        state.role_chain = ()
    return json.dumps({
        "status": "incomplete" if terminal else "error",
        "error_kind": error_kind,
        "message": message,
        "expected_schema": manifest_schema(),
        "allowed_roles": list(ROOT_PLAN_WORKER_ROLES),
        "consecutive_failures": failures,
    })


def sanitize_control_result(result: str, tool_name: str) -> str:
    """Project a Root control outcome into a safe, bounded runtime event.

    The full tool response is retained in the tool audit for debugging. The
    Request Log must not repeat raw ``plan_markdown``, manifest fields, or the
    correction schema, which can be much larger and may contain user context.
    """
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    status = str(payload.get("status") or "error").strip().lower()
    if status not in {"ok", "error", "incomplete"}:
        status = "error"
    parts = [f"[agent] control {tool_name} status={status}"]

    error_kind = payload.get("error_kind")
    if isinstance(error_kind, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error_kind):
        parts.append(f"error_kind={error_kind}")
    # Correction responses use `message`; deliberately do not fall back to a
    # generic `error` field, which may contain provider or filesystem detail.
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(f"reason={bounded(message, 240)}")
    failures = payload.get("consecutive_failures")
    if type(failures) is int and failures >= 0:
        parts.append(f"consecutive_failures={failures}")
    return " ".join(parts)


def _handle_root_control_tool(
    name: str,
    args: dict[str, Any],
    orchestration: Orchestration,
) -> str:
    """Apply model-owned mode/plan declarations to driver-owned goal state."""
    state = orchestration.goal_state
    assert state is not None
    try:
        if name == "musubi_begin_plan":
            deliverable = str(args.get("deliverable") or "").strip()
            if not deliverable:
                raise ValueError("deliverable must be a non-empty string")
            state.begin_plan()
            return json.dumps({
                "status": "ok",
                "mode": state.mode,
                "deliverable": deliverable,
            })

        if name == "musubi_begin_direct":
            target_intent = str(args.get("target_intent") or "").strip()
            target_path = str(args.get("target_path") or "").strip()
            worker_role = str(args.get("worker_role") or "coder").strip()
            if not target_path:
                raise ValueError("target_path must be a non-empty string")
            root_alias, relative = _split_declared_target(target_path)
            registry = _runtime_root_registry()
            resolved = registry.resolve(root_alias, relative)
            exists = resolved.exists()
            state.begin_direct(
                target_intent=target_intent,
                target_path=(
                    relative if root_alias == "musubi"
                    else f"{root_alias}::{relative}"
                ),
                target_exists=exists,
                worker_role=worker_role,
            )
            return json.dumps({
                "status": "ok",
                "mode": state.mode,
                "target_intent": state.target_intent,
                "target_path": state.target_path,
                "target_exists": exists,
                "next_role": state.next_role,
            })

        raw_plan = args.get("plan_markdown")
        if not isinstance(raw_plan, str) or not raw_plan.strip():
            raise _PlanningContractError(
                "invalid_plan_markdown",
                "plan_markdown must be a non-empty string",
            )
        plan_markdown = raw_plan
        manifest_object = args.get("change_manifest")
        model_dump = getattr(manifest_object, "model_dump", None)
        if callable(model_dump):
            manifest_object = model_dump(mode="python")
        manifest = parse_change_manifest_object(manifest_object)
        if manifest is None:
            raise _PlanningContractError(
                "invalid_change_manifest",
                "change_manifest must match the closed manifest schema",
            )
        raw_size = args.get("change_size")
        if not isinstance(raw_size, str):
            raise _PlanningContractError(
                "invalid_change_size",
                "change_size must be small, medium, or large",
            )
        change_size = raw_size.strip()
        raw_chain = args.get("worker_chain")
        if not isinstance(raw_chain, list):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain must be an array of allowed roles",
            )
        if any(not isinstance(role, str) for role in raw_chain):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain must contain only allowed role strings",
            )
        chain = tuple(role.strip() for role in raw_chain)
        # Validate model declaration before any artifact reaches disk.
        if change_size not in ROOT_PLAN_CHANGE_SIZES:
            raise _PlanningContractError(
                "invalid_change_size",
                "change_size must be small, medium, or large",
            )
        if not chain or any(role not in ROOT_PLAN_WORKER_ROLES for role in chain):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain roles must be one of the allowed non-planner roles",
            )
        if not any(role in MUTATION_ROLES for role in chain):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain must contain a mutation role",
            )
        needed = orchestration.spawned_workers + len(chain) + 1
        if needed > MAX_ROOT_WORKERS_HARD:
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain exceeds the hard worker ceiling including recovery",
            )
        if orchestration.planning_artifact_dir is None:
            raise ValueError("planning artifact directory is unavailable")
        persisted = persist_planning_contract(
            plan_markdown,
            manifest_object,
            orchestration.planning_artifact_dir,
        )
        if persisted is None:
            raise _PlanningContractError(
                "invalid_plan_markdown",
                "plan_markdown or change_manifest is invalid",
            )
        paths, artifacts = persisted
        state.commit_root_plan(
            manifest=artifacts.manifest,
            change_size=change_size,
            worker_chain=chain,
            planning_artifacts=(str(path) for path in paths),
        )
        orchestration.max_root_workers = max(
            orchestration.max_root_workers, needed,
        )
        return json.dumps({
            "status": "ok",
            "mode": state.mode,
            "change_size": state.change_size,
            "worker_chain": list(chain),
            "next_role": state.next_role,
            "max_root_workers": orchestration.max_root_workers,
            "planning_artifacts": list(state.planning_artifacts),
        })
    except _PlanningContractError as exc:
        return _root_control_error(exc.error_kind, str(exc), state)
    except (OSError, ValueError) as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def _split_declared_target(target: str) -> tuple[str, str]:
    if "::" not in target:
        return "musubi", target
    root, relative = target.split("::", 1)
    if not root.strip() or not relative.strip():
        raise ValueError("target_path must use ROOT::relative/path")
    return root.strip(), relative.strip()


def _runtime_root_registry() -> RootRegistry:
    root = Path(os.environ.get("MUSUBI_ROOT") or Path.cwd()).resolve()
    raw = os.environ.get(MANIFEST_ENV, "").strip()
    return RootRegistry.from_json(raw, root) if raw else RootRegistry.build(root)


def _tool_wrote_ok(text: str) -> bool:
    """True when an fs tool's JSON result reports a successful write."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("status") == "ok"


def _tool_result_audit_status(text: str) -> str:
    """Reflect deterministic JSON tool denials in the append-only audit."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return "ok"
    if isinstance(payload, dict) and (
        payload.get("status") == "error" or "error" in payload
    ):
        return "error"
    return "ok"


def _tool_audit_args(args: dict[str, Any], text: str) -> dict[str, Any]:
    """Attach resolver evidence from successful filesystem tool results."""
    enriched = dict(args)
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return enriched
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return enriched
    for key in ("root", "grant_id", "path", "resolved_path"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            enriched[key] = value
    return enriched


#: The tool a worker calls to say the pushed skill does not fit its brief.
#: Not a capability — a summarizer with zero tools must still be able to say
#: it, so `select_child_tools` grants it unconditionally.
SKILL_MISMATCH_TOOL = "musubi_report_skill_mismatch"


def _record_skill_mismatch(
    name: str, args: dict[str, Any], text: str, log: Any,
) -> None:
    """Record an accepted skill-mismatch report into the active worker's sink.

    No-op unless a `run_subagent` upstream is collecting. Only a report the
    SERVER accepted counts: the worker states the mismatch, the harness decides
    whether the statement is well-formed, and the parent reads the harness's
    verdict — never the worker's raw claim.
    """
    if name != SKILL_MISMATCH_TOOL:
        return
    sink = _worker_skill_reports.get()
    if sink is None:
        return
    from agent.jsonio import loads_dict

    payload = loads_dict(text)
    if payload.get("status") != "recorded":
        return
    report = {
        "pushed_skill_id": str(payload.get("pushed_skill_id") or ""),
        "reason": str(payload.get("reason") or ""),
        "suggested_skill_id": str(payload.get("suggested_skill_id") or "") or None,
    }
    sink.append(report)
    emit_runtime_log(
        log,
        "[agent]   skill mismatch reported "
        f"pushed={report['pushed_skill_id'] or '?'}"
        + (
            f" suggested={report['suggested_skill_id']}"
            if report["suggested_skill_id"] else ""
        ),
        category="skills",
    )


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
        root = args.get("root", "musubi")
        if not isinstance(root, str) or not root:
            root = "musubi"
        sink.add(path if root == "musubi" else f"{root}::{path}")


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
    return bounded(text, limit, collapse=False)


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
    emit_runtime_log(log, " ".join(parts), category="model")


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
    emit_runtime_log(log, " ".join(parts), category="model")


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
    emit_runtime_log(log, " ".join(parts), category="model")


if __name__ == "__main__":
    raise SystemExit(main())
