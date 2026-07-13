"""Tests for the standalone sub-agent orchestrator.

musubi-tier: ephemeral test — pins the spawn→run→complete contract the
extension's runner already implements. A single FakeRouter serves both the
parent and the (in-process) child because they share one vendor; responses are
popped in execution order. The real Musubi MCP server IS spawned so the
spawn/context/complete tools exercise the actual firewall + audit path.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from agent.run import Orchestration, run_agent
from agent.subagent import (
    _frontmatter_max_output_tokens,
    build_subagent_system_prompt,
    run_subagent,
    select_child_tools,
)
from agent.vendors.base import LMResponse, LMRouter


class FakeRouter(LMRouter):
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[LMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        self.calls.append({"messages": messages, "tools": tools})
        if not self._responses:
            raise AssertionError("FakeRouter ran out of canned responses")
        return self._responses.pop(0)


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def test_run_subagent_records_terminal_outcome_for_parent_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            payloads = {
                "musubi_spawn_subagent": (
                    '{"status":"spawned","handle_id":"h-recovery",'
                    '"role":"coder","max_turns":8}'
                ),
                "musubi_get_subagent_context": (
                    '{"status":"ok","brief":"finish it",'
                    '"role_skill":null,"allowed_tools":[]}'
                ),
                "musubi_complete_subagent": (
                    '{"status":"recorded","final_status":"failed",'
                    '"summary":"[incomplete] verified partial"}'
                ),
            }

            class Chunk:
                text = payloads[name]

            class Result:
                content = [Chunk()]

            return Result()

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        run_mod._worker_touched_files.get().add("dashboard.html")
        return "[incomplete] partial", 3

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(subagent_mod, "_read_agent_md", lambda *args: "# Coder")
    orchestration = Orchestration(parent_session_id="parent")

    result = asyncio.run(run_subagent(
        Session(),
        {"role": "coder", "brief": "finish it", "parent_session_id": "parent"},
        FakeRouter([]),
        [],
        io.StringIO(),
        agents_dir=tmp_path,
        orchestration=orchestration,
    ))

    assert result == "[incomplete] verified partial"
    assert orchestration.latest_failed_outcome("coder") == run_mod.WorkerOutcome(
        role="coder",
        status="failed",
        summary="[incomplete] verified partial",
        touched_files=("dashboard.html",),
    )


def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


def _spawn(role: str, brief: str, **extra: Any) -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": "spawn-1", "name": "musubi_spawn_subagent",
        "input": {"role": role, "brief": brief, **extra},
    }])


# ── happy path: spawn → run child → complete → summary fed back ─────────────


def test_spawn_runs_child_and_feeds_summary_back() -> None:
    router = FakeRouter([
        _spawn("explorer", "list the python modules"),   # parent cycle 0
        _text("explored: found server.py and run.py"),    # child cycle 0 (final)
        _text("done"),                                     # parent cycle 1 (final)
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent("delegate a scan", router, _musubi_dir(), log=log))

    assert answer == "done"
    # Parent's second LM call must carry the child's summary as a tool_result.
    parent_followup = router.calls[2]["messages"]
    tool_results = [
        b for m in parent_followup if isinstance(m.get("content"), list)
        for b in m["content"] if b.get("type") == "tool_result"
    ]
    assert tool_results, "expected a tool_result fed back to the parent"
    assert "explored: found server.py" in tool_results[-1]["content"]


# ── child gets a restricted, firewalled tool surface ────────────────────────


def test_child_tool_surface_is_restricted() -> None:
    """The explorer's child loop must be offered only read-only tools (file
    read + glob/grep discovery), never write/run — captured from the
    FakeRouter's second call."""
    router = FakeRouter([
        _spawn("explorer", "scan"),
        _text("ok"),
        _text("done"),
    ])
    asyncio.run(run_agent("scan", router, _musubi_dir(), log=io.StringIO()))
    child_tools = {t["name"] for t in router.calls[1]["tools"]}
    # Read-only explorer: file read + discovery (glob/grep), never write/run.
    assert child_tools == {"musubi_read_file", "musubi_glob", "musubi_grep"}
    assert "musubi_write_file" not in child_tools
    assert "musubi_run_command" not in child_tools
    assert "musubi_spawn_subagent" not in child_tools  # leaves can't re-spawn


