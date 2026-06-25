"""External MCP-server federation for the standalone agent.

musubi-tier: substrate
expires-when: never — federating other MCP servers' tools into the
  standalone host's tool surface is a permanent capability of a
  Claude-Code-shaped host, mirroring the vendor inject point. It makes
  ZERO LLM calls and imports no LLM SDK (only the `mcp` client), so it is
  driver-side glue like `agent/vendors/curl_router.py`, not a governance
  control.

What this does
--------------
The agent loop (`agent/run.py`) drives one MCP session — Musubi's own
server. This module lets the same loop *also* connect to any number of
**other** MCP servers declared in an `mcp.json` (a filesystem server, a
GitHub server, an internal tool server, …), list their tools, and splice
them into the tool catalog handed to the model. Tool calls are then routed
back to whichever server owns the tool.

The config uses the de-facto ecosystem schema — a top-level `mcpServers`
object keyed by name — so a config from Claude Desktop / Cursor / VS Code /
`.mcp.json` pastes in verbatim.

Namespacing
-----------
Musubi's own tools keep their `musubi_*` names unchanged (lots of code,
tests, and the extension cite them verbatim). Every *external* tool is
exposed to the model as ``<server>__<tool>`` so two servers can offer a
`search` tool without colliding, and so a glance at the name tells the
model (and the logs) which server a call lands on. `route()` strips the
prefix before dispatching to the owning session.

Governance boundary (read this)
-------------------------------
External tools are **not** governed by Musubi. The policy engine, the
evaluator firewall, and the audit DB gate *Musubi's* tools inside
`server.py`; a call to a federated `filesystem__read_file` goes straight
to that server. Federation is a driver-side convenience, not a substrate
control — keep that line clear when reasoning about what Musubi enforces.
Sub-agents never receive external tools (the sub-agent runner filters to
mapped `musubi_*` tools), so federation is a top-level-agent capability.

Failure policy
--------------
Connecting to an external server is **fail-open**: a server that is
misconfigured, missing, or slow is logged and skipped, never fatal — its
tools are additive, so one bad entry must not sink the whole agent. This
is the opposite of Hard Invariant #5 (the *policy engine* fails closed);
that invariant governs Musubi's own `(pipeline, agent)` decisions, not the
discovery of optional third-party tools.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

NAMESPACE_SEP = "__"
_DEFAULT_INIT_TIMEOUT_S = 30

# ${VAR} or ${env:VAR} — the convention every MCP client uses to keep a
# secret out of the JSON file (the value is read from the process env).
_ENV_REF = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}")


# ── Config ──────────────────────────────────────────────────────────────────


@dataclass
class McpServerSpec:
    """One external MCP server declared in the `mcpServers` map.

    Exactly one transport is used: `command` (stdio, the common local case)
    or `url` (streamable HTTP). `env`/`headers`/`url`/`args` values are
    already `${VAR}`-interpolated from the environment at load time, so a
    secret never lives literally in the file.
    """

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    timeout_s: int = _DEFAULT_INIT_TIMEOUT_S

    def resolved_env(self) -> dict[str, str] | None:
        """The child's env overrides, or None to inherit the safe default."""
        return dict(self.env) or None


