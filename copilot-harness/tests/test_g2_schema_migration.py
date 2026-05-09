"""Phase G.2 — schema-migration round-trip tests.

The G.2 acceptance criterion:
  - v1 stage output loads through v2 reader cleanly via migration rule.
  - Audit log row exists per migration applied.
  - Migration is idempotent — second read of the same row doesn't
    re-run the migration (storage already upgraded after first read).

Plus tightness checks: explicit schema-version round-trip, the
reviewer's `category` requirement, the migration registry's lookups.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from session import state
from storage import db
from validation import schema_migrations, verifier


@pytest.fixture
def fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    db.init_db(p)
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", p)
    return p


# ── Schema-version field on stage_outputs ──────────────────────────────────


def test_init_db_adds_schema_version_column(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stage_outputs)")}
    assert "schema_version" in cols


def test_create_session_tags_rows_with_current_version(fresh_db: Path) -> None:
    sid = state.create_session("do x", fresh_db)
    with sqlite3.connect(fresh_db) as conn:
        rows = conn.execute(
            "SELECT stage, schema_version FROM stage_outputs WHERE session_id = ?",
            (sid,),
        ).fetchall()
    assert len(rows) == 4
    for _, version in rows:
        assert version == verifier.CURRENT_SCHEMA_VERSION


def test_pre_g2_db_with_v1_rows_is_migrated_in_place(tmp_path: Path) -> None:
    """Simulate a pre-G.2 DB: stage_outputs without schema_version.
    init_db's ALTER TABLE migration adds the column with DEFAULT 'v1';
    existing rows backfill cleanly."""
    p = tmp_path / "old.db"
    with sqlite3.connect(p) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, request TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE stage_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                output TEXT, written_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO sessions VALUES ('s1','do thing','active','now','now')"
        )
        conn.execute(
            "INSERT INTO stage_outputs (session_id, stage, attempt, output)"
            " VALUES ('s1', 'review', 1, '{\"status\":\"pass\"}')"
        )

    db.init_db(p)

    with sqlite3.connect(p) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stage_outputs)")}
        version = conn.execute(
            "SELECT schema_version FROM stage_outputs WHERE session_id='s1'"
        ).fetchone()[0]
    assert "schema_version" in cols
    assert version == "v1"


# ── schema_migrations module ───────────────────────────────────────────


def test_migrate_no_op_when_versions_match() -> None:
    data = {"status": "pass", "issues": []}
    out = schema_migrations.migrate("reviewer", data, "v2", "v2", audit=False)
    assert out == data


def test_migrate_reviewer_v1_to_v2_adds_category_other() -> None:
    data = {
        "status": "fail",
        "issues": [
            {"severity": "high", "description": "x", "fix_instruction": "y"},
            {"severity": "low",  "description": "z", "fix_instruction": "w"},
        ],
    }
    out = schema_migrations.migrate("reviewer", data, "v1", "v2", audit=False)
    for issue in out["issues"]:
        assert issue["category"] == "other"


def test_migrate_reviewer_v1_to_v2_keeps_existing_category() -> None:
    """If a v1 issue already has `category` (unlikely but tolerated),
    don't clobber it."""
    data = {
        "status": "fail",
        "issues": [
            {"severity": "high", "category": "security",
             "description": "x", "fix_instruction": "y"},
        ],
    }
    out = schema_migrations.migrate("reviewer", data, "v1", "v2", audit=False)
    assert out["issues"][0]["category"] == "security"


def test_migrate_planner_designer_coder_v1_to_v2_are_identity() -> None:
    """Phase G.2 only changes reviewer's schema. Other agents' v2
    matches v1; the migration is registered as identity for chain
    completeness."""
    payload = {"summary": "x", "tasks": [{"id": "T1"}]}
    for agent in ("planner", "designer", "coder"):
        out = schema_migrations.migrate(agent, payload, "v1", "v2", audit=False)
        assert out == payload


def test_migrate_raises_for_unknown_path() -> None:
    with pytest.raises(ValueError, match="No migration path"):
        schema_migrations.migrate("reviewer", {}, "v1", "v99", audit=False)


def test_migrate_does_not_mutate_input() -> None:
    data = {
        "status": "fail",
        "issues": [{"severity": "high", "description": "x", "fix_instruction": "y"}],
    }
    snapshot = {**data, "issues": [dict(data["issues"][0])]}
    schema_migrations.migrate("reviewer", data, "v1", "v2", audit=False)
    assert data["issues"][0] == snapshot["issues"][0]


# ── End-to-end: write v1, read with v2 reader, audit row exists ─────────


