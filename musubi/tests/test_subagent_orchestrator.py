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
import sqlite3
from pathlib import Path
from typing import Any

from agent.runtime_log import PROTOCOL_PREFIX, RuntimeLogWriter
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


def test_run_subagent_attributes_child_log_lines_to_exact_handle(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            payloads = {
                "musubi_spawn_subagent": (
                    '{"status":"spawned","handle_id":"worker-exact-123",'
                    '"role":"coder","max_turns":8}'
                ),
                "musubi_get_subagent_context": (
                    '{"status":"ok","brief":"build it",'
                    '"role_skill":null,"allowed_tools":[]}'
                ),
                "musubi_complete_subagent": (
                    '{"status":"recorded","final_status":"done",'
                    '"summary":"finished"}'
                ),
            }

            class Chunk:
                text = payloads[name]

            class Result:
                content = [Chunk()]

            return Result()

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        print("[agent] child diagnostic", file=kwargs["log"])
        return "finished", 1

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(subagent_mod, "_read_agent_md", lambda *args: "# Coder")
    raw = io.StringIO()
    log = RuntimeLogWriter(raw, request_id="request-1")

    asyncio.run(run_subagent(
        Session(),
        {"role": "coder", "brief": "build it", "parent_session_id": "parent"},
        FakeRouter([]),
        [],
        log,
        agents_dir=tmp_path,
        orchestration=Orchestration(parent_session_id="parent"),
    ))

    events = [
        json.loads(line.removeprefix(PROTOCOL_PREFIX))
        for line in raw.getvalue().split("\n")
        if line
    ]
    child = next(row for row in events if row["message"] == "[agent] child diagnostic")
    assert child["role"] == "coder"
    assert child["agent_handle"] == "worker-exact-123"


def test_run_subagent_logs_the_skill_it_pushed_into_the_worker_prompt(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    """A pushed skill is the one thing a worker gets that makes no tool call.

    `build_subagent_system_prompt` bakes it in, so nothing reached the runtime
    ledger and the console's per-agent Skills view was empty for every worker
    that did not additionally PULL one with `musubi_get_skill` — which is most
    of them, since HI #2's push exists precisely so the worker need not ask.
    """
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            payloads = {
                "musubi_spawn_subagent": (
                    '{"status":"spawned","handle_id":"worker-skill-1",'
                    '"role":"explorer","max_turns":8}'
                ),
                "musubi_get_subagent_context": (
                    '{"status":"ok","brief":"scan it",'
                    '"role_skill":"# Explorer","role_skill_id":"explorer",'
                    '"allowed_tools":[]}'
                ),
                "musubi_complete_subagent": (
                    '{"status":"recorded","final_status":"done",'
                    '"summary":"finished"}'
                ),
            }

            class Chunk:
                text = payloads[name]

            class Result:
                content = [Chunk()]

            return Result()

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        return "finished", 1

    monkeypatch.setattr(run_mod, "run_unit", fake_run_unit)
    monkeypatch.setattr(subagent_mod, "_read_agent_md", lambda *args: "# Explorer")
    raw = io.StringIO()
    log = RuntimeLogWriter(raw, request_id="request-1")

    asyncio.run(run_subagent(
        Session(),
        {"role": "explorer", "brief": "scan it", "parent_session_id": "parent"},
        FakeRouter([]),
        [],
        log,
        agents_dir=tmp_path,
        orchestration=Orchestration(parent_session_id="parent"),
    ))

    events = [
        json.loads(line.removeprefix(PROTOCOL_PREFIX))
        for line in raw.getvalue().split("\n")
        if line
    ]
    pushed = [row for row in events if row["category"] == "skills"]
    assert len(pushed) == 1
    assert pushed[0]["message"] == "[agent]   skill pushed=explorer agent=explorer"
    # Attributed to the exact handle, so the console can scope it to one node.
    assert pushed[0]["agent_handle"] == "worker-skill-1"


def test_run_subagent_records_policy_failure_without_replacement(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    from agent import run as run_mod
    from agent import subagent as subagent_mod

    completed: dict[str, Any] = {}

    class Session:
        async def call_tool(self, name, arguments):  # noqa: ANN001
            if name == "musubi_spawn_subagent":
                payload = (
                    '{"status":"spawned","handle_id":"h-policy",'
                    '"role":"coder","max_turns":8}'
                )
            elif name == "musubi_get_subagent_context":
                payload = (
                    '{"status":"ok","brief":"finish it",'
                    '"role_skill":null,"allowed_tools":[]}'
                )
            elif name == "musubi_complete_subagent":
                completed.update(arguments)
                payload = json.dumps({
                    "status": "recorded",
                    "final_status": arguments["status"],
                    "summary": arguments["summary"],
                })
            else:
                raise AssertionError(name)

            class Chunk:
                text = payload

            class Result:
                content = [Chunk()]

            return Result()

    async def fake_run_unit(*args, **kwargs):  # noqa: ANN001
        raise run_mod.PolicyDeniedError(
            role="coder",
            tool="musubi_new_session",
            reason="session-management tool is reserved for the root agent",
        )

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

    assert result.startswith("[incomplete]")
    assert completed["status"] == "escalated"
    outcome = orchestration.latest_unrecovered_failure()
    assert outcome is not None
    assert outcome.failure_kind is run_mod.FailureKind.POLICY
    assert run_mod.decide_recovery(
        outcome,
        same_role_failures=1,
        worker_slots=1,
    ) is run_mod.RecoveryAction.HALT

def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


def _spawn(role: str, brief: str, **extra: Any) -> LMResponse:
    selected_skill = {"coder": "web-ui"}.get(role, role)
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": "spawn-1", "name": "musubi_spawn_subagent",
        "input": {
            "role": role, "brief": brief,
            "pushed_skill_id": selected_skill,
            **extra,
        },
    }])


