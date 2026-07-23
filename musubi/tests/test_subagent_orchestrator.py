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
import json
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
        brief="finish it",
        failure_kind=run_mod.FailureKind.UNKNOWN,
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
    # Parent's second LM call sees a bounded goal-state delta, not transcript.
    parent_followup = router.calls[2]["messages"]
    feedback = str(parent_followup)
    assert "[root-goal-state]" in feedback
    assert "explored: found server.py" in feedback
    assert "latest_worker=explorer (done)" in feedback


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
    # The model asks for max_turns=1 but explorer.agent.md owns the cap (6):
    # the child runs its full role budget, exhausts it on tool calls, and the
    # escalation still blocks root success until recovered.
    router = FakeRouter([
        _spawn("explorer", "loop", max_turns=1),
    ] + [
        # child cycles 0-5: keeps asking for a tool → exhausts the role cap
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use", "id": f"r{index}", "name": "musubi_read_file",
            "input": {"path": "README.md"},
        }])
        for index in range(6)
    ] + [
        _text("[incomplete] reached the turn limit after reading README.md"),
        _text("done"),  # parent final
    ])
    answer = asyncio.run(run_agent("loopy", router, _musubi_dir(), log=io.StringIO()))
    assert answer.startswith("[incomplete]")
    assert "explorer (escalated)" in answer
    assert "reached the turn limit" in answer
    assert router.calls[7]["tools"] == []
    fed_back = str(router.calls[8]["messages"])
    assert "[root-goal-state]" in fed_back
    assert "reached the turn limit" in fed_back
    assert "max_turns=6 reached" in fed_back


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
    fed_back = str(router.calls[2]["messages"])
    assert "[root-goal-state]" in fed_back
    assert "output_too_large_for_single_tool_call" in fed_back
    assert "blocked" in fed_back


# ── force-concluded-but-complete worker is accepted, not escalated ──────────


def _completed_artifact_session(
    completed: dict[str, Any], *, max_turns: int,
) -> Any:
    """Fake MCP session that EMULATES the server's turn-cap coercion.

    `sub_sessions.complete` coerces done→escalated at turns >= max_turns
    unless a 'done' completion carries an `artifacts` manifest the harness
    verifies. A fake that omits this layer hides exactly the mechanism that
    decides the outcome in production (that gap let an earlier fix pass its
    tests while being defeated end-to-end), so the fake mirrors it.
    """

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            if name == "musubi_complete_subagent":
                completed.update(arguments)
                final = arguments.get("status", "done")
                if int(arguments.get("turns", 0)) >= max_turns and not (
                    final == "done" and arguments.get("artifacts")
                ):
                    final = "escalated"
                payload = json.dumps({
                    "status": "recorded",
                    "final_status": final,
                    "summary": arguments.get("summary"),
                })
            elif name == "musubi_spawn_subagent":
                payload = (
                    '{"status":"spawned","handle_id":"h-nyc","role":"coder",'
                    f'"max_turns":{max_turns}}}'
                )
            elif name == "musubi_get_subagent_context":
                payload = (
                    '{"status":"ok","brief":"nyc dashboard","role_skill":null,'
                    '"allowed_tools":[]}'
                )
            else:
                payload = '{"status":"ok"}'

            class Chunk:
                text = payload

            class Result:
                content = [Chunk()]

            return Result()

    return Session()


_DONE_FINAL = (
    "status: done\n"
    "files_changed:\n- artifacts/nyc-dashboard.html\n"
    "summary: created a self-contained NYC dashboard\n"
    "verification: 10764 bytes, valid HTML\n"
)


def test_max_turns_worker_with_completed_artifact_accepted_as_done(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    """The NYC case: a coder force-concluded at the turn cap whose declared
    artifact exists and is non-empty is accepted as done — no false escalation,
    so the root is never pushed into a pointless recovery."""
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    artifact = tmp_path / "artifacts" / "nyc-dashboard.html"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")

    completed: dict[str, Any] = {}

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        run_mod._worker_touched_files.get().add("artifacts/nyc-dashboard.html")
        return _DONE_FINAL, 10  # turns == max_turns

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(subagent_mod, "_read_agent_md", lambda *a: "# Coder")
    orchestration = Orchestration(parent_session_id="parent")

    result = asyncio.run(run_subagent(
        _completed_artifact_session(completed, max_turns=10),
        {"role": "coder", "brief": "nyc", "parent_session_id": "parent"},
        FakeRouter([]), [], io.StringIO(),
        agents_dir=tmp_path, orchestration=orchestration,
    ))

    assert completed["status"] == "done"
    # The driver's claim travels as an artifacts manifest the harness
    # re-verifies before waiving its own turn-cap coercion.
    assert completed["artifacts"] == ["artifacts/nyc-dashboard.html"]
    assert orchestration.latest_unrecovered_failure() is None
    assert "status: done" in result


def test_max_turns_worker_without_artifact_still_escalates(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    """Force-concluded at the cap but the declared file does not exist → the
    deliverable was NOT produced, so it stays an escalation."""
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))  # artifact never written

    completed: dict[str, Any] = {}

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        run_mod._worker_touched_files.get().add("artifacts/nyc-dashboard.html")
        return _DONE_FINAL, 10

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(subagent_mod, "_read_agent_md", lambda *a: "# Coder")
    orchestration = Orchestration(parent_session_id="parent")

    asyncio.run(run_subagent(
        _completed_artifact_session(completed, max_turns=10),
        {"role": "coder", "brief": "nyc", "parent_session_id": "parent"},
        FakeRouter([]), [], io.StringIO(),
        agents_dir=tmp_path, orchestration=orchestration,
    ))

    assert completed["status"] == "escalated"
    assert "artifacts" not in completed
    assert orchestration.latest_unrecovered_failure() is not None


