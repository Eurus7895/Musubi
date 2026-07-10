"""C1 — deterministic mechanical validation gate at the worker boundary.

The harness records the files a worker actually wrote (via a ContextVar the
dispatch loop appends to), runs a real validator over exactly the files that
still exist, and hands the parent a `result` (pass/fail/error/skipped) so the
goal-holding root accepts from a trustworthy signal rather than re-deriving it.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from agent import run as run_mod
from agent import subagent


# ── _tool_wrote_ok (the collector's success predicate) ──────────────────────


def test_tool_wrote_ok_detects_status() -> None:
    assert run_mod._tool_wrote_ok('{"status": "ok", "bytes_written": 3}') is True
    assert run_mod._tool_wrote_ok('{"status": "error", "error": "denied"}') is False
    assert run_mod._tool_wrote_ok("not json") is False
    assert run_mod._tool_wrote_ok("[1, 2]") is False


def test_record_touched_file_only_when_collecting() -> None:
    run_mod._record_touched_file(
        "musubi_write_file", {"path": "a.py"}, '{"status": "ok"}'
    )  # no sink installed → inert, must not raise

    sink: set[str] = set()
    token = run_mod._worker_touched_files.set(sink)
    try:
        run_mod._record_touched_file(
            "musubi_write_file", {"path": "app.py"}, '{"status": "ok"}'
        )
        run_mod._record_touched_file(
            "musubi_write_file", {"path": "bad.py"}, '{"status": "error"}'
        )
        run_mod._record_touched_file(
            "musubi_read_file", {"path": "x.py"}, '{"status": "ok"}'
        )
    finally:
        run_mod._worker_touched_files.reset(token)
    assert sink == {"app.py"}


# ── _mechanical_line (what the root reads) ──────────────────────────────────


def test_mechanical_line_pass() -> None:
    line = subagent._mechanical_line({
        "result": "pass", "validator": "ruff", "errors": [], "detail": None,
        "files_touched": ["a.py", "b.py"], "artifact_path": None,
    })
    assert line == "[mechanical] result=pass validator=ruff files=2"


def test_mechanical_line_fail_shows_errors() -> None:
    line = subagent._mechanical_line({
        "result": "fail", "validator": "ruff", "errors": ["F401 unused import os"],
        "detail": None, "files_touched": ["a.py"], "artifact_path": "a.py",
    })
    assert "result=fail" in line
    assert "artifact=a.py" in line
    assert "errors=F401 unused import os" in line


def test_mechanical_line_skipped_shows_reason() -> None:
    line = subagent._mechanical_line({
        "result": "skipped", "validator": "none", "errors": [],
        "detail": "no lintable files", "files_touched": ["dash.html"],
        "artifact_path": "dash.html",
    })
    assert "result=skipped" in line
    assert "artifact=dash.html" in line
    assert "reason='no lintable files'" in line


# ── _run_mechanical_gate — result semantics ─────────────────────────────────


def _stub_exists(monkeypatch, value: bool = True) -> None:
    monkeypatch.setattr("agent.subagent._file_still_exists", lambda p: value)


def test_gate_pass_on_clean_lint(monkeypatch) -> None:
    _stub_exists(monkeypatch)
    seen: dict[str, object] = {}

    async def fake_call(session, name, args):
        seen["files"] = args["files"]
        return '{"passed": true, "errors": []}'

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(object(), {"b.py", "a.py"}, io.StringIO())
    )
    assert seen["files"] == ["a.py", "b.py"]  # sorted, deterministic
    assert gate["result"] == "pass"
    assert gate["validator"] == "ruff"


def test_gate_fail_carries_errors_error_does_not(monkeypatch) -> None:
    _stub_exists(monkeypatch)

    async def fake_fail(session, name, args):
        return '{"passed": false, "errors": [{"code": "F401", "message": "unused import os"}]}'

    monkeypatch.setattr("agent.run._call_tool_text", fake_fail)
    g = asyncio.run(subagent._run_mechanical_gate(object(), {"a.py"}, io.StringIO()))
    assert g["result"] == "fail"
    assert g["errors"] == ["F401 unused import os"]

    # ruff ran but produced no structured errors → could not lint, NOT a failure.
    async def fake_error(session, name, args):
        return '{"passed": false, "errors": []}'

    monkeypatch.setattr("agent.run._call_tool_text", fake_error)
    g2 = asyncio.run(subagent._run_mechanical_gate(object(), {"a.py"}, io.StringIO()))
    assert g2["result"] == "error"


def test_gate_skips_when_no_lintable_files(monkeypatch) -> None:
    _stub_exists(monkeypatch)

    async def fake_call(session, name, args):  # pragma: no cover - must not run
        raise AssertionError("lint must not run for a non-python artifact")

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(object(), {"dashboard.html"}, io.StringIO())
    )
    assert gate["result"] == "skipped"
    assert gate["validator"] == "none"
    assert gate["artifact_path"] == "dashboard.html"


# ── G1 — deleted scratch files are filtered out (the PR #135 regression) ─────


def test_gate_filters_deleted_scratch_file(tmp_path: Path, monkeypatch) -> None:
    real = tmp_path / "app.py"
    real.write_text("x = 1\n")
    deleted = tmp_path / "build_gen.py"  # written then deleted → never on disk

    async def fake_call(session, name, args):
        # Only the surviving file reaches the linter; the deleted one is gone.
        assert args["files"] == [str(real)]
        return '{"passed": true, "errors": []}'

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(
            object(), {str(real), str(deleted)}, io.StringIO()
        )
    )
    assert gate["result"] == "pass"
    assert gate["files_touched"] == [str(real)]


def test_gate_skipped_when_all_writes_deleted(tmp_path: Path, monkeypatch) -> None:
    # The generator-script workflow: the only tracked write is deleted, and the
    # real artifact was produced by a subprocess (invisible to the collector).
    # The gate must stay silent, not emit a false failure.
    gone = tmp_path / "build_gen.py"  # never created

    async def fake_call(session, name, args):  # pragma: no cover - must not run
        raise AssertionError("lint must not run when nothing survives")

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    gate = asyncio.run(
        subagent._run_mechanical_gate(object(), {str(gone)}, io.StringIO())
    )
    assert gate["result"] == "skipped"
    assert "no surviving files" in gate["detail"]