def _direct(role: str, path: str, intent: str = "modify") -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use",
        "id": "mode-direct",
        "name": "musubi_begin_direct",
        "input": {
            "target_intent": intent,
            "target_path": path,
            "worker_role": role,
        },
    }])


# ── happy path: spawn → run child → complete → summary fed back ─────────────


def test_spawn_runs_child_and_feeds_summary_back() -> None:
    router = FakeRouter([
        _direct("explorer", "."),
        _spawn("explorer", "list the python modules"),
        _text("explored: found server.py and run.py"),    # child cycle 0 (final)
        _text("done"),                                     # parent cycle 1 (final)
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent("delegate a scan", router, _musubi_dir(), log=log))

    assert answer == "done"
    # Parent's second LM call sees a bounded goal-state delta, not transcript.
    parent_followup = router.calls[3]["messages"]
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
        _direct("explorer", "."),
        _spawn("explorer", "scan"),
        _text("ok"),
        _text("done"),
    ])
    asyncio.run(run_agent("scan", router, _musubi_dir(), log=io.StringIO()))
    child_tools = {t["name"] for t in router.calls[2]["tools"]}
    # Read-only explorer: file read + discovery (glob/grep), never write/run.
    assert child_tools == {"musubi_read_file", "musubi_glob", "musubi_grep"}
    assert "musubi_write_file" not in child_tools
    assert "musubi_run_command" not in child_tools
    assert "musubi_spawn_subagent" not in child_tools  # leaves can't re-spawn


def test_coder_child_gets_write_tools_from_full_local_catalog() -> None:
    """The root model sees the small agent surface, while the coder worker is
    sized from the full local Musubi catalog and can write when policy allows.

    A model-authored ``allowed_tools`` list uses MCP names rather than the
    substrate's symbolic capabilities. Root must not be allowed to starve the
    role by forwarding that accidental narrowing.
    """
    router = FakeRouter([
        _direct("coder", "hello.html", "create"),
        _spawn(
            "coder",
            "create a file",
            allowed_tools=[
                "musubi_write_file",
                "musubi_edit_file",
                "musubi_run_command",
            ],
        ),
        _text("created: hello.html"),
        _text("done"),
    ])
    log = io.StringIO()
    asyncio.run(run_agent("create hello.html", router, _musubi_dir(), log=log))

    root_tools = {t["name"] for t in router.calls[0]["tools"]}
    child_tools = {t["name"] for t in router.calls[2]["tools"]}

    assert "musubi_write_file" not in root_tools
    assert "musubi_edit_file" not in root_tools
    assert "musubi_run_command" not in root_tools
    assert {"musubi_write_file", "musubi_edit_file", "musubi_run_command"} <= child_tools
    assert "ignored model allowed_tools on root spawn" in log.getvalue()


