"""Policy verdict provenance stays durable and append-only.

musubi-tier: substrate test — policy evidence is a governance boundary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent.boundary import PolicyDecision, record_policy_decision


def test_policy_audit_persists_request_and_parent_session_identity(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.db"

    record_policy_decision(
        PolicyDecision("ALLOW", "root", "musubi_glob", "read-only"),
        db_path=audit_path,
        request_id="request-42",
        parent_session_id="session-42",
    )

    with sqlite3.connect(audit_path) as conn:
        row = conn.execute(
            "SELECT request_id, parent_session_id FROM policy_audit"
        ).fetchone()
    assert row == ("request-42", "session-42")


def test_policy_audit_adds_identity_columns_without_rewriting_legacy_rows(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.db"
    with sqlite3.connect(audit_path) as conn:
        conn.execute(
            "CREATE TABLE policy_audit ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
            "verdict TEXT NOT NULL, tool TEXT NOT NULL, role TEXT NOT NULL, "
            "handle TEXT, reason TEXT)"
        )
        conn.execute(
            "INSERT INTO policy_audit(ts, verdict, tool, role) "
            "VALUES (1, 'ALLOW', 'musubi_glob', 'root')"
        )

    record_policy_decision(
        PolicyDecision("DENY", "root", "musubi_write_file", "blocked"),
        db_path=audit_path,
        request_id="request-new",
        parent_session_id="session-new",
    )

    with sqlite3.connect(audit_path) as conn:
        rows = conn.execute(
            "SELECT request_id, parent_session_id FROM policy_audit ORDER BY id"
        ).fetchall()
    assert rows == [(None, None), ("request-new", "session-new")]
