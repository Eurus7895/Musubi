# MCP Tool Surface Trimming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce model-visible MCP tool overload while preserving Musubi's full substrate API for drivers, GUI, VS Code, and operator/debug workflows.

**Architecture:** Implement tool-surface profiles as deterministic allowlists over already-registered `musubi_*` tools. First trim the internal standalone `agent` driver catalog from 61 local tools to an agent-focused default of 20 tools. Then add opt-in external MCP server surfaces with `musubi serve --surface agent|operator|pipeline|full`, keeping external default as `full` for backward compatibility.

**Tech Stack:** Python stdlib, existing `mcp.server.fastmcp.FastMCP`, existing `musubi/agent/mcp_gateway.py`, existing `musubi/agent/run.py`, existing `musubi/cli.py`, pytest.

## Global Constraints

- Keep Musubi deterministic and zero-LLM at the substrate boundary.
- Do not remove any MCP tool implementation from `musubi/server.py`.
- Do not weaken policy enforcement in `musubi/agent/boundary.py`; hidden tools remain governed if surfaced elsewhere.
- Internal standalone `agent` default surface is `agent`.
- External `musubi serve` default surface remains `full` in this rollout to avoid breaking existing MCP clients.
- Sub-agent tool filtering through `agent/subagent.py::select_child_tools` must remain unchanged.
- External federated MCP tools remain additive and are not filtered by Musubi's local surface profiles.
- File content and docs copy must remain English.
- Do not use a worktree.

---

## Context

Current local Musubi MCP server exposes 61 `musubi_*` tools from `musubi/server.py`. The standalone `agent` currently calls `session.list_tools()`, registers the entire local catalog through `McpGateway.register_local(...)`, and sends the full catalog to the model. The policy boundary still denies driver-only or disallowed calls, but hidden-by-policy tools are still visible to the model, which costs tokens and worsens tool choice.

The goal is not to delete substrate capability. The goal is to separate:

- **MCP server capability:** all tools available for compatible clients.
- **Model-visible surface:** the smaller tool set a given driver should show to an LLM.
- **Driver/internal calls:** direct calls made by code, not exposed to the model.

The measured sections in `server.py` are:

| Group | Count |
|---|---:|
| State/session | 7 |
| Pause/chunk/review gate | 6 |
| Pipeline correction/loading | 3 |
| Observability | 11 |
| Skill | 4 |
| Execution | 3 |
| Memory | 5 |
| Worker/subagent/pipeline | 9 |
| Conversation | 4 |
| Hook | 1 |
| Filesystem/command | 4 |
| Compression | 3 |

The internal root-agent default should keep approximately 20 tools:

```python
ROOT_AGENT_TOOL_NAMES = frozenset({
    "musubi_read_file",
    "musubi_write_file",
    "musubi_edit_file",
    "musubi_run_command",
    "musubi_run_lint",
    "musubi_run_typecheck",
    "musubi_run_tests",
    "musubi_list_skills",
    "musubi_recommend_skills",
    "musubi_get_skill",
    "musubi_get_reference",
    "musubi_compress",
    "musubi_retrieve",
    "musubi_compression_stats",
    "musubi_get_memory_context",
    "musubi_get_memory_entry",
    "musubi_query_sessions",
    "musubi_spawn_subagent",
    "musubi_spawn_pipeline",
    "musubi_list_subagents",
})
```

---

## File Structure

- Create `musubi/tool_surface.py`
  - Own the named local tool surfaces and pure filtering helpers.
  - Provide one contained helper for applying a surface to FastMCP's tool manager for external server mode.
- Modify `musubi/agent/run.py`
  - Filter the local Musubi tool schemas before registering them in `McpGateway`.
  - Add `--tool-surface agent|operator|full` for standalone agent runs.
  - Keep the internal default as `agent`.
- Modify `musubi/cli.py`
  - Parse `musubi serve --surface agent|operator|pipeline|full`.
  - Keep `musubi serve` default as `full`.