# ── deny path: an un-spawnable role surfaces the harness error verbatim ─────


def test_disallowed_role_policy_denial_is_terminal_without_running_child() -> None:
    router = FakeRouter([
        _direct("coder", "bad-role.txt", "create"),
        _spawn("saboteur", "do something"),
        _text("this response must not be consumed"),
    ])
    answer = asyncio.run(run_agent("bad role", router, _musubi_dir(), log=io.StringIO()))
    assert answer.startswith("[incomplete]")
    assert "saboteur" in answer
    assert len(router.calls) == 2


def test_spawn_subagent_policy_rejection_has_machine_readable_error_kind() -> None:
    import server

    denied = json.loads(server.musubi_spawn_subagent(
        parent_session_id="not-created",
        parent_agent_name="agent",
        role="saboteur",
        brief="do something",
    ))

    assert denied["status"] == "error"
    assert denied["error_kind"] == "policy_denied"


def test_worker_runtime_policy_denial_halts_root_without_replacement(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    from agent import run as run_mod

    recorded: list[run_mod.WorkerOutcome] = []
    original_record = Orchestration.record_worker_outcome

    def record_outcome(self, **kwargs):  # noqa: ANN001
        outcome = original_record(self, **kwargs)
        recorded.append(outcome)
        return outcome

    monkeypatch.setattr(Orchestration, "record_worker_outcome", record_outcome)
    monkeypatch.setenv("MUSUBI_ROOT", str(_musubi_dir().parent))
    router = FakeRouter([
        _direct("coder", "policy-test.html", "create"),
        _spawn("coder", "attempt a forbidden session operation"),
        LMResponse(stop_reason="tool_use", content=[{
            "type": "tool_use",
            "id": "worker-policy-denial",
            "name": "musubi_new_session",
            "input": {"request": "forged nested root"},
        }]),
        _text("this root response must not be consumed"),
    ])

    answer = asyncio.run(run_agent(
        "create policy-test.html",
        router,
        _musubi_dir(),
        log=io.StringIO(),
    ))

    assert answer.startswith("[incomplete]")
    assert "non-recoverable policy failure" in answer
    assert len(router.calls) == 3
    coder_outcomes = [outcome for outcome in recorded if outcome.role == "coder"]
    assert len(coder_outcomes) == 1
    assert coder_outcomes[0].status == "escalated"
    assert coder_outcomes[0].failure_kind is run_mod.FailureKind.POLICY


# ── escalation: child that won't stop is killed, parent still completes ─────


def test_child_max_turns_requires_recovery_before_root_success() -> None:
    # The model asks for max_turns=1 but explorer.agent.md owns the cap (6):
    # the child runs its full role budget, exhausts it on tool calls, and the
    # escalation still blocks root success until recovered.
    router = FakeRouter([
        _direct("explorer", "."),
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
    assert router.calls[8]["tools"] == []
    fed_back = str(router.calls[9]["messages"])
    assert "[root-goal-state]" in fed_back
    assert "reached the turn limit" in fed_back
    assert "max_turns=6 reached" in fed_back


def test_automatic_recovery_audit_records_two_real_workers_and_no_synthetic_root_cycle(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    from agent import run as run_mod
    from workspace.grants import MANIFEST_ENV, FolderGrant, RootRegistry

    musubi_root = _musubi_dir().parent
    audit_db = tmp_path / "audit.db"
    state_db = tmp_path / "musubi.db"
    monkeypatch.setattr(run_mod, "_server_audit_db_path", lambda *_args: audit_db)
    monkeypatch.setattr(run_mod, "_server_db_path", lambda *_args: state_db)
    monkeypatch.setenv("MUSUBI_ROOT", str(musubi_root))
    monkeypatch.setenv(
        MANIFEST_ENV,
        RootRegistry.build(
            musubi_root,
            [FolderGrant("g-app", "app", tmp_path)],
        ).to_json(),
    )
    primary_summary = (
        "status: incomplete\n"
        "files_changed:\n- recovery.html\n"
        "summary: primary coder left recovery.html ready for continuation\n"
    )
    router = FakeRouter([
        _direct("coder", "recovery.html", "create"),
        _spawn("coder", "create recovery.html"),
        LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use", "id": "write-recovery",
                "name": "musubi_write_file", "input": {
                    "root": "app",
                    "path": "recovery.html",
                    "content": "<!doctype html><title>Recovery</title>",
                },
        }]),
        *[
            LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use", "id": f"read-recovery-{index}",
                "name": "musubi_read_file",
                "input": {"root": "app", "path": "recovery.html"},
            }])
            for index in range(7)
        ],
        _text(primary_summary),
        _text(
            "status: done\n"
            "files_changed:\n- recovery.html\n"
            "summary: replacement coder completed recovery.html\n"
        ),
        _text("done"),
    ])

    answer = asyncio.run(run_agent(
        "create recovery.html", router, _musubi_dir(), log=io.StringIO(),
    ))

    assert answer == "done"
    assert (tmp_path / "recovery.html").read_text(encoding="utf-8") == (
        "<!doctype html><title>Recovery</title>"
    )
    assert len(router.calls) == 13
    forced_final_call = router.calls[10]
    assert forced_final_call["tools"] == []
    forced_final_tool_uses = [
        block
        for message in forced_final_call["messages"]
        for block in (
            message.get("content")
            if isinstance(message.get("content"), list)
            else []
        )
        if block.get("type") == "tool_use"
    ]
    assert [block["id"] for block in forced_final_tool_uses] == [
        "write-recovery", *[f"read-recovery-{index}" for index in range(7)],
    ]

    with sqlite3.connect(audit_db) as conn:
        conn.row_factory = sqlite3.Row
        audit_rows = [
            dict(row) for row in conn.execute(
                "SELECT handle_id, parent_session_id, role, event, brief, "
                "final_status, escalated, turns FROM subagent_audit "
                "WHERE parent_session_id = (SELECT parent_session_id "
                "FROM subagent_audit ORDER BY id LIMIT 1) ORDER BY id"
            )
        ]

    assert len(audit_rows) == 4
    primary_handle = audit_rows[0]["handle_id"]
    replacement_handle = audit_rows[2]["handle_id"]
    assert primary_handle != replacement_handle
    assert [(row["handle_id"], row["event"]) for row in audit_rows] == [
        (primary_handle, "spawned"), (primary_handle, "completed"),
        (replacement_handle, "spawned"), (replacement_handle, "completed"),
    ]
    parent_session_id = audit_rows[0]["parent_session_id"]
    assert audit_rows[2]["parent_session_id"] == parent_session_id
    assert {row["parent_session_id"] for row in audit_rows} == {parent_session_id}
    assert {row["role"] for row in audit_rows} == {"coder"}
    assert audit_rows[1]["final_status"] == "escalated"
    assert audit_rows[1]["escalated"] == 1
    assert audit_rows[1]["turns"] == 8
    assert audit_rows[3]["final_status"] == "done"
    assert audit_rows[3]["escalated"] == 0
    assert audit_rows[3]["turns"] == 1
    replacement_brief = audit_rows[2]["brief"]
    assert "[worker-replacement]" in replacement_brief
    assert "Touched files: app::recovery.html" in replacement_brief
    assert "Prior status: escalated" in replacement_brief
    assert f"Prior summary: {primary_summary}" in replacement_brief

    with sqlite3.connect(state_db) as conn:
        conn.row_factory = sqlite3.Row
        sub_sessions = [
            dict(row) for row in conn.execute(
                "SELECT handle_id, parent_session_id, role, brief, status, "
                "escalated, turns FROM sub_sessions "
                "WHERE parent_session_id = ? ORDER BY rowid",
                (parent_session_id,),
            )
        ]
        assert len(sub_sessions) == 2
        cycle_rows = [
            dict(row) for row in conn.execute(
                "SELECT worker_id, stage, cycle_idx FROM agent_cycles "
                "WHERE session_id = ? ORDER BY id", (parent_session_id,),
            )
        ]

    assert [row["handle_id"] for row in sub_sessions] == [primary_handle, replacement_handle]
    assert [(row["status"], row["escalated"], row["turns"]) for row in sub_sessions] == [
        ("escalated", 1, 8), ("done", 0, 1),
    ]
    assert {row["parent_session_id"] for row in sub_sessions} == {parent_session_id}
    assert sub_sessions[1]["parent_session_id"] == sub_sessions[0]["parent_session_id"]
    assert {row["role"] for row in sub_sessions} == {"coder"}
    assert "[worker-replacement]" in sub_sessions[1]["brief"]
    assert "Touched files: app::recovery.html" in sub_sessions[1]["brief"]
    assert "Prior status: escalated" in sub_sessions[1]["brief"]
    assert f"Prior summary: {primary_summary}" in sub_sessions[1]["brief"]

    assert len(cycle_rows) == len(router.calls) == 13
    assert [row["worker_id"] for row in cycle_rows].count("root") == 3
    assert [row["worker_id"] for row in cycle_rows].count(primary_handle) == 9
    assert [row["worker_id"] for row in cycle_rows].count(replacement_handle) == 1
    assert [row["worker_id"] for row in cycle_rows] == [
        "root", *[primary_handle] * 9, "root", replacement_handle, "root",
    ]
    assert [
        row["cycle_idx"] for row in cycle_rows if row["worker_id"] == "root"
    ] == [0, 1, 2]
    assert [
        row["cycle_idx"]
        for row in cycle_rows
        if row["worker_id"] == primary_handle
    ] == list(range(9))
    assert [
        row["cycle_idx"]
        for row in cycle_rows
        if row["worker_id"] == replacement_handle
    ] == [0]


