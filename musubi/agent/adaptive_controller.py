"""Adaptive lifecycle boundary: select, validate, persist, then expose an action.

musubi-tier: substrate
expires-when: never - one owner must apply Adaptive state transitions
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.decision_contracts import (
    Candidate,
    Decision,
    DecisionProvider,
    DecisionRequest,
    DecisionState,
    resolve_decision,
)
from storage.adaptive_runs import AdaptiveRunStore


@dataclass(frozen=True)
class DecisionOutcome:
    """Persisted provider outcome and the already-authored selected option."""

    decision: Decision
    candidate: Candidate | None


class AdaptiveController:
    """Own the decision boundary without executing the selected operation.

    Runtime receives an option only after it matches the current state and the
    provider outcome is durably recorded. The Controller does not manufacture
    candidates, widen authority or translate a finish proposal into success.
    """

    def __init__(self, provider: DecisionProvider, store: AdaptiveRunStore) -> None:
        self.provider = provider
        self.store = store

    def select(
        self, request: DecisionRequest, *, current_state: DecisionState,
    ) -> DecisionOutcome:
        decision = self.provider.select(request)
        candidate = resolve_decision(request, decision, current_state=current_state)
        self.store.record_decision(
            session_id=current_state.run_id,
            state_revision=current_state.revision,
            request_id=decision.request_id,
            request_hash=decision.request_hash,
            provider_id=decision.provider_id,
            status=decision.status.value,
            candidate_id=decision.candidate_id,
            legal_actions=tuple(item.candidate_id for item in request.candidates),
        )
        return DecisionOutcome(decision, candidate)