- Modify `musubi/server.py`
  - Change `serve()` to accept a surface argument and apply it before `mcp.run(...)`.
- Modify `docs/guide.md`
  - Document internal agent and external server surfaces.
- Modify `docs/roadmap.md`
  - Add or update a summary-only line pointing to this plan.
- Add tests:
  - `musubi/tests/test_tool_surface.py`
  - Extend `musubi/tests/test_mcp_gateway.py`
  - Extend `musubi/tests/test_agent_loop.py`
  - Add or extend a CLI/server test if one exists; otherwise cover `cli.main()` with monkeypatch.

---

### Task 1: Add Pure Tool Surface Definitions

**Files:**
- Create: `musubi/tool_surface.py`
- Create: `musubi/tests/test_tool_surface.py`

**Interfaces:**
- Produces:
  - `ToolSurface = Literal["agent", "operator", "pipeline", "full"]`
  - `ROOT_AGENT_TOOL_NAMES: frozenset[str]`
  - `OPERATOR_TOOL_NAMES: frozenset[str]`
  - `PIPELINE_TOOL_NAMES: frozenset[str]`
  - `tool_names_for_surface(surface: str) -> frozenset[str] | None`
  - `filter_tool_catalog(tools: list[dict[str, Any]], surface: str) -> list[dict[str, Any]]`
  - `apply_fastmcp_tool_surface(mcp: Any, surface: str) -> None`

- [ ] **Step 1: Write failing surface tests**

Create `musubi/tests/test_tool_surface.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tool_surface import (
    ROOT_AGENT_TOOL_NAMES,
    apply_fastmcp_tool_surface,
    filter_tool_catalog,
    tool_names_for_surface,
)


def _tool(name: str) -> dict:
    return {"name": name, "description": "", "input_schema": {}}


def test_agent_surface_has_expected_count_and_core_tools() -> None:
    assert len(ROOT_AGENT_TOOL_NAMES) == 20
    assert "musubi_read_file" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_run_tests" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_recommend_skills" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_retrieve" in ROOT_AGENT_TOOL_NAMES
    assert "musubi_spawn_subagent" in ROOT_AGENT_TOOL_NAMES


def test_agent_surface_excludes_driver_and_pipeline_internals() -> None:
    assert "musubi_write_stage" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_read_stage" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_get_subagent_context" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_record_agent_cycle" not in ROOT_AGENT_TOOL_NAMES
    assert "musubi_complete_subagent" not in ROOT_AGENT_TOOL_NAMES


def test_full_surface_returns_none_meaning_unfiltered() -> None:
    assert tool_names_for_surface("full") is None


def test_unknown_surface_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown tool surface"):
        tool_names_for_surface("ghost")


def test_filter_tool_catalog_preserves_order_and_filters_local_names() -> None:
    tools = [
        _tool("musubi_write_stage"),
        _tool("musubi_read_file"),
        _tool("musubi_recommend_skills"),
    ]

    assert [t["name"] for t in filter_tool_catalog(tools, "agent")] == [
        "musubi_read_file",
        "musubi_recommend_skills",
    ]


def test_filter_tool_catalog_full_returns_copy_of_all_tools() -> None:
    tools = [_tool("musubi_write_stage"), _tool("musubi_read_file")]

    out = filter_tool_catalog(tools, "full")

    assert out == tools
    assert out is not tools


def test_apply_fastmcp_tool_surface_filters_tool_manager() -> None:
    manager = SimpleNamespace(_tools={
        "musubi_write_stage": object(),
        "musubi_read_file": object(),
        "musubi_recommend_skills": object(),
    })
    mcp = SimpleNamespace(_tool_manager=manager)

    apply_fastmcp_tool_surface(mcp, "agent")

    assert set(manager._tools) == {
        "musubi_read_file",
        "musubi_recommend_skills",
    }
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_tool_surface.py -q -p no:cacheprovider
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tool_surface'`.

