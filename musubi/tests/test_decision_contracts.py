"""Selection cannot invent actions or reuse decisions from a different state."""

from dataclasses import replace

import pytest

from agent.decision_contracts import (
    ActionKind,
    Candidate,
    Decision,
    DecisionRequest,
    DecisionState,
    DecisionStatus,
    InvalidDecision,
    resolve_decision,
)


def request():
    return DecisionRequest(
        "request-1", DecisionState("run-1", "unit-1", 1, "contract-1", "context-1"),
        (Candidate.create("read", ActionKind.COLLECT, {"tool": "read", "path": "a.py"}),),
        (ActionKind.COLLECT,), 100,
    )


def selection(req, candidate_id="read"):
    return Decision(req.request_id, req.request_hash, "fake", DecisionStatus.SELECTED, candidate_id)


def test_roundtrip_and_payload_copy_preserve_selection():
    req = request()
    restored = DecisionRequest.from_json(req.to_json())
    decision = Decision.from_json(selection(req).to_json())
    candidate = resolve_decision(restored, decision, current_state=restored.state)
    candidate.payload()["path"] = "other.py"
    assert candidate.payload()["path"] == "a.py"
    assert restored == req
    assert restored.request_hash == req.request_hash


@pytest.mark.parametrize("field,value", [
    ("run_id", "run-2"), ("unit_id", "unit-2"), ("revision", 2),
    ("contract_hash", "contract-2"), ("context_hash", "context-2"),
])
def test_state_changes_reject_previous_decision(field, value):
    req = request()
    with pytest.raises(InvalidDecision, match="stale"):
        resolve_decision(req, selection(req), current_state=replace(req.state, **{field: value}))


def test_changed_options_or_budget_cannot_reuse_selection():
    req = request()
    changed_candidate = Candidate.create("read", ActionKind.COLLECT, {"path": "secret"})
    for changed in (
        replace(req, remaining_tokens=0),
        replace(req, candidates=(changed_candidate,)),
        replace(req, request_id="request-2"),
    ):
        with pytest.raises(InvalidDecision, match="match request"):
            resolve_decision(changed, selection(req), current_state=changed.state)


def test_unknown_selection_and_illegal_option_fail_closed():
    req = request()
    with pytest.raises(InvalidDecision, match="unknown candidate"):
        resolve_decision(req, selection(req, "invented"), current_state=req.state)
    with pytest.raises(ValueError, match="not allowed"):
        replace(req, candidates=(Candidate.create("write", ActionKind.EXECUTE, {}),))
    with pytest.raises(ValueError, match="duplicate"):
        replace(req, candidates=req.candidates * 2)


def test_abstention_and_timeout_never_resolve_an_action():
    req = request()
    for status in (DecisionStatus.ABSTAINED, DecisionStatus.TIMED_OUT):
        decision = Decision(req.request_id, req.request_hash, "fake", status)
        assert resolve_decision(req, decision, current_state=req.state) is None
        with pytest.raises(ValueError, match="nonselected"):
            replace(decision, candidate_id="read")


def test_unsupported_schema_and_extra_fields_are_rejected():
    req = request()
    with pytest.raises(ValueError, match="schema version"):
        replace(req, schema_version=2)
    with pytest.raises(ValueError, match="fields"):
        DecisionRequest.from_json(req.to_json()[:-1] + ',"permission_override":true}')


def test_fake_and_response_adapter_use_same_selection_boundary():
    from agent.decider import ResponseSelectionProvider
    from agent.vendors.base import LMResponse

    class FakeProvider:
        def select(self, req):
            return selection(req)

    req = request()
    response = LMResponse("end_turn", [{"type": "text", "text": '{"candidate_id":"read"}'}])
    for provider in (FakeProvider(), ResponseSelectionProvider(response, provider_id="llm")):
        candidate = resolve_decision(req, provider.select(req), current_state=req.state)
        assert candidate == req.candidates[0]
    for text in ('{"candidate_id":"unknown"}', '{"candidate_id":"read","path":"override"}'):
        adapter = ResponseSelectionProvider(
            LMResponse("end_turn", [{"type": "text", "text": text}]), provider_id="llm",
        )
        with pytest.raises(InvalidDecision):
            adapter.select(req)
