"""Adaptive actions become executable only after validation and persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.adaptive_controller import AdaptiveController
from agent.decision_contracts import (
    ActionKind,
    Candidate,
    Decision,
    DecisionRequest,
    DecisionState,
    DecisionStatus,
    InvalidDecision,
)
from storage import db
from storage.adaptive_runs import AdaptiveRunStore


def _request(run_id: str) -> DecisionRequest:
    return DecisionRequest(
        "request-1",
        DecisionState(run_id, "unit-1", 3, "contract-1", "context-1"),
        (
            Candidate.create("collect", ActionKind.COLLECT, {"source": "tests"}),
            Candidate.create("think", ActionKind.THINK, {"brief_ref": "gap-1"}),
        ),
        (ActionKind.COLLECT, ActionKind.THINK),
        500,
    )


def _store(tmp_path: Path, run_id: str) -> AdaptiveRunStore:
    path = tmp_path / "adaptive.db"
    store = AdaptiveRunStore(path)
    db.insert_session(run_id, "fix verified failure", "2026-09-19T00:00:00Z", path)
    return store


def test_controller_persists_valid_selection_before_returning_it(tmp_path: Path) -> None:
    request = _request("run-1")

    class Provider:
        def select(self, current: DecisionRequest) -> Decision:
            return Decision(
                current.request_id, current.request_hash, "fake",
                DecisionStatus.SELECTED, "collect",
            )

    store = _store(tmp_path, request.state.run_id)
    outcome = AdaptiveController(Provider(), store).select(
        request, current_state=request.state,
    )

    assert outcome.candidate == request.candidates[0]
    persisted = store.decisions(request.state.run_id)
    assert len(persisted) == 1
    assert persisted[0].candidate_id == "collect"
    assert persisted[0].state_revision == 3


def test_invalid_selection_never_reaches_storage(tmp_path: Path) -> None:
    request = _request("run-1")

    class Provider:
        def select(self, current: DecisionRequest) -> Decision:
            return Decision(
                current.request_id, current.request_hash, "fake",
                DecisionStatus.SELECTED, "invented",
            )

    store = _store(tmp_path, request.state.run_id)
    with pytest.raises(InvalidDecision, match="unknown candidate"):
        AdaptiveController(Provider(), store).select(request, current_state=request.state)
    assert store.decisions(request.state.run_id) == ()