- [ ] **Step 3: Implement `musubi/tool_surface.py`**

Create `musubi/tool_surface.py`:

```python
"""Named MCP tool surfaces for model-visible catalogs.

musubi-tier: substrate
expires-when: never - tool-surface shaping is the LM boundary contract.
"""

from __future__ import annotations

from typing import Any, Literal

ToolSurface = Literal["agent", "operator", "pipeline", "full"]

ROOT_AGENT_TOOL_NAMES: frozenset[str] = frozenset({
    "musubi_read_file",
    "musubi_write_file",
    "musubi_edit_file",
    "musubi_run_command",
    "musubi_run_lint",
    "musubi_run_typecheck",
    "musubi_run_tests",
    "musubi_list_skills",
    "musubi_recommend_skills",
    "musubi_get_skill",
    "musubi_get_reference",
    "musubi_compress",
    "musubi_retrieve",
    "musubi_compression_stats",
    "musubi_get_memory_context",
    "musubi_get_memory_entry",
    "musubi_query_sessions",
    "musubi_spawn_subagent",
    "musubi_spawn_pipeline",
    "musubi_list_subagents",
})

OPERATOR_TOOL_NAMES: frozenset[str] = ROOT_AGENT_TOOL_NAMES | frozenset({
    "musubi_get_active_session",
    "musubi_get_status",
    "musubi_get_pause_state",
    "musubi_query_pipeline_runs",
    "musubi_query_stage_metrics",
    "musubi_query_agent_cycles",
    "musubi_query_agent_turns",
    "musubi_pipeline_stats",
    "musubi_query_schema_migrations",
    "musubi_query_subagent_events",
    "musubi_list_subagent_spawns",
    "musubi_session_credits",
    "musubi_credits_since",
})

PIPELINE_TOOL_NAMES: frozenset[str] = OPERATOR_TOOL_NAMES | frozenset({
    "musubi_new_session",
    "musubi_read_stage",
    "musubi_write_stage",
    "musubi_increment_attempt",
    "musubi_pause_session",
    "musubi_resume_session",
    "musubi_compute_chunks",
    "musubi_ensure_chunk_row",
    "musubi_consume_pending_action",
    "musubi_get_correction_rules",
    "musubi_get_injected_skills",
    "musubi_get_pipeline_stages",
    "musubi_record_stage_metric",
    "musubi_finalize_pipeline_run",
})

_SURFACES: dict[str, frozenset[str] | None] = {
    "agent": ROOT_AGENT_TOOL_NAMES,
    "operator": OPERATOR_TOOL_NAMES,
    "pipeline": PIPELINE_TOOL_NAMES,
    "full": None,
}


def tool_names_for_surface(surface: str) -> frozenset[str] | None:
    key = (surface or "").strip().lower()
    if key not in _SURFACES:
        raise ValueError(
            f"unknown tool surface {surface!r}; expected one of "
            f"{sorted(_SURFACES)}"
        )
    return _SURFACES[key]


def filter_tool_catalog(
    tools: list[dict[str, Any]],
    surface: str,
) -> list[dict[str, Any]]:
    allowed = tool_names_for_surface(surface)
    if allowed is None:
        return list(tools)
    return [tool for tool in tools if tool.get("name") in allowed]


def apply_fastmcp_tool_surface(mcp: Any, surface: str) -> None:
    allowed = tool_names_for_surface(surface)
    if allowed is None:
        return
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError("FastMCP tool manager shape changed; cannot filter tools")
    manager._tools = {name: tool for name, tool in tools.items() if name in allowed}
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_tool_surface.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/tool_surface.py musubi/tests/test_tool_surface.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(tools): define MCP tool surfaces"
```

---

### Task 2: Trim Internal Standalone Agent Tool Catalog

**Files:**
- Modify: `musubi/agent/run.py`
- Modify: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Consumes:
  - `filter_tool_catalog(tools, surface)` from `tool_surface.py`