def test_coder_child_gets_write_tools_from_full_local_catalog() -> None:
    """The root model sees the small agent surface, while the coder worker is
    sized from the full local Musubi catalog and can write when policy allows."""
    router = FakeRouter([
        _spawn("coder", "create a file"),
        _text("created: hello.html"),
        _text("done"),
    ])
    asyncio.run(run_agent("create a file", router, _musubi_dir(), log=io.StringIO()))

    root_tools = {t["name"] for t in router.calls[0]["tools"]}
    child_tools = {t["name"] for t in router.calls[1]["tools"]}

    assert "musubi_write_file" not in root_tools
    assert "musubi_edit_file" not in root_tools
    assert "musubi_run_command" not in root_tools
    assert {"musubi_write_file", "musubi_edit_file", "musubi_run_command"} <= child_tools


# ── deny path: an un-spawnable role surfaces the harness error verbatim ─────


def test_disallowed_role_surfaces_error_without_running_child() -> None:
    router = FakeRouter([
        _spawn("saboteur", "do something"),  # unknown role → fail-closed deny
        _text("acknowledged"),
    ])
    answer = asyncio.run(run_agent("bad role", router, _musubi_dir(), log=io.StringIO()))
    assert answer == "acknowledged"
    # Only two LM calls — the harness rejected the spawn, no child loop ran.
    assert len(router.calls) == 2
    fed_back = "".join(
        b["content"] for m in router.calls[1]["messages"]
        if isinstance(m.get("content"), list)
        for b in m["content"] if b.get("type") == "tool_result"
    )
    assert '"status": "error"' in fed_back and "saboteur" in fed_back


# ── escalation: child that won't stop is killed, parent still completes ─────


def test_child_max_turns_requires_recovery_before_root_success() -> None:
    router = FakeRouter([
        _spawn("explorer", "loop", max_turns=1),
        # child cycle 0: keeps asking for a tool → exhausts max_turns=1
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use", "id": "r1", "name": "musubi_read_file",
            "input": {"path": "README.md"},
        }]),
        _text("[incomplete] reached the turn limit after reading README.md"),
        _text("done"),  # parent final
    ])
    answer = asyncio.run(run_agent("loopy", router, _musubi_dir(), log=io.StringIO()))
    assert answer.startswith("[incomplete]")
    assert "explorer (escalated)" in answer
    assert "reached the turn limit" in answer
    assert router.calls[2]["tools"] == []
    fed_back = "".join(
        b["content"] for m in router.calls[3]["messages"]
        if isinstance(m.get("content"), list)
        for b in m["content"] if b.get("type") == "tool_result"
    )
    assert "reached the turn limit" in fed_back
    assert "max_turns=1 reached" in fed_back


def test_child_blocked_reason_prevents_unrecovered_parent_success() -> None:
    blocked = (
        '[blocked] {"status":"blocked",'
        '"reason":"output_too_large_for_single_tool_call",'
        '"retry_same_strategy":false}'
    )
    router = FakeRouter([
        _spawn("coder", "create html dashboard"),
        _text(blocked),
        _text("done"),
    ])

    answer = asyncio.run(run_agent(
        "create html dashboard",
        router,
        _musubi_dir(),
        log=io.StringIO(),
    ))

    assert answer.startswith("[incomplete]")
    assert "coder (escalated)" in answer
    assert "output_too_large_for_single_tool_call" in answer
    fed_back = "".join(
        b["content"] for m in router.calls[2]["messages"]
        if isinstance(m.get("content"), list)
        for b in m["content"] if b.get("type") == "tool_result"
    )
    assert "output_too_large_for_single_tool_call" in fed_back
    assert "blocked" in fed_back


