"""Tests for external MCP-server federation (`agent/mcp_gateway.py`).

musubi-tier: substrate test — pins the federation contract: config
parsing, tool namespacing, collision handling, routing, and the
fail-open connection policy.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.mcp_gateway import (
    McpGateway,
    McpServerSpec,
    find_mcp_config_path,
    load_mcp_servers,
    mcp_config_candidates,
    mcp_tool_to_schema,
    namespaced,
)

# ── Fakes ────────────────────────────────────────────────────────────────────


def _tool(name: str, desc: str = "", schema: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(name=name, description=desc, inputSchema=schema)


class FakeSession:
    """Minimal stand-in for an MCP ClientSession."""

    def __init__(self, tools: list[Any], *, fail_init: bool = False) -> None:
        self._tools = tools
        self._fail_init = fail_init
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        if self._fail_init:
            raise RuntimeError("boom")

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name: str, *, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text=f"ran {name}")])


# ── Config parsing (standard `mcpServers` JSON schema) ───────────────────────


def _write(tmp_path: Path, obj: dict[str, Any]) -> Path:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps(obj), encoding="utf-8")
    return cfg


def test_load_servers_parses_stdio_and_http(tmp_path: Path) -> None:
    cfg = _write(tmp_path, {
        "mcpServers": {
            "fs": {
                "command": "npx",
                "args": ["-y", "server-filesystem", "/work"],
                "cwd": "/work",
            },
            "remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer x"},
            },
        }
    })
    specs = {s.name: s for s in load_mcp_servers(cfg)}
    assert set(specs) == {"fs", "remote"}
    assert specs["fs"].command == "npx"
    assert specs["fs"].args == ["-y", "server-filesystem", "/work"]
    assert specs["fs"].cwd == "/work"
    assert specs["remote"].url == "https://example.com/mcp"
    assert specs["remote"].headers == {"Authorization": "Bearer x"}


def test_no_mcp_servers_key_returns_empty(tmp_path: Path) -> None:
    assert load_mcp_servers(_write(tmp_path, {"other": 1})) == []


def test_disabled_server_is_dropped(tmp_path: Path) -> None:
    cfg = _write(tmp_path, {
        "mcpServers": {
            "fs": {"command": "npx", "disabled": True},
            "keep": {"command": "uvx"},
        }
    })
    assert [s.name for s in load_mcp_servers(cfg)] == ["keep"]


def test_command_and_url_are_mutually_exclusive(tmp_path: Path) -> None:
    cfg = _write(tmp_path, {
        "mcpServers": {"bad": {"command": "npx", "url": "https://example.com/mcp"}}
    })
    with pytest.raises(ValueError, match="exactly one"):
        load_mcp_servers(cfg)


def test_neither_command_nor_url_errors(tmp_path: Path) -> None:
    cfg = _write(tmp_path, {"mcpServers": {"bad": {"args": ["x"]}}})
    with pytest.raises(ValueError, match="exactly one"):
        load_mcp_servers(cfg)


def test_malformed_json_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot parse"):
        load_mcp_servers(cfg)


def test_no_config_file_returns_empty() -> None:
    assert load_mcp_servers("/nonexistent/mcp.json") == []


def test_env_var_interpolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "sekret")
    monkeypatch.setenv("MCP_TOKEN", "bearer-123")
    cfg = _write(tmp_path, {
        "mcpServers": {
            "github": {
                "command": "docker",
                "args": ["run", "--name=${GH_TOKEN}-box"],
                "env": {"GITHUB_TOKEN": "${GH_TOKEN}", "PLAIN": "v"},
            },
            "remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${env:MCP_TOKEN}"},
            },
        }
    })
    specs = {s.name: s for s in load_mcp_servers(cfg)}
    assert specs["github"].env == {"GITHUB_TOKEN": "sekret", "PLAIN": "v"}
    assert specs["github"].args == ["run", "--name=sekret-box"]  # interpolated
    assert specs["remote"].headers == {"Authorization": "Bearer bearer-123"}


def test_unset_env_var_is_a_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    cfg = _write(tmp_path, {
        "mcpServers": {
            "x": {"command": "y", "env": {"K": "${MISSING_SECRET}"}}
        }
    })
    # Fail-closed: never silently send an empty credential.
    with pytest.raises(ValueError, match="MISSING_SECRET"):
        load_mcp_servers(cfg)


def test_resolved_env_none_when_empty() -> None:
    assert McpServerSpec(name="s", command="x").resolved_env() is None


def test_find_config_prefers_explicit(tmp_path: Path) -> None:
    cfg = _write(tmp_path, {"mcpServers": {}})
    assert find_mcp_config_path(cfg) == cfg


def test_find_config_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _write(tmp_path, {"mcpServers": {}})
    monkeypatch.setenv("MUSUBI_MCP_CONFIG", str(cfg))
    assert find_mcp_config_path() == cfg


def test_candidate_order_is_repo_root_before_musubi_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUSUBI_MCP_CONFIG", raising=False)
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path("/proj")))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/u")))
    # Explicit first, then env (absent), then ./.mcp.json before ./.musubi/.
    cands = mcp_config_candidates("/explicit.json")
    assert cands == [
        Path("/explicit.json"),
        Path("/proj/.mcp.json"),
        Path("/proj/.musubi/mcp.json"),
        Path("/home/u/.musubi/mcp.json"),
    ]


# ── Namespacing + routing ────────────────────────────────────────────────────


def test_local_tools_keep_their_names() -> None:
    gw = McpGateway()
    sess = FakeSession([])
    gw.register_local(sess, [mcp_tool_to_schema(_tool("musubi_read_file"))])
    assert [t["name"] for t in gw.tools()] == ["musubi_read_file"]
    assert gw.route("musubi_read_file") == (sess, "musubi_read_file")


def test_remote_tools_are_namespaced() -> None:
    gw = McpGateway()
    sess = FakeSession([])
    added = gw.register_remote(
        "fs", sess, [mcp_tool_to_schema(_tool("read_file"))]
    )
    assert added == ["fs__read_file"]
    assert namespaced("fs", "read_file") == "fs__read_file"
    # The model sees the namespaced name; routing strips it back.
    assert gw.route("fs__read_file") == (sess, "read_file")
    assert [t["name"] for t in gw.tools()] == ["fs__read_file"]


def test_two_servers_with_same_tool_do_not_collide() -> None:
    gw = McpGateway()
    a, b = FakeSession([]), FakeSession([])
    gw.register_remote("a", a, [mcp_tool_to_schema(_tool("search"))])
    gw.register_remote("b", b, [mcp_tool_to_schema(_tool("search"))])
    assert gw.route("a__search") == (a, "search")
    assert gw.route("b__search") == (b, "search")


def test_duplicate_public_name_is_skipped() -> None:
    gw = McpGateway()
    a, b = FakeSession([]), FakeSession([])
    gw.register_remote("dup", a, [mcp_tool_to_schema(_tool("x"))])
    added = gw.register_remote("dup", b, [mcp_tool_to_schema(_tool("x"))])
    assert added == []  # second registration of dup__x is dropped
    assert gw.route("dup__x") == (a, "x")  # first wins


def test_route_unknown_tool_returns_none() -> None:
    assert McpGateway().route("nope") is None


def test_schema_falls_back_for_missing_fields() -> None:
    schema = mcp_tool_to_schema(_tool("t", desc="", schema=None))
    assert schema["description"] == ""
    assert schema["input_schema"] == {"type": "object", "properties": {}}


# ── connect_external: fail-open ──────────────────────────────────────────────


def _connect(gw: McpGateway, specs: list[McpServerSpec], opener: Any) -> list[str]:
    log: list[str] = []

    async def _go() -> None:
        async with AsyncExitStack() as stack:
            await gw.connect_external(
                stack, specs, _LogList(log), opener=opener
            )

    asyncio.run(_go())
    return log


class _LogList:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, s: str) -> int:  # file-like for print(..., file=)
        if s.strip():
            self._sink.append(s.strip())
        return len(s)


def test_connect_external_registers_good_skips_bad() -> None:
    gw = McpGateway()
    good = FakeSession([_tool("ls")])
    bad = FakeSession([], fail_init=True)

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        return good if spec.name == "good" else bad

    specs = [
        McpServerSpec(name="good", command="x"),
        McpServerSpec(name="bad", command="y"),
    ]
    log = _connect(gw, specs, opener)

    # Good server's tool is federated; bad server is skipped, not fatal.
    assert gw.route("good__ls") == (good, "ls")
    assert gw.route("bad__ls") is None
    assert any("good" in line and "1 tool" in line for line in log)
    assert any("bad" in line and "skipped" in line for line in log)


def test_connect_external_opener_failure_is_isolated() -> None:
    gw = McpGateway()

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        raise RuntimeError("cannot launch")

    log = _connect(gw, [McpServerSpec(name="x", command="z")], opener)
    assert gw.tools() == []
    assert any("skipped" in line for line in log)