- Produces:
  - Internal standalone default model-visible local Musubi tools: 20.
  - CLI flag `--tool-surface agent|operator|full`.
  - Env override `MUSUBI_TOOL_SURFACE=agent|operator|full`.

- [ ] **Step 1: Write failing agent catalog tests**

Append to `musubi/tests/test_agent_loop.py`:

```python
def test_run_agent_default_tool_surface_hides_driver_only_tools() -> None:
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    answer = asyncio.run(
        run_agent("inspect files", router, _musubi_dir(), log=io.StringIO(), max_tokens=0)
    )

    assert answer == "ok"
    names = {tool["name"] for tool in router.calls[0]["tools"]}
    assert "musubi_read_file" in names
    assert "musubi_recommend_skills" in names
    assert "musubi_retrieve" in names
    assert "musubi_write_stage" not in names
    assert "musubi_read_stage" not in names
    assert "musubi_get_subagent_context" not in names
    assert "musubi_record_agent_cycle" not in names


def test_run_agent_full_tool_surface_keeps_internal_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSUBI_TOOL_SURFACE", "full")
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    asyncio.run(
        run_agent("debug", router, _musubi_dir(), log=io.StringIO(), max_tokens=0)
    )

    names = {tool["name"] for tool in router.calls[0]["tools"]}
    assert "musubi_write_stage" in names
    assert "musubi_read_stage" in names
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_loop.py::test_run_agent_default_tool_surface_hides_driver_only_tools musubi\tests\test_agent_loop.py::test_run_agent_full_tool_surface_keeps_internal_tools -q -p no:cacheprovider
```

Expected: FAIL because the default root catalog still contains hidden tools and `MUSUBI_TOOL_SURFACE` is ignored.

- [ ] **Step 3: Implement internal filtering helper in `agent/run.py`**

In `musubi/agent/run.py`, import:

```python
from tool_surface import filter_tool_catalog, tool_names_for_surface
```

Add:

```python
def _tool_surface(cli_value: str | None = None) -> str:
    raw = (cli_value or os.environ.get("MUSUBI_TOOL_SURFACE") or "agent").strip().lower()
    if raw == "pipeline":
        raise ValueError("standalone agent supports tool surfaces: agent, operator, full")
    tool_names_for_surface(raw)
    return raw
```

In `run_agent(...)`, add parameter:

```python
tool_surface: str | None = None,
```

Replace:

```python
mcp_tools = (await session.list_tools()).tools
gateway.register_local(
    session, [_mcp_to_anthropic_tool(t) for t in mcp_tools]
)
```

with:

```python
mcp_tools = (await session.list_tools()).tools
local_tools = [_mcp_to_anthropic_tool(t) for t in mcp_tools]
surface = _tool_surface(tool_surface)
visible_local_tools = filter_tool_catalog(local_tools, surface)
gateway.register_local(session, visible_local_tools)
```

Update the log line:

```python
print(
    f"[agent] vendor={vendor.name} model={vendor.model} {profile_part}"
    f"tool_surface={surface} tools={len(tools)} "
    f"(musubi_visible={len(visible_local_tools)}, musubi_total={len(mcp_tools)}, "
    f"external={n_external})",
    file=log,
)
```

In CLI parser, add:

```python
ap.add_argument(
    "--tool-surface",
    choices=["agent", "operator", "full"],
    default=None,
    help="Local Musubi tool catalog exposed to the model; default agent.",
)
```

