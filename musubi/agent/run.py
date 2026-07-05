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
import json
import os
import re
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MUSUBI_MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(_MUSUBI_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_MUSUBI_MODULE_ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.context import build_system_prompt, effort_floor, fit_context
from agent.budget import (
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_call_credits,
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
from agent.scope import ScopeHint, ScopeKind, classify_task, is_simple_scope
from agent.vendors import LMResponse, LMRouter, build_from_profile, build_vendor
from tool_surface import filter_tool_catalog, tool_names_for_surface

DEFAULT_MAX_CYCLES = 16

DEFAULT_AGENT_MAX_TOKENS = 200_000

#: Ceiling for output tokens; effort routing starts below this and escalates
#: to it only when a cycle actually stops on `max_tokens`.
EFFORT_CEILING = 4096

#: Per-cycle fan-out width guard: at most this many workers of the SAME role may
#: be spawned in one model turn. Bounds runaway fan-out when workers run in
#: parallel. Mirrors `max_spawns_per_role_per_turn` in agent.agent.md.
DEFAULT_MAX_SPAWNS_PER_ROLE = 3

ORDER_SENSITIVE_FILE_TOOLS: frozenset[str] = frozenset({
    "musubi_write_file",
    "musubi_append_file",
    "musubi_edit_file",
})


#: How deep workers may nest. depth 0 = root task; a worker at depth < max_depth
#: that is itself allowed to spawn may summon workers one level down. With the
#: default, the root and its direct workers can spawn; their workers are leaves.
DEFAULT_MAX_DEPTH = 2


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

    @property
    def can_spawn_deeper(self) -> bool:
        """True if a worker at this depth is still allowed to nest."""
        return self.enabled and self.depth < self.max_depth


@dataclass
class AgentRunStats:
    """Cumulative telemetry for one CLI turn across root and workers."""

    cycles: int = 0
    lm_ms: int = 0
    tokens_in_estimate: int = 0
    tokens_out_estimate: int = 0
    estimated_credits: float = 0.0

    def record_cycle(
        self,
        *,
        lm_ms: int,
        tokens_in: int,
        tokens_out: int,
        estimated_credits: float,
    ) -> None:
        self.cycles += 1
        self.lm_ms += lm_ms
        self.tokens_in_estimate += tokens_in
        self.tokens_out_estimate += tokens_out
        self.estimated_credits += estimated_credits


@dataclass
class EffortCallResult:
    """Final response plus every vendor call made to obtain it."""

    response: LMResponse
    attempts: list[LMResponse]


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
    max_credits: float | None = None,
    max_tokens: int | None = None,
    tool_surface: str | None = None,
    pipeline: str | None = None,
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
    budget = _build_token_budget(max_tokens, max_credits, log)
    scope_hint = classify_task(task)
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
        parent_session_id = await _open_parent_session(session, task, log)
        orchestration = Orchestration(parent_session_id=parent_session_id)
        print(f"[agent] {scope_hint.log_line()}", file=log)
        system_prompt = build_system_prompt(scope_hint.prompt_block())
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
                f"[agent] chat_id={chat_id} replay_messages="
                f"{max(0, len(initial_messages) - 1)}",
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
    role: str = "agent",
    scope_hint: ScopeHint | None = None,
    stats: AgentRunStats | None = None,
    budget: TokenBudgetEnforcer | None = None,
    audit_db_path: Path | None = None,
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
    for cycle in range(max_cycles):
        cycles_used = cycle + 1
        # IntelligentContext: trim an over-budget conversation deterministically
        # before the call (oldest/largest tool results elided, pairing intact).
        messages = fit_context(messages, compression_db_path=compression_db_path)
        input_tokens_est = _estimate_input_tokens(messages, tools)
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
        lm_started = time.perf_counter()
        effort = await asyncio.to_thread(_call_with_effort, vendor, messages, tools)
        lm_ms = int((time.perf_counter() - lm_started) * 1000)
        resp = effort.response
        tokens_in, tokens_out, cached_tokens = _cycle_token_counts(
            effort.attempts, input_tokens_est,
        )
        estimated_credits = estimate_call_credits(
            vendor.model, tokens_in, tokens_out, cached_tokens,
        )
        if stats is not None:
            stats.record_cycle(
                lm_ms=lm_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                estimated_credits=estimated_credits,
            )
        _charge_budget_postflight(budget, tokens_in + tokens_out, log)
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
        _log_cycle(
            log, cycle, resp.stop_reason, tool_uses, resp.usage,
            tokens_out=tokens_out,
            attempt_count=len(effort.attempts),
        )
        _log_cycle_cost(
            log, cycle, lm_ms, tokens_in, tokens_out, estimated_credits, budget,
        )

        text = _extract_text(resp.content)
        if text:
            last_text = text  # remember even when the model also called a tool

        if not tool_uses:
            final_answer = text
            break

        if resp.stop_reason == "max_tokens":
            print(
                "[agent] max_tokens response contained tool calls; "
                "not dispatching possibly truncated tool arguments",
                file=log,
            )
            final_answer = _truncated_tool_call_answer(tool_uses)
            break

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
        messages.append({"role": "user", "content": tool_results})

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
                final_messages = fit_context(
                    messages, compression_db_path=compression_db_path,
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
                lm_started = time.perf_counter()
                effort = await asyncio.to_thread(
                    _call_with_effort, vendor, final_messages, []
                )
                lm_ms = int((time.perf_counter() - lm_started) * 1000)
                resp = effort.response
                tokens_in, tokens_out, cached_tokens = _cycle_token_counts(
                    effort.attempts, input_tokens_est,
                )
                estimated_credits = estimate_call_credits(
                    vendor.model, tokens_in, tokens_out, cached_tokens,
                )
                if stats is not None:
                    stats.record_cycle(
                        lm_ms=lm_ms,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        estimated_credits=estimated_credits,
                    )
                _charge_budget_postflight(budget, tokens_in + tokens_out, log)
                _log_cycle_cost(
                    log, max_cycles, lm_ms, tokens_in, tokens_out,
                    estimated_credits, budget,
                )
                final_answer = _extract_text(resp.content) or None
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
    initial_messages: list[dict[str, Any]] | None = None,
    role: str = "agent",
    scope_hint: ScopeHint | None = None,
    stats: AgentRunStats | None = None,
    budget: TokenBudgetEnforcer | None = None,
    audit_db_path: Path | None = None,
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
        role=role,
        scope_hint=scope_hint,
        stats=stats,
        budget=budget,
        audit_db_path=audit_db_path,
    )


async def _open_parent_session(session: ClientSession, task: str, log: Any) -> str | None:
    """Create the agent's owning session; None if it can't (spawns disabled)."""
    try:
        raw = await _call_tool_text(session, "musubi_new_session", {"request": task[:500]})
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
        "--max-credits",
        type=float,
        default=None,
        help=(
            "Deprecated compatibility flag. Credits are no longer used for "
            "budget enforcement; use --max-tokens instead. A value of 0 "
            "still disables the token cap for older scripts."
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
                max_credits=args.max_credits,
                max_tokens=args.max_tokens,
                tool_surface=args.tool_surface,
                pipeline=args.pipeline,
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
    from agent.config import find_config_path, load_profile

    if profile:
        prof = load_profile(profile)
        label = f"{prof['family']}.{prof['profile']} (--profile)"
        return build_from_profile(prof), label

    if find_config_path() is not None:
        prof = load_profile(None)  # the file's `default`
        label = f"{prof['family']}.{prof['profile']} (llm.json default)"
        return build_from_profile(prof), label

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
    max_credits: float | None,
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

    if max_credits is not None:
        if max_credits <= 0 and max_tokens is None:
            cap = 0
        else:
            print(
                "[agent] --max-credits is deprecated and ignored for budget "
                "enforcement; use --max-tokens",
                file=log,
            )

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
            messages.append({"role": "user", "content": f"[prior tool result]\n{content}"})
        elif role == "system":
            messages.append({"role": "user", "content": f"[prior system note]\n{content}"})
    return messages


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
) -> EffortCallResult:
    """Effort routing: start at a low output-token cap, escalate only on need.

    Most cycles emit a small tool_use block, so the floor cap costs nothing
    they needed. If a call truncates (`stop_reason == "max_tokens"`), re-issue
    the same request once at the ceiling so a real answer is never cut off.
    """
    floor = min(effort_floor(), EFFORT_CEILING)
    resp = vendor.call(messages, tools, max_tokens=floor)
    attempts = [resp]
    if resp.stop_reason == "max_tokens" and floor < EFFORT_CEILING:
        resp = vendor.call(messages, tools, max_tokens=EFFORT_CEILING)
        attempts.append(resp)
    return EffortCallResult(response=resp, attempts=attempts)


def _estimate_input_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> int:
    chars = len(json.dumps(messages, default=str, ensure_ascii=False))
    chars += len(json.dumps(tools, default=str, ensure_ascii=False))
    return estimate_tokens_from_chars(chars)


def _cycle_token_counts(
    responses: LMResponse | list[LMResponse],
    input_estimate: int,
) -> tuple[int, int, int]:
    attempts = responses if isinstance(responses, list) else [responses]
    totals = [
        _single_response_token_counts(resp, input_estimate)
        for resp in attempts
    ]
    return (
        sum(t[0] for t in totals),
        sum(t[1] for t in totals),
        sum(t[2] for t in totals),
    )


def _single_response_token_counts(
    resp: LMResponse,
    input_estimate: int,
) -> tuple[int, int, int]:
    usage = resp.usage or {}
    tokens_in = _usage_int(usage, "input_tokens", "prompt_tokens") or input_estimate
    output_estimate = estimate_tokens_from_chars(
        len(json.dumps(resp.content, default=str, ensure_ascii=False))
    )
    tokens_out = (
        _usage_int(usage, "output_tokens", "completion_tokens")
        or output_estimate
    )
    cached = (
        _usage_int(usage, "cache_read_input_tokens", "cached_input_tokens")
        or _nested_usage_int(usage, ("prompt_tokens_details", "cached_tokens"))
        or 0
    )
    return tokens_in, tokens_out, min(cached, tokens_in)


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
    spawns BEFORE launch so a single turn cannot fan out without bound.
    """
    refused = _spawn_overflow_reasons(
        tool_uses, log, role=role, scope_hint=scope_hint, cycle_index=cycle_index,
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
) -> dict[str, str]:
    """tool_use ids of spawn calls that exceed the active route width cap.

    Keeps the first `DEFAULT_MAX_SPAWNS_PER_ROLE` spawns of each role in the
    batch by default. Simple root tasks are tighter: one coder worker per
    model turn. Non-spawn calls are never capped.
    """
    caller_role = role
    seen: dict[str, int] = {}
    overflow: dict[str, str] = {}
    for tu in tool_uses:
        if tu.get("name") != "musubi_spawn_subagent":
            continue
        spawn_role = str((tu.get("input") or {}).get("role", ""))
        seen[spawn_role] = seen.get(spawn_role, 0) + 1
        cap = DEFAULT_MAX_SPAWNS_PER_ROLE
        reason = (
            f"per-turn spawn cap ({DEFAULT_MAX_SPAWNS_PER_ROLE}) reached "
            f"for role {spawn_role!r}"
        )
        if caller_role == "agent" and spawn_role == "coder" and is_simple_scope(scope_hint):
            cap = 1
            reason = "simple task route allows only one coder worker"
        if (
            caller_role == "agent"
            and spawn_role == "coder"
            and scope_hint is not None
            and scope_hint.kind is ScopeKind.MEDIUM_CHANGE
            and cycle_index == 0
        ):
            overflow[tu.get("id", "")] = (
                "medium task route requires planner before coder; spawn planner "
                "first, then pass the planner summary to coder"
            )
            print(
                "[agent]   x refused worker(role='coder'): "
                "medium task route requires planner before coder",
                file=log,
            )
            continue
        if seen[spawn_role] > cap:
            overflow[tu.get("id", "")] = reason
            print(
                f"[agent]   ⨯ refused extra worker(role={spawn_role!r}): "
                f"{reason}",
                file=log,
            )
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
        injected = {
            **args,
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


def _file_tool_argument_error(name: str, args: Any) -> str | None:
    if name not in ORDER_SENSITIVE_FILE_TOOLS:
        return None
    if not isinstance(args, dict):
        return "arguments must be an object"

    errors: list[str] = []
    _require_string(args, "path", errors)
    if name in {"musubi_write_file", "musubi_append_file"}:
        _require_string(args, "content", errors)
        _optional_bool(args, "create_parents", errors)
    elif name == "musubi_edit_file":
        _require_string(args, "old_string", errors)
        _require_string(args, "new_string", errors)
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


def _model_action(stop_reason: str, tool_uses: list[dict[str, Any]]) -> str:
    if stop_reason == "max_tokens":
        return "truncated"
    if tool_uses:
        return "tool_calls"
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
        f"[agent] cycle {cycle}: model_action={_model_action(stop_reason, tool_uses)}",
        f"stop={stop_reason}",
        f"tools={len(tool_uses)}",
    ]
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
    estimated_credits: float,
    budget: TokenBudgetEnforcer | None,
) -> None:
    parts = [
        f"[agent] cycle {cycle}: lm_ms={lm_ms}",
        f"in_tokens={tokens_in}",
        f"out_tokens={tokens_out}",
        f"estimated_credits={estimated_credits:.4f}",
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
        f"estimated_credits={stats.estimated_credits:.4f}",
    ]
    if budget is not None:
        parts.append(
            f"token_budget={budget.tokens_used}/{budget.max_tokens}"
        )
    print(" ".join(parts), file=log)


if __name__ == "__main__":
    raise SystemExit(main())
