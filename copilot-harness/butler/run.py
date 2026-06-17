"""Butler CLI — drive the harness MCP server via a direct LLM API.

harness-tier: substrate
expires-when: never — the butler is the model's native mode (per
  CLAUDE.md), and this is its vendor-agnostic Python entry point.
  Replaces the Copilot-Chat-only access path with one that works
  against any LLM whose Python SDK exposes a tool-use API.

Usage:
    agent-butler "your task"                      # vendor auto-detected from env
    agent-butler "your task" --vendor anthropic
    agent-butler "your task" --vendor openai --model gpt-5-mini
    python -m butler.run "your task"              # equivalent

Env vars:
    ANTHROPIC_API_KEY   used by the anthropic vendor
    OPENAI_API_KEY      used by the openai vendor

The harness MCP server is auto-located: same repo as this module by
default, overridable with --harness or HARNESS_ROOT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from butler.vendors import LMRouter, build_vendor

DEFAULT_MAX_CYCLES = 16


# ── Public entry ────────────────────────────────────────────────────────────


async def run_butler(
    task: str,
    vendor: LMRouter,
    harness_dir: Path,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    log: Any = sys.stderr,
) -> str:
    """Drive one butler turn end-to-end. Returns the final assistant text.

    Spawns the harness MCP server, lists its tools, hands them to the
    LLM via `vendor.call`, dispatches whatever tools the model asks
    for, feeds results back, repeats until the model stops asking for
    tools (`stop_reason != "tool_use"`) OR `max_cycles` is hit.
    """
    server_path = harness_dir / "server.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env=None,
    )

    # Track the result inside the MCP contexts but raise the
    # max-cycles error OUTSIDE them — anyio's TaskGroup wraps any
    # exception raised inside the stdio_client/ClientSession contexts
    # in a BaseExceptionGroup, which would defeat `except RuntimeError`
    # at every call site (including main()).
    final_answer: str | None = None

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools = [_mcp_to_anthropic_tool(t) for t in mcp_tools]
            print(
                f"[butler] vendor={vendor.name} model={vendor.model} "
                f"tools={len(tools)}",
                file=log,
            )

            messages: list[dict[str, Any]] = [
                {"role": "user", "content": task},
            ]

            for cycle in range(max_cycles):
                resp = vendor.call(messages, tools)
                messages.append({"role": "assistant", "content": resp.content})

                tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
                _log_cycle(log, cycle, resp.stop_reason, tool_uses, resp.usage)

                if resp.stop_reason != "tool_use" or not tool_uses:
                    final_answer = _extract_text(resp.content)
                    break

                tool_results = await _dispatch(session, tool_uses, log)
                messages.append({"role": "user", "content": tool_results})

    if final_answer is None:
        raise RuntimeError(
            f"butler exceeded {max_cycles} cycles without a final answer"
        )
    return final_answer


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="agent-butler",
        description=(
            "Drive the harness MCP server via a direct LLM API "
            "(no Copilot Chat required)."
        ),
    )
    ap.add_argument("task", help="The user task to run.")
    ap.add_argument(
        "--vendor",
        choices=["anthropic", "openai"],
        default=None,
        help="LLM vendor. Defaults to whichever API key is present in env.",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Model id. Vendor-specific default if omitted.",
    )
    ap.add_argument(
        "--harness",
        type=Path,
        default=None,
        help=(
            "Path to the harness package directory (the one with server.py). "
            "Defaults to this module's parent — i.e. the installed package."
        ),
    )
    ap.add_argument(
        "--max-cycles",
        type=int,
        default=DEFAULT_MAX_CYCLES,
        help=f"Cycle-loop cap. Default {DEFAULT_MAX_CYCLES}.",
    )
    args = ap.parse_args(argv)

    try:
        vendor = build_vendor(args.vendor, model=args.model)
    except (RuntimeError, ValueError) as exc:
        print(f"agent-butler: {exc}", file=sys.stderr)
        return 2

    harness_dir = args.harness or _default_harness_dir()
    if not (harness_dir / "server.py").is_file():
        print(
            f"agent-butler: server.py not found under {harness_dir} "
            f"(set --harness or HARNESS_ROOT)",
            file=sys.stderr,
        )
        return 2

    try:
        answer = asyncio.run(
            run_butler(args.task, vendor, harness_dir, max_cycles=args.max_cycles)
        )
    except KeyboardInterrupt:
        print("\n[butler] cancelled.", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"agent-butler: {exc}", file=sys.stderr)
        return 1

    print(answer)
    return 0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _default_harness_dir() -> Path:
    """Resolve the harness server dir.

    Preference order:
      1. $HARNESS_ROOT (matches the extension's convention).
      2. The directory containing this very module — works for the
         installed-wheel case (server.py ships alongside butler/).
    """
    env = os.environ.get("HARNESS_ROOT")
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


async def _dispatch(
    session: ClientSession,
    tool_uses: list[dict[str, Any]],
    log: Any,
) -> list[dict[str, Any]]:
    """Call each tool and collect Anthropic-shaped tool_result blocks."""
    results: list[dict[str, Any]] = []
    for tu in tool_uses:
        name = tu.get("name", "")
        args = tu.get("input") or {}
        print(
            f"[butler]   → {name}({_truncate(json.dumps(args), 60)})",
            file=log,
        )
        try:
            result = await session.call_tool(name, arguments=args)
            content = _first_text(result)
        except Exception as exc:  # noqa: BLE001 — surface errors to the model
            content = f"[tool error] {type(exc).__name__}: {exc}"
        results.append({
            "type": "tool_result",
            "tool_use_id": tu.get("id", ""),
            "content": content,
        })
    return results


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
    parts = [f"[butler] cycle {cycle}: stop={stop_reason}", f"tools={len(tool_uses)}"]
    if usage:
        toks = usage.get("output_tokens") or usage.get("completion_tokens")
        if toks is not None:
            parts.append(f"out_tokens={toks}")
    print(" ".join(parts), file=log)


if __name__ == "__main__":
    raise SystemExit(main())