Pass it into `run_agent(..., tool_surface=args.tool_surface)`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_agent_loop.py::test_run_agent_default_tool_surface_hides_driver_only_tools musubi\tests\test_agent_loop.py::test_run_agent_full_tool_surface_keeps_internal_tools -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/agent/run.py musubi/tests/test_agent_loop.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(agent): trim default MCP tool surface"
```

---

### Task 3: Preserve External MCP Federation Behavior

**Files:**
- Modify: `musubi/tests/test_mcp_gateway.py`
- Modify if needed: `musubi/agent/mcp_gateway.py`

**Interfaces:**
- Consumes:
  - `McpGateway.register_local(...)`
  - `McpGateway.register_remote(...)`
- Produces:
  - Local Musubi tools may be filtered before registration.
  - Federated external tools remain additive and visible when configured.

- [ ] **Step 1: Add regression tests for external tools**

Append to `musubi/tests/test_mcp_gateway.py`:

```python
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
```

- [ ] **Step 2: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_mcp_gateway.py -q -p no:cacheprovider
```

Expected: PASS. If this fails, fix `McpGateway` without changing external tool namespacing or route ownership.

- [ ] **Step 3: Commit**

```powershell
git add musubi/tests/test_mcp_gateway.py musubi/agent/mcp_gateway.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "test(agent): pin external MCP tools outside local surface filter"
```

---

### Task 4: Add External `musubi serve --surface` Profiles

**Files:**
- Modify: `musubi/server.py`
- Modify: `musubi/cli.py`
- Create or modify: `musubi/tests/test_server_surface.py`

**Interfaces:**
- Consumes:
  - `apply_fastmcp_tool_surface(mcp, surface)` from `tool_surface.py`
- Produces:
  - `server.serve(surface: str = "full") -> None`
  - `musubi serve --surface full` default-compatible full server.
  - `musubi serve --surface agent` external MCP server exposing the 20-tool agent catalog.
  - `musubi serve --surface operator` external operator/debug catalog.
  - `musubi serve --surface pipeline` external pipeline/VS Code catalog.

- [ ] **Step 1: Write failing server surface tests**

Create `musubi/tests/test_server_surface.py`:

```python
from __future__ import annotations

import pytest

import cli
import server


def test_server_apply_agent_surface_hides_driver_only_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[set[str]] = []

    def fake_run(*, transport: str) -> None:
        seen.append(set(server.mcp._tool_manager._tools))

    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.serve(surface="agent")

    assert seen
    assert "musubi_read_file" in seen[0]
    assert "musubi_write_stage" not in seen[0]
    assert "musubi_read_stage" not in seen[0]


def test_server_full_surface_keeps_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[set[str]] = []

    def fake_run(*, transport: str) -> None:
        seen.append(set(server.mcp._tool_manager._tools))

    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.serve(surface="full")

    assert "musubi_write_stage" in seen[0]
    assert "musubi_read_stage" in seen[0]


def test_cli_serve_passes_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fake_serve(surface: str = "full") -> None:
        called.append(surface)

    monkeypatch.setattr("server.serve", fake_serve)
    monkeypatch.setattr("sys.argv", ["musubi", "serve", "--surface", "agent"])

    cli.main()

    assert called == ["agent"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_server_surface.py -q -p no:cacheprovider
```

Expected: FAIL because `server.serve(surface=...)` and CLI parsing do not exist.

- [ ] **Step 3: Implement `server.serve(surface=...)`**

In `musubi/server.py`, import:

```python
from tool_surface import apply_fastmcp_tool_surface
```

Replace:

```python
def serve() -> None:
    """Start the MCP stdio server. Called by cli.py."""
    mcp.run(transport="stdio")
```

with:

```python
def serve(surface: str = "full") -> None:
    """Start the MCP stdio server. Called by cli.py."""
    apply_fastmcp_tool_surface(mcp, surface)
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Implement `musubi serve --surface` in `cli.py`**

Replace the `serve` branch in `musubi/cli.py`:

```python
if cmd == "serve":
    from server import serve
    serve()
    return
```

with:

```python
if cmd == "serve":
    surface = "full"
    rest = args[1:]
    if rest:
        if len(rest) == 2 and rest[0] == "--surface":
            surface = rest[1]
        else:
            print(
                "Usage: musubi serve [--surface agent|operator|pipeline|full]",
                file=sys.stderr,
            )
            sys.exit(1)
    from server import serve
    serve(surface=surface)
    return
