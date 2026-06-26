"""Agent CLI — drive the Musubi MCP server via a direct LLM API.

musubi-tier: substrate
expires-when: never — the agent is the model's native mode (per
  CLAUDE.md), and this is its vendor-agnostic Python entry point.
  Replaces the Copilot-Chat-only access path with one that works
  against any LLM whose Python SDK exposes a tool-use API.

Usage:
    agent-agent "your task"                      # vendor auto-detected from env
    agent-agent "your task" --vendor anthropic
    agent-agent "your task" --vendor openai --model gpt-5-mini
    agent-agent "your task" --vendor ollama --model llama3.1   # local, no key
    agent-agent "your task" --profile azure.work               # .musubi/llm.json
    python -m agent.run "your task"              # equivalent

Vendor selection precedence: --vendor → --profile → the .musubi/llm.json
`default` profile → env-key detection. On-prem endpoints (base URL, family,
api-key, curl transport for Azure) are configured in `.musubi/llm.json`; see
`agent/config.py`.

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
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.context import build_system_prompt, effort_floor, fit_context
from agent.mcp_gateway import (
    McpGateway,
    find_mcp_config_path,
    load_mcp_servers,
    mcp_config_candidates,
)
from agent.vendors import LMResponse, LMRouter, build_from_profile, build_vendor

DEFAULT_MAX_CYCLES = 16

#: Ceiling for output tokens; effort routing starts below this and escalates
#: to it only when a cycle actually stops on `max_tokens`.
EFFORT_CEILING = 4096


@dataclass
class Orchestration:
    """Context that lets the loop run sub-agents the model spawns.

    `parent_session_id` owns the spawn parentage; `parent_agent_name` is the
    firewall identity ("agent" → MAIN_SUBAGENT_ALLOWLIST["agent"]). Disabled
    (no spawning) when `parent_session_id` is None.
    """

    parent_session_id: str | None
    parent_agent_name: str = "agent"

    @property
    def enabled(self) -> bool:
        return self.parent_session_id is not None


# ── Public entry ────────────────────────────────────────────────────────────


async def run_agent(
    task: str,
    vendor: LMRouter,
    musubi_dir: Path,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    log: Any = sys.stderr,
    mcp_config: str | os.PathLike[str] | None = None,
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
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env=_server_env(),
    )

    # Track the result inside the MCP contexts but raise the
    # max-cycles error OUTSIDE them — anyio's TaskGroup wraps any
    # exception raised inside the stdio_client/ClientSession contexts
    # in a BaseExceptionGroup, which would defeat `except RuntimeError`
    # at every call site (including main()).
    final_answer: str | None = None
    loop_error: BaseException | None = None

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
        print(
            f"[agent] vendor={vendor.name} model={vendor.model} "
            f"tools={len(tools)} (musubi={len(mcp_tools)}, external={n_external})",
            file=log,
        )

        # Open a parent session up front so the model's sub-agent spawns
        # have a valid parent. The "agent" identity short-circuits the
        # spawn firewall to MAIN_SUBAGENT_ALLOWLIST["agent"] regardless of
        # the session's pipeline tag (policy_engine `_effective_spawn_roles`).
        orchestration = Orchestration(
            parent_session_id=await _open_parent_session(session, task, log),
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": task},
        ]
        # Catch a loop failure (an LLM/network error from `vendor.call`, a
        # dispatch error) HERE, inside the `async with`, and stash it. Letting
        # it escape into the stack's `__aexit__` makes anyio re-wrap it in a
        # BaseExceptionGroup (the unreadable multi-page traceback) and defeats
        # `except RuntimeError` at every call site. We re-raise it cleanly
        # outside the contexts below.
        try:
            final_answer, _ = await _run_loop(
                session, vendor, tools, messages,
                max_cycles=max_cycles, log=log, orchestration=orchestration,
                gateway=gateway,
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
    """
    final_answer: str | None = None
    cycles_used = 0
    for cycle in range(max_cycles):
        cycles_used = cycle + 1
        # IntelligentContext: trim an over-budget conversation deterministically
        # before the call (oldest/largest tool results elided, pairing intact).
        messages = fit_context(messages)
        resp = _call_with_effort(vendor, messages, tools)
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
        _log_cycle(log, cycle, resp.stop_reason, tool_uses, resp.usage)

        if resp.stop_reason != "tool_use" or not tool_uses:
            final_answer = _extract_text(resp.content)
            break

        tool_results = await _dispatch(
            session, tool_uses, log,
            vendor=vendor, tools=tools, orchestration=orchestration,
            gateway=gateway,
        )
        messages.append({"role": "user", "content": tool_results})

    return final_answer, cycles_used


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
        "--vendor",
        choices=["anthropic", "openai", "ollama", "azure", "genai_farm"],
        default=None,
        help=(
            "LLM vendor. Defaults to --profile, then to whichever API key is "
            "present in env."
        ),
    )
    ap.add_argument(
        "--profile",
        default=None,
        help=(
            "Named endpoint from .musubi/llm.json as <family>.<name> "
            "(e.g. azure.work). Used when --vendor is not given."
        ),
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Model id. Vendor-specific default if omitted.",
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
    args = ap.parse_args(argv)

    try:
        vendor = _resolve_vendor(args.vendor, args.profile, args.model)
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


def _resolve_vendor(
    vendor: str | None, profile: str | None, model: str | None
) -> LMRouter:
    """Pick the LMRouter. Precedence: --vendor → --profile → file default → env.

    `--model` overrides the profile's model id (the deployment for Azure is set
    in the profile; use a dedicated profile to switch deployments).
    """
    if vendor:
        return build_vendor(vendor, model=model)

    from agent.config import find_config_path, load_profile

    if profile:
        prof = load_profile(profile)
        return build_from_profile(_apply_model(prof, model))

    if find_config_path() is not None:
        prof = load_profile(None)  # the file's `default`
        return build_from_profile(_apply_model(prof, model))

    return build_vendor(None, model=model)


def _apply_model(profile: dict[str, Any], model: str | None) -> dict[str, Any]:
    return {**profile, "model": model} if model else profile


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
) -> LMResponse:
    """Effort routing: start at a low output-token cap, escalate only on need.

    Most cycles emit a small tool_use block, so the floor cap costs nothing
    they needed. If a call truncates (`stop_reason == "max_tokens"`), re-issue
    the same request once at the ceiling so a real answer is never cut off.
    """
    floor = min(effort_floor(), EFFORT_CEILING)
    resp = vendor.call(messages, tools, max_tokens=floor)
    if resp.stop_reason == "max_tokens" and floor < EFFORT_CEILING:
        resp = vendor.call(messages, tools, max_tokens=EFFORT_CEILING)
    return resp


