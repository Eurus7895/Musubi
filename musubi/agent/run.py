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
    OLLAMA_HOST         optional; ollama base URL (default http://localhost:11434)

The Musubi MCP server is auto-located: same repo as this module by
default, overridable with --musubi or MUSUBI_ROOT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.context import build_system_prompt, effort_floor, fit_context
from agent.budget import (
    BudgetEnforcer,
    BudgetExhaustedError,
    estimate_call_credits,
    estimate_tokens_from_chars,
)
from agent.boundary import (
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
from agent.vendors import LMResponse, LMRouter, build_from_profile, build_vendor

DEFAULT_MAX_CYCLES = 16

DEFAULT_AGENT_MAX_CREDITS = 30.0

#: Ceiling for output tokens; effort routing starts below this and escalates
#: to it only when a cycle actually stops on `max_tokens`.
EFFORT_CEILING = 4096

#: Per-cycle fan-out width guard: at most this many workers of the SAME role may
#: be spawned in one model turn. Bounds runaway fan-out when workers run in
#: parallel. Mirrors `max_spawns_per_role_per_turn` in agent.agent.md.
DEFAULT_MAX_SPAWNS_PER_ROLE = 3


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
    credits: float = 0.0

    def record_cycle(
        self,
        *,
        lm_ms: int,
        tokens_in: int,
        tokens_out: int,
        credits: float,
    ) -> None:
        self.cycles += 1
        self.lm_ms += lm_ms
        self.tokens_in_estimate += tokens_in
        self.tokens_out_estimate += tokens_out
        self.credits += credits


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
) -> str:
    """Drive one agent turn end-to-end. Returns the final assistant text.

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
    budget = _build_budget(max_credits, log)
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
        gateway.register_local(
            session, [_mcp_to_anthropic_tool(t) for t in mcp_tools]
        )
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
        n_external = len(tools) - len(mcp_tools)
        profile_part = f"profile={vendor_source} " if vendor_source else ""
        print(
            f"[agent] vendor={vendor.name} model={vendor.model} {profile_part}"
            f"tools={len(tools)} (musubi={len(mcp_tools)}, external={n_external})",
            file=log,
        )

        # Open a parent session up front so the model's sub-agent spawns
        # have a valid parent. The "agent" identity short-circuits the
        # spawn firewall to MAIN_SUBAGENT_ALLOWLIST["agent"] regardless of
        # the session's pipeline tag (policy_engine `_effective_spawn_roles`).
        parent_session_id = await _open_parent_session(session, task, log)
        orchestration = Orchestration(parent_session_id=parent_session_id)
        system_prompt = build_system_prompt()
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
            final_answer, _ = await run_unit(
                session, vendor, tools,
                system_prompt=system_prompt,
                user_message=task,
                max_cycles=max_cycles, log=log,
                orchestration=orchestration, gateway=gateway,
                salvage_on_exhaust=True,
                compression_db_path=context_compression_db_path,
                initial_messages=initial_messages,
                role="agent",
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
    stats: AgentRunStats | None = None,
    budget: BudgetEnforcer | None = None,
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
        _check_budget_preflight(
            budget, vendor.model, input_tokens_est, log,
        )
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
        cycle_credits = estimate_call_credits(
            vendor.model, tokens_in, tokens_out, cached_tokens,
        )
        if stats is not None:
            stats.record_cycle(
                lm_ms=lm_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                credits=cycle_credits,
            )
        _charge_budget_postflight(
            budget, vendor.model, cycle_credits, log,
        )
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
        _log_cycle(
            log, cycle, resp.stop_reason, tool_uses, resp.usage,
            tokens_out=tokens_out,
            attempt_count=len(effort.attempts),
        )
        _log_cycle_cost(
            log, cycle, lm_ms, tokens_in, tokens_out, cycle_credits, budget,
        )

        text = _extract_text(resp.content)
        if text:
            last_text = text  # remember even when the model also called a tool

        if resp.stop_reason != "tool_use" or not tool_uses:
            final_answer = text
            break

        tool_results = await _dispatch(
            session, tool_uses, log,
            vendor=vendor, tools=(spawn_catalog or tools),
            orchestration=orchestration, gateway=gateway,
            compression_db_path=compression_db_path,
            role=role,
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
                _check_budget_preflight(
                    budget, vendor.model, input_tokens_est, log,
                )
                lm_started = time.perf_counter()
                effort = await asyncio.to_thread(
                    _call_with_effort, vendor, final_messages, []
                )
                lm_ms = int((time.perf_counter() - lm_started) * 1000)
                resp = effort.response
                tokens_in, tokens_out, cached_tokens = _cycle_token_counts(
                    effort.attempts, input_tokens_est,
                )
                cycle_credits = estimate_call_credits(
                    vendor.model, tokens_in, tokens_out, cached_tokens,
                )
                if stats is not None:
                    stats.record_cycle(
                        lm_ms=lm_ms,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        credits=cycle_credits,
                    )
                _charge_budget_postflight(
                    budget, vendor.model, cycle_credits, log,
                )
                _log_cycle_cost(
                    log, max_cycles, lm_ms, tokens_in, tokens_out,
                    cycle_credits, budget,
                )
                final_answer = _extract_text(resp.content) or None
            except Exception as exc:  # noqa: BLE001 — fall through to the raise
                print(
                    f"[agent] forced final call failed: "
                    f"{type(exc).__name__}: {exc}",
                    file=log,
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
    stats: AgentRunStats | None = None,
    budget: BudgetEnforcer | None = None,
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


def main(argv: list[str] | None = None) -> int:
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
        "--max-credits",
        type=float,
        default=None,
        help=(
            "Per-turn credit cap. Defaults to MUSUBI_AGENT_MAX_CREDITS, "
            f"then {DEFAULT_AGENT_MAX_CREDITS:g}. Use 0 to disable."
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


def _build_budget(max_credits: float | None, log: Any) -> BudgetEnforcer | None:
    cap = max_credits
    if cap is None:
        raw = os.environ.get("MUSUBI_AGENT_MAX_CREDITS", "").strip()
        if raw:
            try:
                cap = float(raw)
            except ValueError as exc:
                raise RuntimeError(
                    f"MUSUBI_AGENT_MAX_CREDITS must be numeric, got {raw!r}"
                ) from exc
        else:
            cap = DEFAULT_AGENT_MAX_CREDITS
    if cap <= 0:
        print("[agent] budget: disabled", file=log)
        return None
    budget = BudgetEnforcer(cap)
    print(
        f"[agent] budget: {budget.max_credits:.1f} credits "
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
    budget: BudgetEnforcer | None,
    family: str,
    input_tokens: int,
    log: Any,
) -> None:
    if budget is None:
        return
    estimated_output = max(1, int(input_tokens * 0.25))
    credits = estimate_call_credits(family, input_tokens, estimated_output)
    status = budget.preflight(credits)
    if status == "allow":
        return
    projected = budget.credits_used + credits
    print(
        f"[agent] budget {status}: projected={projected:.2f}/"
        f"{budget.max_credits:.2f} credits this_call={credits:.2f}",
        file=log,
    )
    if status == "halt":
        raise BudgetExhaustedError(
            phase="preflight",
            credits_used=projected,
            max_credits=budget.max_credits,
            family=family,
            this_call_credits=credits,
        )


def _charge_budget_postflight(
    budget: BudgetEnforcer | None,
    family: str,
    credits: float,
    log: Any,
) -> None:
    if budget is None:
        return
    status = budget.charge(credits)
    if status == "allow":
        return
    print(
        f"[agent] budget {status}: used={budget.credits_used:.2f}/"
        f"{budget.max_credits:.2f} credits this_call={credits:.2f}",
        file=log,
    )
    if status == "halt":
        raise BudgetExhaustedError(
            phase="postflight",
            credits_used=budget.credits_used,
            max_credits=budget.max_credits,
            family=family,
            this_call_credits=credits,
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
    budget: BudgetEnforcer | None = None,
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
    refused = _spawn_overflow_ids(tool_uses, log)
    coros = [
        _dispatch_one(
            tu, session, log,
            vendor=vendor, tools=tools,
            orchestration=orchestration, gateway=gateway,
            refused=tu.get("id", "") in refused,
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


def _spawn_overflow_ids(tool_uses: list[dict[str, Any]], log: Any) -> set[str]:
    """tool_use ids of spawn calls that exceed the per-role width cap.

    Keeps the first `DEFAULT_MAX_SPAWNS_PER_ROLE` spawns of each role in the
    batch and marks the rest refused. Non-spawn calls are never capped.
    """
    seen: dict[str, int] = {}
    overflow: set[str] = set()
    for tu in tool_uses:
        if tu.get("name") != "musubi_spawn_subagent":
            continue
        role = str((tu.get("input") or {}).get("role", ""))
        seen[role] = seen.get(role, 0) + 1
        if seen[role] > DEFAULT_MAX_SPAWNS_PER_ROLE:
            overflow.add(tu.get("id", ""))
            print(
                f"[agent]   ⨯ refused extra worker(role={role!r}): "
                f"per-turn cap {DEFAULT_MAX_SPAWNS_PER_ROLE} reached",
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
    refused: bool,
    compression_db_path: Path | None,
    role: str = "agent",
    budget: BudgetEnforcer | None = None,
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
            denied = f"[policy denied] {decision.reason}"
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
        if refused:
            result = (
                f'{{"status": "refused", "reason": "per-turn spawn cap '
                f'({DEFAULT_MAX_SPAWNS_PER_ROLE}) reached for role '
                f'{args.get("role")!r}"}}'
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
        text = _first_text(result)
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
    parts = [f"[agent] cycle {cycle}: stop={stop_reason}", f"tools={len(tool_uses)}"]
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
    credits: float,
    budget: BudgetEnforcer | None,
) -> None:
    parts = [
        f"[agent] cycle {cycle}: lm_ms={lm_ms}",
        f"in_tokens={tokens_in}",
        f"out_tokens={tokens_out}",
        f"credits={credits:.4f}",
    ]
    if budget is not None:
        parts.append(
            f"budget={budget.credits_used:.2f}/{budget.max_credits:.2f}"
        )
    print(" ".join(parts), file=log)


def _log_turn_usage(
    log: Any,
    stats: AgentRunStats,
    budget: BudgetEnforcer | None,
) -> None:
    if stats.cycles <= 0:
        return
    parts = [
        f"[agent] usage cycles={stats.cycles}",
        f"lm_ms={stats.lm_ms}",
        f"in_tokens={stats.tokens_in_estimate}",
        f"out_tokens={stats.tokens_out_estimate}",
        f"credits={stats.credits:.4f}",
    ]
    if budget is not None:
        parts.append(
            f"budget={budget.credits_used:.2f}/{budget.max_credits:.2f}"
        )
    print(" ".join(parts), file=log)


if __name__ == "__main__":
    raise SystemExit(main())
