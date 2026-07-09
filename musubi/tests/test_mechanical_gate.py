"""C1 — deterministic mechanical validation gate at the worker boundary.

The harness records the files a worker actually wrote (via a ContextVar the
dispatch loop appends to), runs a real validator over exactly those files, and
hands the verdict to the parent so the goal-holding root accepts the mechanical
layer from a trustworthy signal rather than re-deriving it.
"""

from __future__ import annotations

import asyncio
import io

from agent import run as run_mod
from agent import subagent


# ── _tool_wrote_ok (the collector's success predicate) ──────────────────────


def test_tool_wrote_ok_detects_status() -> None:
    assert run_mod._tool_wrote_ok('{"status": "ok", "bytes_written": 3}') is True
    assert run_mod._tool_wrote_ok('{"status": "error", "error": "denied"}') is False
    assert run_mod._tool_wrote_ok("not json") is False
    assert run_mod._tool_wrote_ok("[1, 2]") is False


# ── _record_touched_file (ContextVar collector) ─────────────────────────────


def test_record_touched_file_only_when_collecting() -> None:
    # No sink installed → inert (root worker / non-spawned call).
    run_mod._record_touched_file(
        "musubi_write_file", {"path": "a.py"}, '{"status": "ok"}'
    )  # must not raise

    sink: set[str] = set()
    token = run_mod._worker_touched_files.set(sink)
    try:
        run_mod._record_touched_file(
            "musubi_write_file", {"path": "app.py"}, '{"status": "ok"}'
        )
        run_mod._record_touched_file(
            "musubi_edit_file", {"path": "app.py"}, '{"status": "ok"}'
        )
        # A failed write is not recorded.
        run_mod._record_touched_file(
            "musubi_write_file", {"path": "bad.py"}, '{"status": "error"}'
        )
        # A read tool is not a mutation.
        run_mod._record_touched_file(
            "musubi_read_file", {"path": "x.py"}, '{"status": "ok"}'
        )
    finally:
        run_mod._worker_touched_files.reset(token)

    assert sink == {"app.py"}


# ── _mechanical_line (what the root reads) ──────────────────────────────────


def test_mechanical_line_python_pass() -> None:
    line = subagent._mechanical_line({
        "validator": "ruff", "validator_exit": 0,
        "files_touched": ["a.py", "b.py"], "artifact_path": None,
    })
    assert line == "[mechanical] validator=ruff exit=0 files=2"


def test_mechanical_line_skipped_with_artifact() -> None:
    line = subagent._mechanical_line({
        "validator": "none", "validator_exit": None,
        "files_touched": ["dash.html"], "artifact_path": "dash.html",
    })
    assert "exit=skipped" in line
    assert "files=1" in line
    assert "artifact=dash.html" in line


# ── _run_mechanical_gate (deterministic verdict from the tool) ──────────────


def test_gate_lints_python_and_derives_exit(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_call(session, name, args):
        seen["name"] = name
        seen["files"] = args["files"]
        return '{"passed": true, "errors": []}'

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(object(), {"b.py", "a.py"}, io.StringIO())
    )

    assert seen["name"] == "musubi_run_lint"
    assert seen["files"] == ["a.py", "b.py"]  # sorted, deterministic
    assert gate["validator"] == "ruff"
    assert gate["validator_exit"] == 0
    assert gate["files_touched"] == ["a.py", "b.py"]


def test_gate_failing_lint_yields_nonzero_exit(monkeypatch) -> None:
    async def fake_call(session, name, args):
        return '{"passed": false, "errors": [{"code": "F401"}]}'

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(object(), {"a.py"}, io.StringIO())
    )
    assert gate["validator_exit"] == 1


def test_gate_skips_when_no_lintable_files(monkeypatch) -> None:
    async def fake_call(session, name, args):  # pragma: no cover - must not run
        raise AssertionError("lint must not run for a non-python artifact")

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(object(), {"dashboard.html"}, io.StringIO())
    )
    assert gate["validator"] == "none"
    assert gate["validator_exit"] is None
    assert gate["artifact_path"] == "dashboard.html"