async def _dispatch(
    session: ClientSession,
    tool_uses: list[dict[str, Any]],
    log: Any,
    *,
    vendor: LMRouter | None = None,
    tools: list[dict[str, Any]] | None = None,
    orchestration: Orchestration | None = None,
    gateway: McpGateway | None = None,
) -> list[dict[str, Any]]:
    """Call each tool and collect Anthropic-shaped tool_result blocks.

    When `orchestration` is enabled and the model calls
    `musubi_spawn_subagent`, the spawn is run to completion in-process (port of
    the extension's dispatcher) and the sub-agent's summary becomes the tool
    result — so the model just spawns and gets the answer back.

    Every other tool is routed via `gateway` (when set) to its owning session
    and original name — so a federated `<server>__<tool>` call lands on that
    external server. With no gateway, the call goes to `session` verbatim.
    """
    results: list[dict[str, Any]] = []
    for tu in tool_uses:
        name = tu.get("name", "")
        args = tu.get("input") or {}

        if (
            name == "musubi_spawn_subagent"
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
            print(f"[agent]   → spawn_subagent(role={args.get('role')!r})", file=log)
            try:
                from agent import subagent

                content = await subagent.run_subagent(
                    session, injected, vendor, tools, log
                )
            except Exception as exc:  # noqa: BLE001 — surface to the model
                content = f"[subagent error] {type(exc).__name__}: {exc}"
        else:
            print(
                f"[agent]   → {name}({_truncate(json.dumps(args), 60)})",
                file=log,
            )
            target = gateway.route(name) if gateway is not None else (session, name)
            if target is None:
                content = f"[tool error] no MCP server owns tool {name!r}"
            else:
                target_session, original_name = target
                try:
                    result = await target_session.call_tool(
                        original_name, arguments=args
                    )
                    content = _first_text(result)
                except Exception as exc:  # noqa: BLE001 — surface errors to the model
                    content = f"[tool error] {type(exc).__name__}: {exc}"

        results.append({
            "type": "tool_result",
            "tool_use_id": tu.get("id", ""),
            "content": content,
        })
    return results


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


def _log_cycle(
    log: Any,
    cycle: int,
    stop_reason: str,
    tool_uses: list[dict[str, Any]],
    usage: dict[str, Any] | None,
) -> None:
    parts = [f"[agent] cycle {cycle}: stop={stop_reason}", f"tools={len(tool_uses)}"]
    if usage:
        toks = usage.get("output_tokens") or usage.get("completion_tokens")
        if toks is not None:
            parts.append(f"out_tokens={toks}")
        # CacheAligner measurement: how much of the prefix was served from the
        # prompt cache vs. (re)written this cycle.
        cache_read = usage.get("cache_read_input_tokens")
        cache_write = usage.get("cache_creation_input_tokens")
        if cache_read:
            parts.append(f"cache_read={cache_read}")
        if cache_write:
            parts.append(f"cache_write={cache_write}")
    print(" ".join(parts), file=log)


if __name__ == "__main__":
    raise SystemExit(main())