def test_v1_reviewer_row_migrates_on_read_and_writes_audit(fresh_db: Path) -> None:
    """Plant a v1-tagged row directly, then call state.read_stage. The
    reader must run the v1→v2 migration, persist the upgraded version,
    and write an audit row to schema_migrations."""
    sid = state.create_session("do thing", fresh_db)

    # Replace the v2-tagged review row with a v1-tagged one carrying
    # legacy-shape (no `category`) issues + status='fail'.
    legacy_output = {
        "status": "fail",
        "attempt": 1,
        "issues": [
            {"severity": "high", "description": "no auth", "fix_instruction": "add JWT"},
        ],
        "escalate_reason": None,
    }
    import json as _json
    with sqlite3.connect(fresh_db) as conn:
        conn.execute(
            "UPDATE stage_outputs SET output = ?, status = 'complete',"
            " written_at = 'now', schema_version = 'v1'"
            " WHERE session_id = ? AND stage = 'review'",
            (_json.dumps(legacy_output), sid),
        )

    out = state.read_stage(sid, "review", fresh_db)
    assert isinstance(out, dict)
    # category was backfilled by the migration.
    assert out["issues"][0]["category"] == "other"

    # Audit row exists.
    rows = db.query_schema_migrations(session_id=sid, db_path=fresh_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "reviewer"
    assert row["from_version"] == "v1"
    assert row["to_version"] == "v2"
    assert row["success"] == 1


def test_second_read_does_not_re_migrate(fresh_db: Path) -> None:
    """First read upgrades the row's schema_version. Second read sees
    stored == current and runs zero migrations — no extra audit row."""
    sid = state.create_session("do thing", fresh_db)
    import json as _json
    with sqlite3.connect(fresh_db) as conn:
        conn.execute(
            "UPDATE stage_outputs SET output = ?, status = 'complete',"
            " written_at = 'now', schema_version = 'v1'"
            " WHERE session_id = ? AND stage = 'review'",
            (_json.dumps({
                "status": "fail", "attempt": 1, "issues": [],
                "escalate_reason": None,
            }), sid),
        )

    state.read_stage(sid, "review", fresh_db)
    state.read_stage(sid, "review", fresh_db)
    rows = db.query_schema_migrations(session_id=sid, db_path=fresh_db)
    assert len(rows) == 1, f"expected 1 audit row, got {len(rows)}"


def test_post_migration_row_has_current_version(fresh_db: Path) -> None:
    sid = state.create_session("do thing", fresh_db)
    import json as _json
    with sqlite3.connect(fresh_db) as conn:
        conn.execute(
            "UPDATE stage_outputs SET output = ?, status = 'complete',"
            " written_at = 'now', schema_version = 'v1'"
            " WHERE session_id = ? AND stage = 'review'",
            (_json.dumps({
                "status": "pass", "attempt": 1, "issues": [],
                "escalate_reason": None,
            }), sid),
        )
    state.read_stage(sid, "review", fresh_db)
    with sqlite3.connect(fresh_db) as conn:
        version = conn.execute(
            "SELECT schema_version FROM stage_outputs"
            " WHERE session_id = ? AND stage = 'review'",
            (sid,),
        ).fetchone()[0]
    assert version == verifier.CURRENT_SCHEMA_VERSION


# ── verifier v2 contract ──────────────────────────────────────────────


def test_verifier_rejects_reviewer_issue_without_category() -> None:
    """v2 reviewer schema requires `category` on every issue."""
    output = {
        "status": "fail",
        "attempt": 1,
        "issues": [{
            "severity": "high",
            "description": "no auth",
            "fix_instruction": "add JWT",
            # `category` deliberately absent
        }],
    }
    result = verifier.validate(output, "reviewer")
    assert result.valid is False
    assert any("category" in e for e in result.errors)


def test_verifier_rejects_unknown_category() -> None:
    output = {
        "status": "fail",
        "attempt": 1,
        "issues": [{
            "severity": "high",
            "category": "vibes",  # not in REVIEWER_CATEGORY_ENUM
            "description": "x",
            "fix_instruction": "y",
        }],
    }
    result = verifier.validate(output, "reviewer")
    assert result.valid is False
    assert any("category" in e for e in result.errors)


def test_verifier_accepts_each_known_category() -> None:
    for cat in verifier.REVIEWER_CATEGORY_ENUM:
        output = {
            "status": "fail" if cat != "style" else "pass",
            "attempt": 1,
            "issues": [{
                "severity": "high" if cat != "style" else "low",
                "category": cat,
                "description": "x",
                "fix_instruction": "y",
            }],
        }
        result = verifier.validate(output, "reviewer")
        assert result.valid is True, f"category={cat!r}: {result.errors}"


def test_schemas_for_version_returns_v1_and_v2() -> None:
    """Lock the version → schema map so a future bump can't silently
    drop v1 (which old DB rows might still need)."""
    v1 = verifier.schemas_for_version("v1")
    v2 = verifier.schemas_for_version("v2")
    # v1 reviewer doesn't require category; v2 does.
    assert v1["reviewer"].get("issue_category_required", False) is False
    assert v2["reviewer"]["issue_category_required"] is True


def test_schemas_for_version_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        verifier.schemas_for_version("v99")
