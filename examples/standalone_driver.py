"""Reference standalone driver for the harness MCP server.

harness-tier: substrate (example)
expires-when: never — example code showing how to drive the substrate
  without the VS Code extension is durable regardless of pipeline shape.

This script shows how to drive the harness against ANY LLM that supports
tool use (Anthropic Claude, OpenAI GPT-4/5, etc.) without going through
the Copilot VS Code extension. It demonstrates:

  1. Spawning `copilot-harness serve` as an MCP stdio subprocess.
  2. Listing the harness_* tool catalog.
  3. Calling individual tools (smoke test: harness_new_session +
     harness_get_memory_entry against the project profile).
  4. The agentic loop shape: LLM call → dispatch tool calls → feed
     results back → repeat until the LLM stops asking for tools.

The actual LLM call is left as a `call_llm()` stub the user fills in
for their preferred vendor. The MCP tool spec → vendor tool spec
conversion is sketched in `_mcp_to_anthropic_tool()`.

Run:
    python -m pip install -e copilot-harness/
    python examples/standalone_driver.py --smoke
    python examples/standalone_driver.py --turn "list the project profile"

See `docs/standalone-usage.md` for the full guide.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# The harness MCP SDK is already a hard dep of copilot-harness/.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ── MCP plumbing ────────────────────────────────────────────────────────────


def _server_params(harness_dir: Path) -> StdioServerParameters:
    """Spawn the server via `python <harness>/server.py`.

    The console script `copilot-harness serve` (from `pip install -e
    copilot-harness/`) is equivalent — both call `server.serve()`. We
    use the direct path so this driver works without an install step.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=[str(harness_dir / "server.py")],
        env=None,
    )


async def _smoke(harness_dir: Path) -> None:
    """List tools and call a couple to confirm the substrate is live."""
    async with stdio_client(_server_params(harness_dir)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"[harness] {len(tools.tools)} tools available:")
            for t in tools.tools:
                print(f"  - {t.name}")
            print()

            # Smoke: open a session. `request` is the user-visible task;
            # `pipeline_name` defaults to "feature-dev" in the server.
            new_sess = await session.call_tool(
                "harness_new_session",
                arguments={"request": "smoke test from standalone driver"},
            )
            print("[harness_new_session] →", _first_text(new_sess))

            # Smoke: read the project profile written by SessionStart
            # (MVP item 4 / Track D.1). If it isn't there yet, the tool
            # returns a not-found shape.
            profile = await session.call_tool(
                "harness_get_memory_entry",
                arguments={"name": "project-profile"},
            )
            print("[harness_get_memory_entry project-profile] →",
                  _first_text(profile)[:200], "...")


# ── Agentic loop sketch ─────────────────────────────────────────────────────


def _mcp_to_anthropic_tool(tool: Any) -> dict[str, Any]:
    """Anthropic Messages API tool spec.

    OpenAI's `tools` field uses a sibling shape with `function: {...}`
    wrapping. Adapt as needed for your vendor.
    """
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


async def call_llm(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stub — fill in for your LLM vendor.

    Expected return shape (Anthropic-ish):
        {
            "stop_reason": "tool_use" | "end_turn",
            "content": [
                {"type": "text", "text": "..."},
                {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
            ],
        }

    Anthropic SDK example (uncomment + add `anthropic` to your env):

        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            tools=tools,
            messages=messages,
        )
        return {
            "stop_reason": msg.stop_reason,
            "content": [block.model_dump() for block in msg.content],
        }
    """
    raise NotImplementedError(
        "Fill in `call_llm` with your vendor's tool-use API. See docstring."
    )


async def _turn(harness_dir: Path, user_request: str, max_cycles: int = 8) -> None:
    """One agentic turn: drive the LLM ↔ harness loop until terminal."""
    async with stdio_client(_server_params(harness_dir)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_list = (await session.list_tools()).tools
            tools_for_llm = [_mcp_to_anthropic_tool(t) for t in tool_list]

            messages: list[dict[str, Any]] = [
                {"role": "user", "content": user_request},
            ]

            for cycle in range(max_cycles):
                resp = await call_llm(messages, tools_for_llm)
                messages.append({"role": "assistant", "content": resp["content"]})

                tool_uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
                if not tool_uses:
                    print(f"[turn] cycle {cycle}: end_turn — done.")
                    return

                tool_results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    name = tu["name"]
                    args = tu.get("input", {})
                    print(f"[turn] cycle {cycle}: → {name}({json.dumps(args)[:80]})")
                    result = await session.call_tool(name, arguments=args)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": _first_text(result),
                    })
                messages.append({"role": "user", "content": tool_results})


# ── Small helpers ──────────────────────────────────────────────────────────


def _first_text(call_result: Any) -> str:
    """Pull the first text chunk out of an MCP CallToolResult."""
    for c in getattr(call_result, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            return text
    return ""


# ── Entry point ────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harness", type=Path,
                    default=_repo_root() / "copilot-harness",
                    help="Path to the harness package directory.")
    ap.add_argument("--smoke", action="store_true",
                    help="List tools + call a couple as a smoke test.")
    ap.add_argument("--turn", type=str, default=None,
                    help="Run one agentic turn with the given request.")
    args = ap.parse_args()

    if not args.smoke and args.turn is None:
        ap.print_help()
        return 2

    if args.smoke:
        asyncio.run(_smoke(args.harness))
        return 0
    assert args.turn is not None
    asyncio.run(_turn(args.harness, args.turn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