# ── pure helpers ────────────────────────────────────────────────────────────


def test_select_child_tools_maps_symbolic_to_mcp() -> None:
    catalog = [
        {"name": "musubi_read_file"}, {"name": "musubi_write_file"},
        {"name": "musubi_append_file"}, {"name": "musubi_run_command"},
        {"name": "musubi_new_session"},
    ]
    read_only = {t["name"] for t in select_child_tools(catalog, ["Read", "View"])}
    assert read_only == {"musubi_read_file"}
    assert {t["name"] for t in select_child_tools(catalog, ["Write"])} == {
        "musubi_write_file",
        "musubi_append_file",
    }
    assert {t["name"] for t in select_child_tools(catalog, ["Bash"])} == {"musubi_run_command"}
    # Grep/Glob have no MCP equivalent → no silent shell upgrade.
    assert select_child_tools(catalog, ["Grep", "Glob"]) == []


def test_build_subagent_prompt_includes_brief_and_strips_frontmatter() -> None:
    agent_md = "---\nname: explorer\n---\n# Explorer\nScan code."
    prompt = build_subagent_system_prompt(agent_md, "skill body", "find the bug")
    assert "name: explorer" not in prompt        # frontmatter stripped
    assert "# Explorer" in prompt
    assert "## Skill (pushed by harness)" in prompt
    assert "find the bug" in prompt


def test_build_subagent_prompt_names_windows_shell_commands() -> None:
    prompt = build_subagent_system_prompt(
        "# Coder", None, "clean up", platform_name="nt"
    )
    assert "Host: Windows" in prompt
    assert "use `del`, not `rm`" in prompt


def test_build_subagent_prompt_names_posix_shell_commands() -> None:
    prompt = build_subagent_system_prompt(
        "# Coder", None, "clean up", platform_name="posix"
    )
    assert "Host: POSIX" in prompt
    assert "use `rm`, not `del`" in prompt


def test_frontmatter_max_output_tokens_reads_positive_integer() -> None:
    agent_md = "---\nname: large-writer\nmaxOutputTokens: 32768\n---\n# Writer"
    assert _frontmatter_max_output_tokens(agent_md) == 32768


def test_frontmatter_max_output_tokens_defaults_when_missing_or_invalid() -> None:
    assert _frontmatter_max_output_tokens("# Worker") is None
    assert _frontmatter_max_output_tokens(
        "---\nmaxOutputTokens: unlimited\n---\n# Worker"
    ) is None
    assert _frontmatter_max_output_tokens(
        "---\nmaxOutputTokens: 0\n---\n# Worker"
    ) is None


def test_run_subagent_threads_frontmatter_output_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            payloads = {
                "musubi_spawn_subagent": (
                    '{"status":"spawned","handle_id":"h1",'
                    '"role":"coder","max_turns":8}'
                ),
                "musubi_get_subagent_context": (
                    '{"status":"ok","brief":"write it",'
                    '"role_skill":null,"allowed_tools":[]}'
                ),
                "musubi_complete_subagent": (
                    '{"status":"complete","summary":"done"}'
                ),
            }

            class Chunk:
                text = payloads[name]

            class Result:
                content = [Chunk()]

            return Result()

    seen: dict[str, Any] = {}

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        seen.update(kwargs)
        return "done", 1

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(
        subagent_mod,
        "_read_agent_md",
        lambda role, agents_dir: (
            "---\nname: coder\nmaxOutputTokens: 32768\n---\n# Coder"
        ),
    )

    result = asyncio.run(run_subagent(
        Session(),
        {
            "role": "coder", "brief": "write it",
            "parent_session_id": "parent-1",
        },
        FakeRouter([]),
        [],
        io.StringIO(),
        agents_dir=tmp_path,
    ))

    assert result == "done"
    assert seen["worker_max_output"] == 32768
    assert seen["audit_session_id"] == "parent-1"
    assert seen["audit_worker_id"] == "h1"
    assert seen["audit_stage"] == "coder"