def mcp_config_candidates(
    explicit: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """The ordered list of paths `find_mcp_config_path` checks (first wins).

    Exposed so the agent log can show *exactly* where it looked when no
    config is found — the ambiguity is otherwise invisible from the output.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("MUSUBI_MCP_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / ".mcp.json")
    candidates.append(Path.cwd() / ".musubi" / "mcp.json")
    candidates.append(Path.home() / ".musubi" / "mcp.json")
    return candidates


def find_mcp_config_path(
    explicit: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Resolve the mcp.json location — the first existing candidate.

    Order: explicit arg → $MUSUBI_MCP_CONFIG → ./.mcp.json (Claude Code's
    own project convention) → ./.musubi/mcp.json → ~/.musubi/mcp.json.
    Returns None if none exists (the common case — the feature is opt-in).
    """
    for c in mcp_config_candidates(explicit):
        if c.is_file():
            return c
    return None


def load_mcp_servers(
    path: str | os.PathLike[str] | None = None,
) -> list[McpServerSpec]:
    """Parse an `mcp.json` into a list of enabled `McpServerSpec`.

    Uses the de-facto ecosystem schema — a top-level ``mcpServers`` object
    keyed by server name — so a Claude Desktop / Cursor / VS Code config
    pastes in unchanged. Returns ``[]`` when no config file exists (feature
    off). Per-server ``"disabled": true`` is honoured. Raises ValueError for
    a malformed file or an unresolved ``${VAR}`` reference.

    Format::

        {
          "mcpServers": {
            "filesystem": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "/work"]
            },
            "github": {
              "command": "docker",
              "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
              "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" }
            },
            "remote": {
              "url": "https://example.com/mcp",
              "headers": { "Authorization": "Bearer ${MCP_TOKEN}" }
            }
          }
        }
    """
    cfg_path = find_mcp_config_path(path)
    if cfg_path is None:
        return []
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{cfg_path}: cannot parse mcp.json: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{cfg_path}: top level must be a JSON object")
    servers = raw.get("mcpServers")
    if servers is None:
        return []
    if not isinstance(servers, dict):
        raise ValueError(f"{cfg_path}: `mcpServers` must be an object of named servers")

    specs: list[McpServerSpec] = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{cfg_path}: server '{name}' must be an object")
        spec = _spec_from_entry(name, entry, cfg_path)
        if not spec.disabled:
            specs.append(spec)
    return specs


def _spec_from_entry(
    name: str, entry: dict[str, Any], cfg_path: Path
) -> McpServerSpec:
    command = entry.get("command")
    url = entry.get("url")
    if bool(command) == bool(url):
        raise ValueError(
            f"{cfg_path}: server '{name}' needs exactly one of `command` "
            f"(stdio) or `url` (http), not both/neither"
        )
    where = f"{cfg_path} server '{name}'"
    return McpServerSpec(
        name=name,
        command=command,
        args=[_interp(str(a), where) for a in entry.get("args", [])],
        env={k: _interp(str(v), where) for k, v in entry.get("env", {}).items()},
        cwd=entry.get("cwd"),
        url=_interp(url, where) if url else None,
        headers={
            k: _interp(str(v), where) for k, v in entry.get("headers", {}).items()
        },
        disabled=bool(entry.get("disabled", False)),
        timeout_s=int(entry.get("timeout_s", _DEFAULT_INIT_TIMEOUT_S)),
    )


def _interp(value: str, where: str) -> str:
    """Expand `${VAR}` / `${env:VAR}` from the environment.

    Fail-closed on a missing variable: an unresolved reference raises rather
    than silently sending empty credentials. `$$` is a literal `$`.
    """

    def repl(m: re.Match[str]) -> str:
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            raise ValueError(
                f"{where}: environment variable ${{{var}}} is referenced "
                f"but not set"
            )
        return val

    return _ENV_REF.sub(repl, value.replace("$$", "\x00")).replace("\x00", "$")


# ── Gateway ─────────────────────────────────────────────────────────────────


class McpGateway:
    """Aggregates Musubi's tools + every external server's tools, and routes.

    Build it once per agent run: register Musubi's session as the local
    catalog, then `connect_external` for the declared servers. `tools()`
    is the merged catalog handed to the model; `route(name)` maps a tool
    name the model called back to ``(owning_session, original_tool_name)``.
    """

    def __init__(self) -> None:
        self._tools: list[dict[str, Any]] = []
        # public tool name → (session, original name on that server)
        self._routes: dict[str, tuple[Any, str]] = {}

    # -- registration ---------------------------------------------------------

    def register_local(
        self, session: Any, tools: list[dict[str, Any]]
    ) -> None:
        """Register Musubi's own tools, unprefixed (names cited verbatim)."""
        for tool in tools:
            name = tool["name"]
            self._routes[name] = (session, name)
            self._tools.append(tool)

    def register_remote(
        self, server: str, session: Any, tools: list[dict[str, Any]]
    ) -> list[str]:
        """Register one external server's tools under the `<server>__` prefix.

        Returns the public names that were added. A name that would collide
        with an already-registered tool is skipped (and omitted from the
        return) so a second server can never shadow Musubi or a sibling.
        """
        added: list[str] = []
        for tool in tools:
            public = namespaced(server, tool["name"])
            if public in self._routes:
                continue  # collision guard — first registration wins
            self._routes[public] = (session, tool["name"])
            self._tools.append({**tool, "name": public})
            added.append(public)
        return added

    # -- connection -----------------------------------------------------------

    async def connect_external(
        self,
        stack: AsyncExitStack,
        specs: list[McpServerSpec],
        log: Any,
        *,
        opener: Any = None,
    ) -> None:
        """Open + register every spec, fail-open per server.

        Sessions are entered on the caller's `AsyncExitStack` so they live
        exactly as long as the agent run. `opener` (test seam) is an async
        callable ``(stack, spec) -> ClientSession``; defaults to the real
        stdio/http opener.
        """
        open_session = opener or _open_session
        for spec in specs:
            try:
                session = await open_session(stack, spec)
                await session.initialize()
                listed = (await session.list_tools()).tools
                tools = [mcp_tool_to_schema(t) for t in listed]
                added = self.register_remote(spec.name, session, tools)
                _log(
                    log,
                    f"[agent] +mcp '{spec.name}': {len(added)} tool(s)"
                    + (
                        f" ({len(tools) - len(added)} skipped: name clash)"
                        if len(added) != len(tools)
                        else ""
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — additive; never fatal
                _log(
                    log,
                    f"[agent] !mcp '{spec.name}' skipped: "
                    f"{type(exc).__name__}: {exc}",
                )

    # -- query ----------------------------------------------------------------

    def tools(self) -> list[dict[str, Any]]:
        """The merged tool catalog (Musubi first, then external)."""
        return list(self._tools)

    def route(self, name: str) -> tuple[Any, str] | None:
        """Map a tool name the model called to (session, original name)."""
        return self._routes.get(name)


# ── helpers ─────────────────────────────────────────────────────────────────


def namespaced(server: str, tool: str) -> str:
    return f"{server}{NAMESPACE_SEP}{tool}"


def mcp_tool_to_schema(tool: Any) -> dict[str, Any]:
    """MCP Tool object → the Anthropic-shaped dict the loop hands to vendors."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


async def _open_session(stack: AsyncExitStack, spec: McpServerSpec) -> ClientSession:
    """Open a ClientSession for `spec` on `stack` (stdio or streamable HTTP)."""
    if spec.url:
        try:
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # older mcp without the HTTP client
            raise RuntimeError(
                "http MCP transport needs a newer `mcp` (streamable_http "
                "client); use a stdio `command` server instead"
            ) from exc
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(spec.url, headers=spec.headers or None)
        )
    else:
        params = StdioServerParameters(
            command=spec.command or "",
            args=spec.args,
            env=spec.resolved_env(),
            cwd=spec.cwd,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
    return await stack.enter_async_context(ClientSession(read, write))


def _log(log: Any, message: str) -> None:
    try:
        print(message, file=log)
    except Exception:  # noqa: BLE001 — logging must never break a run
        pass
