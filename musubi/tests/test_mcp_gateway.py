"""Tests for external MCP-server federation (`agent/mcp_gateway.py`).

musubi-tier: substrate test — pins the federation contract: config
parsing, tool namespacing, collision handling, routing, and the
fail-open connection policy.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.mcp_gateway import (
    McpGateway,
    McpServerSpec,
    _is_spurious_cancel,
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


def test_no_config_file_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUSUBI_MCP_CONFIG", raising=False)
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert load_mcp_servers(tmp_path / "nonexistent" / "mcp.json") == []


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


def test_tool_catalog_order_is_stable_independent_of_registration_order() -> None:
    first = McpGateway()
    second = McpGateway()
    sess = FakeSession([])

    first.register_remote("zeta", sess, [mcp_tool_to_schema(_tool("search"))])
    first.register_local(sess, [mcp_tool_to_schema(_tool("musubi_read_file"))])
    first.register_remote("alpha", sess, [mcp_tool_to_schema(_tool("grep"))])

    second.register_remote("alpha", sess, [mcp_tool_to_schema(_tool("grep"))])
    second.register_remote("zeta", sess, [mcp_tool_to_schema(_tool("search"))])
    second.register_local(sess, [mcp_tool_to_schema(_tool("musubi_read_file"))])

    assert [t["name"] for t in first.tools()] == [
        "alpha__grep",
        "musubi_read_file",
        "zeta__search",
    ]
    assert first.tools() == second.tools()


def test_tool_catalog_schema_is_canonicalized_without_rerouting() -> None:
    gw = McpGateway()
    sess = FakeSession([])
    schema = {
        "type": "object",
        "required": ["b", "a"],
        "properties": {
            "z": {"type": "string", "description": "last"},
            "a": {"description": "first", "type": "string"},
        },
    }
    gw.register_remote("fs", sess, [mcp_tool_to_schema(_tool("read", schema=schema))])

    tool = gw.tools()[0]

    assert list(tool["input_schema"].keys()) == ["properties", "required", "type"]
    assert list(tool["input_schema"]["properties"].keys()) == ["a", "z"]
    assert tool["input_schema"]["required"] == ["b", "a"]
    assert gw.route("fs__read") == (sess, "read")


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


def test_local_surface_filter_does_not_filter_external_tools() -> None:
    from tool_surface import filter_tool_catalog

    gw = McpGateway()
    local = FakeSession([])
    remote = FakeSession([])
    local_tools = [
        mcp_tool_to_schema(_tool("musubi_read_file")),
        mcp_tool_to_schema(_tool("musubi_write_stage")),
    ]
    gw.register_local(local, filter_tool_catalog(local_tools, "agent"))
    gw.register_remote("github", remote, [mcp_tool_to_schema(_tool("search_issues"))])

    names = [tool["name"] for tool in gw.tools()]

    assert "musubi_read_file" in names
    assert "musubi_write_stage" not in names
    assert "github__search_issues" in names
    assert gw.route("github__search_issues") == (remote, "search_issues")


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


def test_connect_external_absorbs_teardown_exception_group() -> None:
    """Regression: an unreachable streamable-HTTP server connects lazily, then
    re-raises its ConnectError wrapped in a BaseExceptionGroup at *teardown*.
    That must be absorbed by the per-server stack — never propagate to the run
    and crash the agent (the Windows http-server traceback)."""
    gw = McpGateway()
    good = FakeSession([_tool("ls")])

    @asynccontextmanager
    async def exploding_transport():
        yield
        raise BaseExceptionGroup(
            "teardown", [ConnectionError("All connection attempts failed")]
        )

    async def opener(server_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        # Enter a context whose *teardown* explodes, mimicking anyio's task
        # group holding a late ConnectError. The session itself initialises OK.
        await server_stack.enter_async_context(exploding_transport())
        return good

    # _connect closes the run stack on exit; this must not raise.
    log = _connect(gw, [McpServerSpec(name="remote", command="x")], opener)
    assert gw.route("remote__ls") == (good, "ls")  # connected + registered
    assert any("remote" in line and "1 tool" in line for line in log)


def test_connect_external_skips_server_failing_at_teardown_only() -> None:
    """A server whose transport explodes at teardown but never returned a
    usable session is skipped without crashing the run."""
    gw = McpGateway()

    @asynccontextmanager
    async def exploding_transport():
        yield
        raise BaseExceptionGroup("teardown", [ConnectionError("refused")])

    async def opener(server_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        await server_stack.enter_async_context(exploding_transport())
        raise RuntimeError("connect failed")

    log = _connect(gw, [McpServerSpec(name="remote", command="x")], opener)
    assert gw.tools() == []
    assert any("skipped" in line for line in log)


def test_connect_external_skips_spurious_anyio_scope_cancel() -> None:
    """Regression: an unreachable streamable-HTTP server's anyio cancel scope
    leaks a bare `CancelledError` ("Cancelled via cancel scope …") when it can't
    connect. `_is_fatal` treats every `CancelledError` as fatal, so before this
    guard that one dead optional server re-raised out of `connect_external` and
    aborted the whole run — surfacing to the user as "agent exceeded N cycles"
    because the model loop never started. Our task is not actually cancelled, so
    it must be skipped like any other unreachable server."""
    gw = McpGateway()
    good = FakeSession([_tool("ls")])

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        if spec.name == "dead":
            raise asyncio.CancelledError("Cancelled via cancel scope 0xdeadbeef")
        return good

    specs = [
        McpServerSpec(name="dead", command="x"),
        McpServerSpec(name="good", command="y"),
    ]
    # Must NOT raise, and the reachable server after the dead one still registers.
    log = _connect(gw, specs, opener)
    assert gw.route("good__ls") == (good, "ls")
    assert any("dead" in line and "skipped" in line for line in log)


def test_connect_external_still_propagates_keyboard_interrupt() -> None:
    """A real interrupt during connect is never spurious — it must abort."""
    gw = McpGateway()

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _connect(gw, [McpServerSpec(name="x", command="z")], opener)


def test_is_spurious_cancel_discriminates() -> None:
    async def _check() -> None:
        # A bare scope cancel with our task uncancelled is spurious.
        assert _is_spurious_cancel(asyncio.CancelledError("via cancel scope"))
        # An exception group of only cancels is spurious...
        assert _is_spurious_cancel(
            BaseExceptionGroup("g", [asyncio.CancelledError("scope")])
        )
        # ...but a group that also carries a real interrupt is not.
        assert not _is_spurious_cancel(
            BaseExceptionGroup("g", [KeyboardInterrupt(), asyncio.CancelledError()])
        )
        # Non-cancel errors are handled by the ordinary fail-open path.
        assert not _is_spurious_cancel(RuntimeError("boom"))
        assert not _is_spurious_cancel(KeyboardInterrupt())

    asyncio.run(_check())


def test_is_spurious_cancel_false_when_task_genuinely_cancelling() -> None:
    """When our task is actually being cancelled, a `CancelledError` is real and
    must propagate — `cancelling()` is non-zero."""
    seen: list[bool] = []

    async def _inner() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            seen.append(_is_spurious_cancel(exc))
            raise

    async def _drive() -> None:
        task = asyncio.ensure_future(_inner())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())
    assert seen == [False]


def test_skip_log_reaches_the_leaf_of_a_NESTED_exception_group() -> None:
    # `_describe_exc` used to unwrap exactly one level, while its twin in
    # run.py (`_clean_error`) unwrapped repeatedly — two copies of one idea,
    # and only one of them got fixed. anyio nests groups routinely (a task
    # group inside a task group), and on a two-level nest the single unwrap
    # printed "ExceptionGroup: inner (1 sub-exception)", swallowing the cause
    # of the skip. That log line is the ONLY thing an operator gets when an
    # optional server is dropped, so it has to carry the leaf.
    from agent.mcp_gateway import _describe_exc

    leaf = ConnectionRefusedError("port 8080 closed")
    one = BaseExceptionGroup("inner", [leaf])
    two = BaseExceptionGroup("outer", [one])
    three = BaseExceptionGroup("outermost", [two])

    for depth, exc in ((0, leaf), (1, one), (2, two), (3, three)):
        assert _describe_exc(exc) == "ConnectionRefusedError: port 8080 closed", depth


def test_one_owner_for_the_anthropic_tool_schema() -> None:
    # `run.py` carried a byte-identical copy of this under another name. Two
    # copies of the vendor tool shape drift the moment one provider changes.
    from agent import run as run_mod
    from agent.mcp_gateway import mcp_tool_to_schema

    assert not hasattr(run_mod, "_mcp_to_anthropic_tool")
    tool = SimpleNamespace(name="x", description=None, inputSchema=None)
    assert mcp_tool_to_schema(tool) == {
        "name": "x",
        "description": "",
        "input_schema": {"type": "object", "properties": {}},
    }


def test_a_skip_line_names_the_transport_and_the_elapsed_time() -> None:
    """The traced session logged `!mcp 'local' skipped: CancelledError`.

    That named neither what was tried nor how long it waited — and a stdio
    server whose command is missing needs an opposite first move from an HTTP
    server whose host is unreachable. Both used to produce the same line.
    """
    gw = McpGateway()

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        raise RuntimeError("boom")

    stdio_log = _connect(
        gw, [McpServerSpec(name="local", command="npx", args=["-y", "srv"])], opener,
    )
    line = next(entry for entry in stdio_log if "skipped" in entry)
    assert "via stdio npx -y srv" in line
    assert re.search(r"skipped after \d+ms", line)

    http_log = _connect(
        gw, [McpServerSpec(name="remote", url="https://mcp.example/api")], opener,
    )
    line = next(entry for entry in http_log if "skipped" in entry)
    assert "via http https://mcp.example/api" in line


def test_a_skip_line_never_prints_a_secret() -> None:
    # `headers` and `env` are where the ${VAR}-interpolated tokens land, so the
    # line reports the transport target and nothing else from the spec.
    gw = McpGateway()

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        raise RuntimeError("boom")

    log = _connect(gw, [McpServerSpec(
        name="remote",
        url="https://mcp.example/api",
        headers={"Authorization": "Bearer super-secret-token"},
        env={"API_KEY": "another-secret"},
    )], opener)

    joined = "\n".join(log)
    assert "super-secret-token" not in joined
    assert "another-secret" not in joined


def test_a_timeout_says_so_rather_than_looking_like_an_instant_failure() -> None:
    gw = McpGateway()

    async def opener(_stack: AsyncExitStack, spec: McpServerSpec) -> Any:
        raise RuntimeError("boom")

    # timeout_s=0 makes any elapsed time reach the ceiling, which is the
    # condition the line reports — a real 30s wait and an instant refusal read
    # identically without it.
    log = _connect(gw, [McpServerSpec(name="slow", command="x", timeout_s=0)], opener)

    assert any("(timeout 0s)" in entry for entry in log)


def test_transport_is_derived_not_guessed() -> None:
    assert McpServerSpec(name="a", command="x").transport == "stdio"
    assert McpServerSpec(name="b", url="http://h").transport == "http"
    assert McpServerSpec(name="c").transport == "none"
    assert "no transport configured" in McpServerSpec(name="c").target