def test_child_blocked_reason_prevents_unrecovered_parent_success() -> None:
    blocked = (
        '[blocked] {"status":"blocked",'
        '"reason":"output_too_large_for_single_tool_call",'
        '"retry_same_strategy":false}'
    )
    router = FakeRouter([
        _direct("coder", "dashboard.html", "create"),
        _spawn("coder", "create html dashboard"),
        _text(blocked),
        _text("done"),
    ])

    answer = asyncio.run(run_agent(
        "create dashboard.html",
        router,
        _musubi_dir(),
        log=io.StringIO(),
    ))

    assert answer.startswith("[incomplete]")
    assert "coder (escalated)" in answer
    assert "output_too_large_for_single_tool_call" in answer
    fed_back = str(router.calls[3]["messages"])
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


def test_role_without_maxturns_uses_server_default(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    spawn, run_unit_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path, agent_md="# Coder", spawn_args={"max_turns": 1},
    )
    assert "max_turns" not in spawn
    assert run_unit_kwargs["max_cycles"] == 8


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


# ── incident regressions (governed scope, budget, recovery) ──────────────────


def test_a_bare_product_request_no_longer_halts_before_the_model() -> None:
    """Deleted with plan step 4, and the deletion is the assertion.

    `create a new website` used to be met with a canned question, from a regex
    that had read nothing. The traced session shows why that could not work:
    the ANSWER re-matched the same pattern and drew the identical sentence
    back, three turns for zero model calls and zero files — a fixed point, not
    a stall. What the halt was groping at is now checked rather than guessed:
    `GoalState.evidence_gap` refuses a WRITER while nothing establishes the
    target, and an explorer clears it inside the same turn.
    """
    from agent.scope import classify_task
    from agent.routes import RouteKind

    hint = classify_task("create a new website")

    assert hint.route == RouteKind.ROOT_DECIDES
    assert hint.assessment is None


def test_bounded_scaffold_cannot_be_starved_or_abandon_recovery(
    monkeypatch, tmp_path: Path,
) -> None:  # noqa: ANN001
    # A bounded scaffold coder cannot be starved below its role budget, and a
    # turn-cap failure with surviving files is an automatic replacement, never
    # an abandoned recovery.
    from agent.run import FailureKind, RecoveryAction, WorkerOutcome, decide_recovery

    spawn, run_kwargs = _run_direct_spawn(
        monkeypatch, tmp_path,
        agent_md=_CODER_MD_8,
        spawn_args={"max_turns": 6},
    )
    outcome = WorkerOutcome(
        role="coder",
        status="escalated",
        summary="Next.js scaffold unfinished",
        touched_files=("app/page.tsx", "app/layout.tsx"),
        brief="create the bounded scaffold",
        failure_kind=FailureKind.TURN_CAP,
    )

    assert spawn["max_turns"] == 8
    assert run_kwargs["max_cycles"] == 8
    assert decide_recovery(
        outcome, same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.AUTO_REPLACE
