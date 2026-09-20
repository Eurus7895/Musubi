"""Durable Adaptive decisions and public run reconstruction."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage import db
from storage.adaptive_runs import AdaptiveRunStore


def _store(tmp_path: Path) -> tuple[AdaptiveRunStore, str]:
    path = tmp_path / "adaptive.db"
    store = AdaptiveRunStore(path)
    session_id = "adaptive-run-1"
    db.insert_session(session_id, "build verified output", "2026-09-19T00:00:00Z", path)
    return store, session_id


def test_decisions_are_append_only_and_reconstruct_after_reopen(tmp_path: Path) -> None:
    store, session_id = _store(tmp_path)
    first = store.record_decision(
        session_id=session_id,
        state_revision=0,
        request_id="decision-1",
        request_hash="hash-1",
        provider_id="llm",
        status="selected",
        candidate_id="collect",
        legal_actions=("collect", "think"),
        confidence={"collect": 0.8, "think": 0.2},
    )
    second = store.record_decision(
        session_id=session_id,
        state_revision=1,
        request_id="decision-2",
        request_hash="hash-2",
        provider_id="llm",
        status="abstained",
        candidate_id=None,
        legal_actions=("execute", "think"),
    )

    reopened = AdaptiveRunStore(tmp_path / "adaptive.db").inspect(session_id)
    assert (first.sequence, second.sequence) == (1, 2)
    assert [item.request_id for item in reopened.decisions] == ["decision-1", "decision-2"]
    assert reopened.decisions[0].confidence == {"collect": 0.8, "think": 0.2}


def test_store_rejects_rewritten_request_and_stale_state(tmp_path: Path) -> None:
    store, session_id = _store(tmp_path)
    common = {
        "session_id": session_id,
        "request_id": "decision-1",
        "request_hash": "hash-1",
        "provider_id": "llm",
        "status": "selected",
        "candidate_id": "think",
        "legal_actions": ("think",),
    }
    store.record_decision(state_revision=2, **common)

    with pytest.raises(ValueError, match="rewritten"):
        store.record_decision(state_revision=2, **{**common, "request_hash": "changed"})
    with pytest.raises(ValueError, match="stale"):
        store.record_decision(
            state_revision=1,
            **{**common, "request_id": "decision-2", "request_hash": "hash-2"},
        )


def test_decision_schema_fails_closed(tmp_path: Path) -> None:
    store, session_id = _store(tmp_path)
    with pytest.raises(ValueError, match="nonselected"):
        store.record_decision(
            session_id=session_id,
            state_revision=0,
            request_id="decision-1",
            request_hash="hash-1",
            provider_id="llm",
            status="abstained",
            candidate_id="execute",
            legal_actions=("execute",),
        )
    with pytest.raises(ValueError, match="\[0, 1\]"):
        store.record_decision(
            session_id=session_id,
            state_revision=0,
            request_id="decision-2",
            request_hash="hash-2",
            provider_id="llm",
            status="selected",
            candidate_id="execute",
            legal_actions=("execute",),
            confidence={"execute": 1.1},
        )