def test_forced_final_artifacts_semantics(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    from agent.subagent import _forced_final_artifacts

    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    f = tmp_path / "out.html"
    f.write_text("<html></html>", encoding="utf-8")
    empty = tmp_path / "empty.html"
    empty.write_text("", encoding="utf-8")

    # Declared done + surviving non-empty file → the survivors, sorted.
    assert _forced_final_artifacts(
        "status: done\nfiles_changed:", {"out.html"},
    ) == ["out.html"]
    # A deleted scratch file (generator pattern) is ignored, mirroring the
    # mechanical gate's G1 filter — the surviving artifact still qualifies.
    assert _forced_final_artifacts(
        "status: done", {"out.html", "gen-scratch.py"},
    ) == ["out.html"]
    # No touched files → nothing was produced.
    assert _forced_final_artifacts("status: done", set()) is None
    # Every touched file gone → nothing survived to deliver.
    assert _forced_final_artifacts("status: done", {"missing.html"}) is None
    # A surviving file that is EMPTY is truncation evidence.
    assert _forced_final_artifacts("status: done", {"empty.html"}) is None
    # Touched a real file but did not declare done.
    assert _forced_final_artifacts("status: incomplete", {"out.html"}) is None
    # A leading [mechanical] banner does not hide the status line.
    assert _forced_final_artifacts(
        "[mechanical] result=skipped\nstatus: done", {"out.html"},
    ) == ["out.html"]


# ── one-cap rule for direct workers: frontmatter maxTurns clamps the spawn ──


def _capturing_spawn_session(captured_spawn: dict[str, Any]) -> Any:
    """Fake session that echoes the spawn's max_turns like the real server."""

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            if name == "musubi_spawn_subagent":
                captured_spawn.clear()
                captured_spawn.update(arguments)
                payload = json.dumps({
                    "status": "spawned", "handle_id": "h-cap", "role": "coder",
                    "max_turns": arguments.get("max_turns", 8),
                })
            elif name == "musubi_get_subagent_context":
                payload = (
                    '{"status":"ok","brief":"b","role_skill":null,'
                    '"allowed_tools":[]}'
                )
            else:
                payload = '{"status":"recorded"}'

            class Chunk:
                text = payload

            class Result:
                content = [Chunk()]

            return Result()

    return Session()


def _run_direct_spawn(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    agent_md: str,
    spawn_args: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    captured_spawn: dict[str, Any] = {}
    seen_run_unit: dict[str, Any] = {}

    async def fake_run_unit(*args: Any, **kwargs: Any) -> tuple[str, int]:
        seen_run_unit.update(kwargs)
        return "status: done", 1

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(subagent_mod, "_read_agent_md", lambda *a: agent_md)

    asyncio.run(run_subagent(
        _capturing_spawn_session(captured_spawn),
        {"role": "coder", "brief": "b", "parent_session_id": "p", **spawn_args},
        FakeRouter([]), [], io.StringIO(), agents_dir=tmp_path,
    ))
    return captured_spawn, seen_run_unit


_CODER_MD_8 = "---\nname: Coder\nmaxTurns: 8\n---\n# Coder"


def test_model_spawn_request_cannot_exceed_frontmatter_maxturns(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    """The NYC gap: the root model handed a coder max_turns=10 while
    coder.agent.md declares 8 — the role contract was silently ignored.
    Now the declared cap clamps the request before the spawn row is written,
    so ONE value flows through spawn, runtime, and audit."""
    spawn, run_unit_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8, spawn_args={"max_turns": 10},
    )
    assert spawn["max_turns"] == 8
    assert run_unit_kwargs["max_cycles"] == 8


def test_model_spawn_request_cannot_reduce_frontmatter_maxturns(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    """Role frontmatter is the SOLE owner of a direct worker's turn cap: the
    spawning model can no longer starve a coder below its declared budget
    (the observed failure handed max_turns=2 to a role whose contract
    declares 8, guaranteeing a turn-cap escalation)."""
    spawn, run_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8,
        spawn_args={"max_turns": 2},
    )
    assert spawn["max_turns"] == 8
    assert run_kwargs["max_cycles"] == 8


def test_replacement_receives_full_role_turn_budget(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    spawn, run_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8,
        spawn_args={"max_turns": 1, "brief": "[worker-replacement] continue"},
    )
    assert spawn["max_turns"] == 8
    assert run_kwargs["max_cycles"] == 8


def test_absent_spawn_request_uses_frontmatter_maxturns(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    md = "---\nname: Coder\nmaxTurns: 5\n---\n# Coder"
    spawn, run_unit_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path, agent_md=md, spawn_args={},
    )
    assert spawn["max_turns"] == 5
    assert run_unit_kwargs["max_cycles"] == 5


def test_undeclared_frontmatter_leaves_spawn_request_untouched(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    spawn, _ = _run_direct_spawn(
        monkeypatch, tmp_path, agent_md="# Coder", spawn_args={},
    )
    assert "max_turns" not in spawn


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
