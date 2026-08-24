from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import audit_report


def _seed_state(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE agent_turns (
                parent_session_id TEXT, chat_id TEXT, request_id TEXT,
                started_at TEXT, ended_at TEXT, model_family TEXT,
                root_triage TEXT, cycles INTEGER, tokens_in_estimate INTEGER,
                tokens_out_estimate INTEGER, delivered_artifact INTEGER
            );
            CREATE TABLE sessions (session_id TEXT, request TEXT);
            CREATE TABLE session_folder_grants (
                chat_id TEXT, alias TEXT, canonical_path TEXT, ordinal INTEGER
            );
            CREATE TABLE pipeline_runs (session_id TEXT, request_id TEXT);
            CREATE TABLE sub_sessions (
                handle_id TEXT, parent_session_id TEXT, role TEXT, status TEXT,
                escalated INTEGER, turns INTEGER, max_turns INTEGER,
                pushed_skill_id TEXT, brief TEXT, result_summary TEXT,
                turn_cap_acceptance TEXT, created_at TEXT
            );
            CREATE TABLE agent_cycles (
                session_id TEXT, worker_id TEXT, cycle_status TEXT,
                tokens_in INTEGER, cached_input_tokens INTEGER,
                tokens_out INTEGER, lm_ms INTEGER
            );
            CREATE TABLE audit_obligations (
                handle_id TEXT, kind TEXT, status TEXT, error TEXT
            );
        """)
        conn.execute(
            "INSERT INTO agent_turns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("root", "chat", "request-1", "start", "end", "model", "pipeline",
             1, 10, 5, 1),
        )
        conn.execute("INSERT INTO sessions VALUES (?,?)", ("root", "build"))
        conn.execute(
            "INSERT INTO pipeline_runs VALUES (?,?)", ("pipeline", "request-1")
        )
        conn.execute(
            "INSERT INTO sub_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("worker", "pipeline", "coder", "done", 0, 1, 8, "python",
             "code", "done", None, "now"),
        )
        conn.execute(
            "INSERT INTO agent_cycles VALUES (?,?,?,?,?,?,?)",
            ("pipeline", "worker", "final", 100, 0, 20, 5),
        )
        conn.execute(
            "INSERT INTO audit_obligations VALUES (?,?,?,?)",
            ("unrelated", "spawn", "pending", "other run"),
        )


def _seed_audit(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE tool_audit (
                id INTEGER PRIMARY KEY, session_id TEXT, agent TEXT, tool TEXT,
                args_json TEXT, status TEXT
            );
            CREATE TABLE policy_audit (
                parent_session_id TEXT, role TEXT, tool TEXT, reason TEXT,
                verdict TEXT
            );
        """)
        conn.execute(
            "INSERT INTO tool_audit VALUES (?,?,?,?,?,?)",
            (1, "pipeline", "coder", "musubi_write_file",
             '{"path":"app.py"}', "ok"),
        )


def test_report_traverses_pipeline_sessions_and_scopes_obligations(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    state = tmp_path / "state.db"
    audit = tmp_path / "audit.db"
    _seed_state(state)
    _seed_audit(audit)
    monkeypatch.setattr(audit_report, "_STATE_DB", state)
    monkeypatch.setattr(audit_report, "_AUDIT_DB", audit)

    assert audit_report.report(session="root") == 0
    output = capsys.readouterr().out

    assert "worker" in output
    assert "musubi_write_file" in output
    assert "120 tokens" in output
    assert "other run" not in output
