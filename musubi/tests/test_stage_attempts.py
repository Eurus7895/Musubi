from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from storage import db


def _session(path: Path) -> str:
    db.init_db(path)
    db.insert_session("s1", "request", "2026-08-01T00:00:00+00:00", path)
    db.insert_stage("s1", "build-ui", 1, path)
    return "s1"


def test_arbitrary_stage_transitions_append_an_event(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _session(path)
    identity = db.StageAttemptIdentity("s1", "build-ui", 1)

    row = db.transition_stage_attempt(
        identity, "pending", "preflight_running", "preflight_started",
        {"source": "driver"}, db_path=path,
    )

    assert row["phase"] == "preflight_running"
    events = db.get_stage_attempt_events(identity, db_path=path)
    assert [(event["event"], event["detail"]) for event in events] == [
        ("preflight_started", {"source": "driver"}),
    ]


def test_transition_is_compare_and_swap(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _session(path)
    identity = db.StageAttemptIdentity("s1", "build-ui", 1)
    db.transition_stage_attempt(
        identity, "pending", "preflight_running", "started", {}, db_path=path,
    )
    with pytest.raises(ValueError, match="expected phase"):
        db.transition_stage_attempt(
            identity, "pending", "contract_frozen", "bad", {}, db_path=path,
        )


def test_next_attempt_rejects_a_stale_writer(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _session(path)
    identity = db.StageAttemptIdentity("s1", "build-ui", 1)
    assert db.create_next_stage_attempt(identity, 1, {"retry": True}, db_path=path) == 2
    with pytest.raises((ValueError, sqlite3.IntegrityError), match="attempt|stale"):
        db.create_next_stage_attempt(identity, 1, {"retry": True}, db_path=path)


def test_attempt_payload_fields_are_write_once(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _session(path)
    identity = db.StageAttemptIdentity("s1", "build-ui", 1)
    db.write_stage_attempt_once(
        identity, "contract_json", '{"goal":"done"}', db_path=path,
    )
    with pytest.raises(ValueError, match="write-once"):
        db.write_stage_attempt_once(
            identity, "contract_json", '{"goal":"changed"}', db_path=path,
        )


def test_partial_unique_indexes_cover_chunked_and_non_chunked(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _session(path)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_stage("s1", "build-ui", 1, path)
    db.insert_stage("s1", "build-ui", 1, path, chunk_id="T1")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_stage("s1", "build-ui", 1, path, chunk_id="T1")