```

Keep the top-level usage string as:

```python
print("Usage: musubi {serve|setup}", file=sys.stderr)
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_server_surface.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add musubi/server.py musubi/cli.py musubi/tests/test_server_surface.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(server): add MCP surface profiles"
```

---

### Task 5: Document Operation And Rollout

**Files:**
- Modify: `docs/guide.md`
- Modify: `docs/roadmap.md`
- Modify if needed: `musubi/setup_wizard.py`
- Modify if needed: `musubi/tests/test_setup_wizard.py`

**Interfaces:**
- Consumes:
  - Internal `agent --tool-surface`.
  - External `musubi serve --surface`.
- Produces:
  - Docs that distinguish internal driver trimming from external MCP server profiles.
  - No default external behavior change unless explicitly configured.

- [ ] **Step 1: Update `docs/guide.md`**

Add a subsection under the MCP/server or agent area:

```markdown
### Tool surfaces

Musubi keeps the full substrate API, but drivers should expose only the tools
their model needs.

- Standalone `agent` defaults to `--tool-surface agent`, a focused root-agent
  catalog of file, execution, skill, compression, memory, and orchestration
  tools.
- `agent --tool-surface full` is an escape hatch for debugging.
- External MCP clients can opt into a smaller server catalog with
  `musubi serve --surface agent`.
- `musubi serve` still defaults to `--surface full` for compatibility with
  existing VS Code, GUI, and custom MCP configurations.

Surface profiles hide tools from `list_tools()`; they do not delete tool
implementations or replace the policy boundary.
```

- [ ] **Step 2: Update `docs/roadmap.md` summary**

Replace the current Live Substrate Work line:

```markdown
- **Standalone tool-catalog trimming.** Reduce model-visible `musubi_*` tool
  schemas for the root standalone agent without removing any substrate tools.
```

with:

```markdown
- **MCP tool surface profiles.** Trim model-visible tool catalogs for internal
  and external drivers without removing substrate tools. Implementation plan:
  [`2026-07-01-mcp-tool-surface-trimming.md`](./superpowers/plans/2026-07-01-mcp-tool-surface-trimming.md).
```

- [ ] **Step 3: Check setup wizard generated MCP config**

Inspect `musubi/setup_wizard.py` for `.vscode/mcp.json` generation. If it writes `musubi serve`, leave it unchanged for this rollout because `musubi serve` defaults to `full`. Add only a doc comment if the setup wizard already has comments near the generated command.

- [ ] **Step 4: Run docs and focused verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_tool_surface.py musubi\tests\test_mcp_gateway.py musubi\tests\test_agent_loop.py musubi\tests\test_server_surface.py musubi\tests\test_setup_wizard.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe scripts\check_musubi_tier.py
git diff --check
```

Expected:

```text
all selected tests pass
[check-musubi-tier] OK
git diff --check exits 0
```

- [ ] **Step 5: Commit docs**

```powershell
git add docs/guide.md docs/roadmap.md musubi/setup_wizard.py musubi/tests/test_setup_wizard.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(tools): document MCP tool surfaces"
```

---

## Self-Review

- Spec coverage: The plan covers both requested implementation directions: internal Musubi driver trimming and external MCP server profile surfaces.
- Operation safety: Internal standalone default changes to `agent`; external `musubi serve` remains `full` by default to preserve current MCP clients.
- Capability preservation: No server tool implementation is deleted. Hidden tools remain available through full/pipeline/operator surfaces or direct driver calls.
- Placeholder scan: No placeholder markers or unresolved implementation steps remain.
- Type consistency: `tool_surface.py` defines the shared surface names used by `agent/run.py`, `server.py`, and `cli.py`.

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-07-01-mcp-tool-surface-trimming.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

Choose one before implementation begins.
