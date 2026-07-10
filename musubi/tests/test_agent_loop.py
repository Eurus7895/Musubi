"""Tests for the agent loop driving a real harness MCP server.

musubi-tier: substrate test - pins the cycle-loop contract. Uses a
canned-response FakeRouter to keep the test hermetic; the real harness
MCP server IS spawned (we want to catch breakage there).
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent.run import Orchestration, run_agent
from agent.budget import TokenBudgetEnforcer, TokenBudgetExhaustedError
from agent.vendors.base import LMResponse, LMRouter


class FakeRouter(LMRouter):
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[LMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeRouter ran out of canned responses")
        return self._responses.pop(0)


def _musubi_dir() -> Path:
    """The agent-harness package directory (this file's grandparent)."""
    return Path(__file__).resolve().parent.parent


def test_server_env_forwards_musubi_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spawned server must see MUSUBI_* config while unrelated secrets stay out."""
    from agent.run import _server_env

    monkeypatch.setenv("MUSUBI_COMPRESS", "1")
    monkeypatch.setenv("MUSUBI_ROOT", "/some/dir")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-leak")
    env = _server_env()
    assert env["MUSUBI_COMPRESS"] == "1"
    assert env["MUSUBI_ROOT"] == "/some/dir"
    assert "UNRELATED_SECRET" not in env
    assert "PATH" in env


def test_server_db_path_matches_spawned_server_default(tmp_path: Path) -> None:
    from agent.run import _server_db_path

    musubi_dir = tmp_path / "checkout" / "musubi"
    assert _server_db_path(musubi_dir, {}) == musubi_dir / "storage" / "musubi.db"

    root = tmp_path / "portable-root"
    assert (
        _server_db_path(musubi_dir, {"MUSUBI_ROOT": str(root)})
        == root / "data" / "musubi.db"
    )


def test_run_loop_passes_context_compression_db_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod

    seen: list[Path | None] = []

    def spy_fit_context(messages, *, compression_db_path=None):  # noqa: ANN001
        seen.append(compression_db_path)
        return messages

    monkeypatch.setattr(run_mod, "fit_context", spy_fit_context)
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    db_path = tmp_path / "server.db"

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            object(), router, [], [{"role": "user", "content": "hi"}],
            max_cycles=1,
            log=io.StringIO(),
            compression_db_path=db_path,
        )
    )

    assert answer == "ok"
    assert cycles == 1
    assert seen == [db_path]


def test_run_loop_dispatches_tool_blocks_even_when_stop_reason_is_end_turn() -> None:
    """Some OpenAI-compatible routers emit tool_use blocks with end_turn.

    The loop must key off the presence of tool_use content, not only the
    stop_reason string, or write-capable workers silently skip their writes.
    """
    from agent import run as run_mod

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{
            "type": "tool_use",
            "id": "write-1",
            "name": "musubi_write_file",
            "input": {"path": "dashboard.html", "content": "<html></html>"},
        }]),
        LMResponse(stop_reason="end_turn", content=[{
            "type": "text",
            "text": "created dashboard.html",
        }]),
    ])
    session = _FakeToolSession('{"status":"ok","path":"dashboard.html"}')

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_write_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=2,
            log=io.StringIO(),
            role="coder",
        )
    )

    assert answer == "created dashboard.html"
    assert cycles == 2
    assert session.calls == [
        (
            "musubi_write_file",
            {"path": "dashboard.html", "content": "<html></html>"},
        )
    ]


def test_run_loop_preflight_budget_halt_skips_vendor_call() -> None:
    from agent import run as run_mod

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    budget = TokenBudgetEnforcer(max_tokens=100)

    with pytest.raises(TokenBudgetExhaustedError, match="preflight"):
        asyncio.run(
            run_mod._run_loop(
                object(),
                router,
                [{"name": "musubi_read_file", "description": "", "input_schema": {}}],
                [{"role": "user", "content": "x" * 20_000}],
                max_cycles=1,
                log=io.StringIO(),
                budget=budget,
            )
        )

    assert router.calls == []


def test_build_token_budget_uses_token_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import run as run_mod

    monkeypatch.delenv("MUSUBI_AGENT_MAX_TOKENS", raising=False)
    log = io.StringIO()

    budget = run_mod._build_token_budget(1234, None, log)

    assert budget is not None
    assert budget.max_tokens == 1234
    assert "token budget: 1234 tokens" in log.getvalue()


def test_build_token_budget_preserves_max_credits_zero_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import run as run_mod

    monkeypatch.delenv("MUSUBI_AGENT_MAX_TOKENS", raising=False)
    log = io.StringIO()

    budget = run_mod._build_token_budget(None, 0, log)

    assert budget is None
    assert "token budget: disabled" in log.getvalue()


def test_build_token_budget_ignores_positive_max_credits() -> None:
    from agent import run as run_mod

    log = io.StringIO()

    budget = run_mod._build_token_budget(4321, 10, log)

    assert budget is not None
    assert budget.max_tokens == 4321
    assert "--max-credits is deprecated and ignored" in log.getvalue()


class _FakeToolSession:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))

        class _Chunk:
            def __init__(self, text: str) -> None:
                self.text = text

        class _Result:
            def __init__(self, text: str) -> None:
                self.content = [_Chunk(text)]

        return _Result(self.text)


def _read_policy_rows(db_path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(
            "SELECT verdict, role, tool FROM policy_audit ORDER BY id"
        ))


def _read_tool_rows(db_path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(
            "SELECT agent, tool, status FROM tool_audit ORDER BY id"
        ))


def test_dispatch_denies_root_write_before_call_and_records_policy_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession()
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-denied",
                "name": "musubi_write_file",
                "input": {"path": "x.py", "content": "print('x')"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="agent"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[policy denied]" in result
    assert "spawn `coder`" in result
    assert "do not retry" in result
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_write_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_write_file", "denied")
    ]


def test_dispatch_denies_root_command_with_investigator_hint(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession()
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-denied",
                "name": "musubi_run_command",
                "input": {"command": "pytest"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="agent"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[policy denied]" in result
    assert "spawn `investigator`" in result
    assert "do not retry" in result
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_run_command")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_run_command", "denied")
    ]


def test_dispatch_allows_coder_write_and_records_post_tool_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-allowed",
                "name": "musubi_write_file",
                "input": {"path": "x.py", "content": "print('x')"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert result == "stored"
    assert session.calls == [("musubi_write_file", {"path": "x.py", "content": "print('x')"})]
    assert _read_policy_rows(audit_db) == [
        ("ALLOW", "coder", "musubi_write_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_write_file", "ok")
    ]


def test_dispatch_denies_root_append_before_call_and_records_policy_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession()
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-denied",
                "name": "musubi_append_file",
                "input": {"path": "x.py", "content": "print('x')"},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="agent"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[policy denied]" in result
    assert "spawn `coder`" in result
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("DENY", "agent", "musubi_append_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("agent", "musubi_append_file", "denied")
    ]


def test_dispatch_allows_coder_append_and_records_post_tool_audit(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-allowed",
                "name": "musubi_append_file",
                "input": {"path": "x.py", "content": "print('x')", "expected_offset": 0},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert result == "stored"
    assert session.calls == [
        (
            "musubi_append_file",
            {"path": "x.py", "content": "print('x')", "expected_offset": 0},
        )
    ]
    assert _read_policy_rows(audit_db) == [
        ("ALLOW", "coder", "musubi_append_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_append_file", "ok")
    ]


def test_dispatch_rejects_invalid_file_tool_args_before_mcp_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {"id": "call-bad", "name": "musubi_write_file", "input": {}},
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[tool error] invalid arguments" in result
    assert "path must be a string" in result
    assert "content must be a string" in result
    assert session.calls == []
    assert _read_policy_rows(audit_db) == [
        ("ALLOW", "coder", "musubi_write_file")
    ]
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_write_file", "error")
    ]


def test_dispatch_rejects_invalid_append_args_before_mcp_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    session = _FakeToolSession("stored")
    audit_db = tmp_path / "audit.db"

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "call-bad",
                "name": "musubi_append_file",
                "input": {"path": "x.py", "content": "x", "expected_offset": -1},
            },
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            refused=False,
            compression_db_path=None,
            audit_db_path=audit_db,
        )
    )

    assert "[tool error] invalid arguments" in result
    assert "expected_offset must be a non-negative integer" in result
    assert session.calls == []
    assert _read_tool_rows(audit_db) == [
        ("coder", "musubi_append_file", "error")
    ]


def test_dispatch_runs_file_mutations_sequentially_in_model_order(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    class _ConcurrencySession(_FakeToolSession):
        def __init__(self) -> None:
            super().__init__("stored")
            self.active = 0
            self.max_active = 0

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            try:
                return await super().call_tool(name, arguments)
            finally:
                self.active -= 1

    session = _ConcurrencySession()
    audit_db = tmp_path / "audit.db"
    tool_uses = [
        {
            "id": "w",
            "name": "musubi_write_file",
            "input": {"path": "x.py", "content": ""},
        },
        {
            "id": "a1",
            "name": "musubi_append_file",
            "input": {"path": "x.py", "content": "one", "expected_offset": 0},
        },
        {
            "id": "a2",
            "name": "musubi_append_file",
            "input": {"path": "x.py", "content": "two", "expected_offset": 3},
        },
    ]

    asyncio.run(
        run_mod._dispatch(
            session,
            tool_uses,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=Orchestration(parent_session_id="parent", parent_agent_name="coder"),
            gateway=None,
            audit_db_path=audit_db,
        )
    )

    assert session.max_active == 1
    assert [name for name, _ in session.calls] == [
        "musubi_write_file",
        "musubi_append_file",
        "musubi_append_file",
    ]


def test_normalize_tool_result_text_minifies_json() -> None:
    from agent.run import normalize_tool_result_text

    raw = '{\n  "z": 2,\n  "a": [1, 2]\n}\n\n'

    assert normalize_tool_result_text(raw) == '{"z":2,"a":[1,2]}'


def test_normalize_tool_result_text_preserves_retrieve_marker() -> None:
    from agent.run import normalize_tool_result_text

    marker = (
        'summary\n\n[musubi:compressed kind=json ref=abc chars 1000->100; '
        'call musubi_retrieve("abc") for the verbatim original]\n\n'
    )

    assert normalize_tool_result_text(marker).endswith(
        'musubi_retrieve("abc") for the verbatim original]'
    )


def test_dispatch_feeds_normalized_tool_result_to_model() -> None:
    from agent import run as run_mod

    session = _FakeToolSession('{\n  "z": 2,\n  "a": [1, 2]\n}\n\n')

    result = asyncio.run(
        run_mod._dispatch_one(
            {"id": "call-json", "name": "external_json", "input": {}},
            session,
            io.StringIO(),
            vendor=None,
            tools=[],
            orchestration=None,
            gateway=None,
            refused=False,
            compression_db_path=None,
        )
    )

    assert result == '{"z":2,"a":[1,2]}'


def test_dispatch_logs_loaded_skill_id() -> None:
    from agent import run as run_mod

    log = io.StringIO()
    session = _FakeToolSession("---\nname: HTML Dashboard\n---\n")

    result = asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "skill-call",
                "name": "musubi_get_skill",
                "input": {
                    "skill_id": "html-css-dashboard",
                    "agent_name": "root",
                },
            },
            session,
            log,
            vendor=None,
            tools=[],
            orchestration=None,
            gateway=None,
            refused=False,
            compression_db_path=None,
        )
    )

    assert result.startswith("---")
    assert "skill used=html-css-dashboard agent=root" in log.getvalue()


def test_dispatch_does_not_log_skill_used_for_skill_errors() -> None:
    from agent import run as run_mod

    log = io.StringIO()
    session = _FakeToolSession('{"error":"not permitted"}')

    asyncio.run(
        run_mod._dispatch_one(
            {
                "id": "skill-call",
                "name": "musubi_get_skill",
                "input": {
                    "skill_id": "devops",
                    "agent_name": "coder",
                },
            },
            session,
            log,
            vendor=None,
            tools=[],
            orchestration=None,
            gateway=None,
            refused=False,
            compression_db_path=None,
        )
    )

    assert "skill used=" not in log.getvalue()


def test_dispatch_one_records_touched_file_into_active_sink(tmp_path: Path) -> None:
    from agent import run as run_mod

    session = _FakeToolSession('{"status": "ok", "bytes_written": 3}')
    sink: set[str] = set()
    token = run_mod._worker_touched_files.set(sink)
    try:
        result = asyncio.run(
            run_mod._dispatch_one(
                {
                    "id": "c-ok",
                    "name": "musubi_write_file",
                    "input": {"path": "app.py", "content": "x = 1"},
                },
                session,
                io.StringIO(),
                vendor=None,
                tools=[],
                orchestration=None,
                gateway=None,
                role="coder",
                audit_db_path=tmp_path / "audit.db",
            )
        )
    finally:
        run_mod._worker_touched_files.reset(token)

    assert '"ok"' in result
    assert sink == {"app.py"}


def test_system_prompt_states_two_layer_acceptance() -> None:
    from agent.context import build_system_prompt

    prompt = build_system_prompt()
    # C2 — the root is told it owns goal-acceptance and trusts the mechanical
    # verdict rather than re-deriving it.
    assert "[mechanical]" in prompt
    assert "goal" in prompt.lower()
    assert "do not re-run linters" in prompt


def test_replay_elides_large_tool_rows() -> None:
    from agent import run as run_mod

    small = run_mod._elide_replayed_tool_row("short output")
    assert small == "short output"

    big = "A" * (run_mod.REPLAY_TOOL_ROW_MAX_CHARS + 500)
    elided = run_mod._elide_replayed_tool_row(big)
    assert len(elided) < len(big)
    assert "chars elided on replay" in elided

    history = {"messages": [
        {"id": 1, "role": "user", "content": "make a dashboard", "ts": "t"},
        {"id": 2, "role": "tool", "content": big, "ts": "t"},
    ]}
    messages = run_mod._messages_from_chat_history("sys", history)
    tool_msg = messages[-1]["content"]
    assert tool_msg.startswith("[prior tool result]")
    assert "chars elided on replay" in tool_msg
    assert len(tool_msg) < len(big)


def test_log_cycle_includes_human_readable_model_action() -> None:
    from agent import run as run_mod

    log = io.StringIO()

    run_mod._log_cycle(
        log,
        3,
        "tool_use",
        [{"type": "tool_use", "name": "musubi_get_skill"}],
        {"cache_read_input_tokens": 512},
        tokens_out=42,
    )

    line = log.getvalue()
    assert "model_action=tool_calls:read" in line
    assert "stop=tool_use" in line
    assert "tools=1" in line
    assert "names=[get_skill]" in line


def test_log_cycle_names_aggregate_repeated_tools() -> None:
    from agent import run as run_mod

    log = io.StringIO()
    run_mod._log_cycle(
        log,
        1,
        "tool_use",
        [
            {"type": "tool_use", "name": "musubi_grep"},
            {"type": "tool_use", "name": "musubi_grep"},
            {"type": "tool_use", "name": "musubi_read_file"},
        ],
        None,
    )
    line = log.getvalue()
    # A pure read/grep cycle is a verification loop; it should read as one.
    assert "model_action=tool_calls:read" in line
    assert "tools=3" in line
    assert "names=[grep×2, read_file]" in line


def test_model_action_flags_mutation_and_spawn() -> None:
    from agent import run as run_mod

    mutate = run_mod._model_action(
        "tool_use",
        [{"type": "tool_use", "name": "musubi_write_file"},
         {"type": "tool_use", "name": "musubi_grep"}],
    )
    assert mutate == "tool_calls:mutate"

    spawn = run_mod._model_action(
        "tool_use", [{"type": "tool_use", "name": "musubi_spawn_subagent"}],
    )
    assert spawn == "tool_calls:spawn"


def test_log_cycle_is_tagged_with_the_active_worker_label() -> None:
    # O3 — a worker's cycle lines carry its label so multiple "cycle 0" lines
    # from different workers are distinguishable; the root uses the default.
    from agent import run as run_mod

    root_log = io.StringIO()
    run_mod._log_cycle(root_log, 0, "end_turn", [], None)
    assert "[root] cycle 0" in root_log.getvalue()

    worker_log = io.StringIO()
    token = run_mod._worker_log_label.set("coder#483b27c2")
    try:
        run_mod._log_cycle(worker_log, 0, "end_turn", [], None)
    finally:
        run_mod._worker_log_label.reset(token)
    assert "[coder#483b27c2] cycle 0" in worker_log.getvalue()


def test_dropped_tool_target_names_the_discarded_write() -> None:
    # O2 — a truncated write is logged with its target so the drop is traceable.
    from agent import run as run_mod

    named = run_mod._dropped_tool_target(
        {"name": "musubi_write_file", "input": {"path": "dash.html"}}
    )
    assert named == "write_file(dash.html)"
    bare = run_mod._dropped_tool_target({"name": "musubi_spawn_subagent", "input": {}})
    assert bare == "spawn_subagent"


def test_run_loop_elides_large_file_tool_args_before_next_model_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    raw = "<html>" + ("A" * 2400) + "</html>"
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[
                {
                    "type": "tool_use",
                    "id": "append-1",
                    "name": "musubi_append_file",
                    "input": {
                        "path": "dashboard.html",
                        "content": raw,
                        "expected_offset": 0,
                    },
                }
            ],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "dashboard written."}],
        ),
    ])
    session = _FakeToolSession(
        '{"status":"ok","bytes_written":2413,"total_bytes":2413}'
    )

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_append_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=2,
            log=io.StringIO(),
            role="coder",
            audit_db_path=tmp_path / "audit.db",
        )
    )

    assert answer == "dashboard written."
    assert cycles == 2
    assert session.calls == [
        (
            "musubi_append_file",
            {"path": "dashboard.html", "content": raw, "expected_offset": 0},
        )
    ]

    replay = json.dumps(router.calls[1]["messages"])
    assert raw not in replay
    assert "[musubi:elided-tool-arg" in replay
    assert "dashboard.html" in replay


def test_call_with_effort_escalates_on_max_tokens() -> None:
    """A truncated call is retried once at the ceiling."""
    from agent.context import DEFAULT_EFFORT_FLOOR
    from agent.run import EFFORT_CEILING, _call_with_effort

    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[{"type": "text", "text": ""}]),
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    result = _call_with_effort(router, [{"role": "user", "content": "hi"}], [])
    assert result.response.stop_reason == "end_turn"
    assert len(result.attempts) == 2
    assert [c["max_tokens"] for c in router.calls] == [
        DEFAULT_EFFORT_FLOOR,
        EFFORT_CEILING,
    ]


def test_call_with_effort_no_escalation_when_complete() -> None:
    from agent.context import DEFAULT_EFFORT_FLOOR
    from agent.run import _call_with_effort

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    result = _call_with_effort(router, [{"role": "user", "content": "hi"}], [])
    assert result.response.stop_reason == "end_turn"
    assert len(result.attempts) == 1
    assert len(router.calls) == 1
    assert router.calls[0]["max_tokens"] == DEFAULT_EFFORT_FLOOR


def test_run_loop_does_not_dispatch_tool_call_from_max_tokens_response() -> None:
    from agent import run as run_mod

    partial_write = {
        "type": "tool_use",
        "id": "partial-write",
        "name": "musubi_write_file",
        "input": {},
    }
    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[partial_write]),
        LMResponse(stop_reason="max_tokens", content=[partial_write]),
    ])
    session = _FakeToolSession()

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_write_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=1,
            log=io.StringIO(),
            role="coder",
        )
    )

    assert answer is not None
    assert answer.startswith("[blocked] ")
    payload = json.loads(answer.removeprefix("[blocked] "))
    assert payload["status"] == "blocked"
    assert payload["reason"] == "output_too_large_for_single_tool_call"
    assert payload["retry_same_strategy"] is False
    assert payload["attempted_tools"] == ["musubi_write_file"]
    assert "append_chunks" in payload["recommended_strategies"]
    assert "max_tokens" in payload["message"]
    assert cycles == 1
    assert session.calls == []


def test_cycle_token_counts_sum_effort_retry_attempts() -> None:
    from agent import run as run_mod

    attempts = [
        LMResponse(
            stop_reason="max_tokens",
            content=[{"type": "text", "text": "partial"}],
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "final"}],
            usage={
                "input_tokens": 110,
                "output_tokens": 30,
                "cache_read_input_tokens": 80,
            },
        ),
    ]

    assert run_mod._cycle_token_counts(attempts, input_estimate=999) == (
        210,
        50,
        80,
    )


def test_loop_returns_text_when_model_does_not_use_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "no tools needed."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(
        run_agent("ping", router, _musubi_dir(), log=log, max_tokens=0)
    )
    assert answer == "no tools needed."
    assert router.calls[0]["tools"], "expected the MCP tool catalog in the first call"


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
    assert "musubi_spawn_subagent" in names
    assert "musubi_write_file" not in names
    assert "musubi_edit_file" not in names
    assert "musubi_run_command" not in names
    assert "musubi_run_tests" not in names
    assert "musubi_write_stage" not in names
    assert "musubi_read_stage" not in names
    assert "musubi_get_subagent_context" not in names
    assert "musubi_record_agent_cycle" not in names


def test_run_agent_full_tool_surface_keeps_internal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSUBI_TOOL_SURFACE", "full")
    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])

    asyncio.run(
        run_agent("debug", router, _musubi_dir(), log=io.StringIO(), max_tokens=0)
    )

    names = {tool["name"] for tool in router.calls[0]["tools"]}
    assert "musubi_write_file" in names
    assert "musubi_run_command" in names
    assert "musubi_write_stage" in names
    assert "musubi_read_stage" in names


def test_run_agent_persists_and_replays_chat_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))

    first_router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "first answer"}]),
    ])
    first = asyncio.run(
        run_agent(
            "first question",
            first_router,
            _musubi_dir(),
            log=io.StringIO(),
            chat_id="chat-1",
            max_tokens=0,
        )
    )
    assert first == "first answer"

    second_router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "second answer"}]),
    ])
    second = asyncio.run(
        run_agent(
            "second question",
            second_router,
            _musubi_dir(),
            log=io.StringIO(),
            chat_id="chat-1",
            max_tokens=0,
        )
    )
    assert second == "second answer"

    replay = "\n".join(
        str(message.get("content"))
        for message in second_router.calls[0]["messages"]
    )
    assert "first question" in replay
    assert "first answer" in replay
    assert "second question" in replay

    with sqlite3.connect(tmp_path / "data" / "musubi.db") as conn:
        rows = list(conn.execute(
            "SELECT role, content FROM conversation_messages "
            "WHERE chat_id='chat-1' ORDER BY id"
        ))
    assert rows == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]


def test_loop_dispatches_real_tool_and_feeds_result_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "call-1",
                "name": "musubi_new_session",
                "input": {"request": "smoke from agent loop test"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "session opened."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(
        run_agent(
            "open a session",
            router,
            _musubi_dir(),
            log=log,
            max_tokens=0,
            tool_surface="full",
        )
    )
    assert answer == "session opened."
    second_call_messages = router.calls[1]["messages"]
    user_results = [
        m
        for m in second_call_messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert user_results, "expected a user message carrying tool_result blocks"
    blocks = user_results[-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "call-1"
    assert "session_id" in blocks[0]["content"], "musubi_new_session must return a session_id"


def test_loop_aborts_after_max_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    looping_response = LMResponse(
        stop_reason="tool_use",
        content=[{
            "type": "tool_use",
            "id": "x",
            "name": "musubi_get_active_session",
            "input": {},
        }],
    )
    router = FakeRouter([looping_response, looping_response, looping_response])
    log = io.StringIO()
    answer = asyncio.run(run_agent(
        "loop forever", router, _musubi_dir(), max_cycles=2, log=log,
        max_tokens=0,
    ))

    assert "incomplete" in answer.lower()
    assert "2 cycles" in answer


def test_loop_passes_tool_error_to_model_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "bad-call",
                "name": "musubi_get_active_session",
                "input": {"nonexistent_param": True, "another": [1, 2, 3]},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "ack."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent(
        "bad tool", router, _musubi_dir(), log=log, max_tokens=0,
    ))
    assert answer == "ack."
    assert len(router.calls) == 2, "loop should have completed both cycles"


def test_root_system_prompt_includes_scope_hint_for_simple_task() -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "ok"}],
        )
    ])

    answer = asyncio.run(run_agent(
        "Update weather-dashboard.html to refresh every 5 minutes",
        router,
        _musubi_dir(),
        log=io.StringIO(),
        max_tokens=0,
    ))

    assert answer == "ok"
    system_text = router.calls[0]["messages"][0]["content"]
    assert "[agent-routing-scope]" in system_text
    assert "scope=simple_edit" in system_text
    assert "route=single_coder" in system_text
    assert "max_workers=1" in system_text


def test_spawn_overflow_uses_flat_cap_regardless_of_scope() -> None:
    # D2a — classify_task is advisory. A "simple" scope no longer tightens the
    # coder cap to one; the only enforcement is the flat per-role width cap (3),
    # so the 4th coder in a batch is the first refused.
    from agent import run as run_mod
    from agent.scope import classify_task

    simple = classify_task("Update weather-dashboard.html to refresh every 5 minutes")
    tool_uses = [
        {"id": f"s{i}", "name": "musubi_spawn_subagent", "input": {"role": "coder"}}
        for i in range(4)
    ]

    overflow = run_mod._spawn_overflow_reasons(
        tool_uses, io.StringIO(), role="agent", scope_hint=simple, cycle_index=0,
    )

    assert list(overflow) == ["s3"]
    assert "per-turn spawn cap (3)" in overflow["s3"]


def test_spawn_overflow_no_longer_forces_planner_before_coder() -> None:
    # D1 — a coder as the first worker of a medium-scope turn is no longer
    # refused; plan-first is opt-in via --plan, not a keyword guess.
    from agent import run as run_mod
    from agent.scope import classify_task

    medium = classify_task("Improve the dashboard weather display")
    tool_uses = [
        {"id": "c1", "name": "musubi_spawn_subagent",
         "input": {"role": "coder", "brief": "implement"}},
    ]

    overflow = run_mod._spawn_overflow_reasons(
        tool_uses, io.StringIO(), role="agent", scope_hint=medium, cycle_index=0,
    )

    assert overflow == {}


def test_plan_first_directive_injected_into_system_prompt() -> None:
    # D2b — --plan appends an explicit plan-first directive to the root prompt.
    from agent import run as run_mod
    from agent.context import build_system_prompt

    assert "planner" in run_mod._PLAN_FIRST_DIRECTIVE.lower()
    combined = f"{build_system_prompt('scope')}\n\n{run_mod._PLAN_FIRST_DIRECTIVE}"
    assert "--plan" in combined
    assert "plan-first" in combined.lower()


def test_delete_request_returns_manual_answer_without_llm_calls() -> None:
    router = FakeRouter([])
    log = io.StringIO()

    answer = asyncio.run(run_agent(
        "delete all *-dashboard.html files",
        router,
        _musubi_dir(),
        log=log,
        max_tokens=0,
    ))

    assert router.calls == []
    assert "I cannot safely delete files from this route" in answer
    assert "*-dashboard.html" in answer
    assert "manual_destructive" in log.getvalue()


def test_greeting_returns_direct_answer_without_llm_calls() -> None:
    router = FakeRouter([])
    log = io.StringIO()

    answer = asyncio.run(run_agent(
        "hi",
        router,
        _musubi_dir(),
        log=log,
        max_tokens=0,
    ))

    assert router.calls == []
    assert answer.startswith("Hi!")
    assert "direct_answer" in log.getvalue()


class _ExplodingRouter(LMRouter):
    """A vendor whose call fails like a real network/proxy error would."""

    name = "boom"
    model = "boom-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001, ARG002
        raise RuntimeError("curl exited 56 ... 407 proxy auth required")


def test_resolve_vendor_labels_which_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_resolve_vendor` returns a human label of how the endpoint was picked,
    so the startup log can show which profile is in effect."""
    from agent import run as run_mod

    cfg = tmp_path / ".musubi" / "llm.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({
        "default": "ollama.local",
        "ollama": {"local": {"model": "llama3.1"}},
    }), encoding="utf-8")
    monkeypatch.setenv("MUSUBI_LLM_CONFIG", str(cfg))
    # Avoid importing a real vendor SDK — only the label logic is under test.
    monkeypatch.setattr(run_mod, "build_from_profile", lambda prof: "ROUTER")

    _, default_src = run_mod._resolve_vendor(None)
    assert default_src == "ollama.local (llm.json default)"

    _, profile_src = run_mod._resolve_vendor("ollama.local")
    assert profile_src == "ollama.local (--profile)"


def test_vendor_error_surfaces_clean_not_as_exception_group() -> None:
    """A vendor.call failure inside the loop must reach the caller as a plain
    RuntimeError with the underlying message — NOT anyio's BaseExceptionGroup
    wall raised at AsyncExitStack teardown (the Windows curl-407 traceback)."""
    log = io.StringIO()
    with pytest.raises(RuntimeError, match="407 proxy auth") as ei:
        asyncio.run(
            run_agent(
                "summarize repository architecture",
                _ExplodingRouter(),
                _musubi_dir(),
                log=log,
                max_tokens=0,
            )
        )
    # The message is a clean one-liner, not a nested group dump.
    assert not isinstance(ei.value, BaseExceptionGroup)
