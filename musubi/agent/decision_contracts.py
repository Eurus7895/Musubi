"""Versioned, immutable option selection contracts; no model or tool execution.

musubi-tier: substrate
expires-when: never - bind decisions to the state and options they evaluated
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Any, Protocol


class InvalidDecision(ValueError):
    """A proposal cannot be applied to the current decision request."""


class ActionKind(StrEnum):
    COLLECT = "collect"
    THINK = "think"
    EXECUTE = "execute"
    VERIFY = "verify"
    FINISH = "finish"
    ESCALATE = "escalate"


class DecisionStatus(StrEnum):
    SELECTED = "selected"
    ABSTAINED = "abstained"
    TIMED_OUT = "timed_out"


def _identity(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _record(raw: str, record_type: type) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {f.name for f in fields(record_type)}:
        raise ValueError("record fields do not match schema")
    return data


@dataclass(frozen=True)
class DecisionState:
    run_id: str
    unit_id: str
    revision: int
    contract_hash: str
    context_hash: str

    def __post_init__(self) -> None:
        for name in ("run_id", "unit_id", "contract_hash", "context_hash"):
            _required(getattr(self, name), name)
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")


@dataclass(frozen=True)
class Candidate:
    """An immutable proposal; payload is canonical JSON, not a mutable dict.

    FINISH requests a completion check; it never means verified completion.
    The producer supplies arguments. A selecting provider only returns an ID.
    """

    candidate_id: str
    kind: ActionKind
    payload_json: str

    def __post_init__(self) -> None:
        _required(self.candidate_id, "candidate_id")
        if not isinstance(self.kind, ActionKind):
            raise ValueError("kind must be an ActionKind")
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("candidate payload must be an object")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        object.__setattr__(self, "payload_json", canonical)

    @classmethod
    def create(cls, candidate_id: str, kind: ActionKind, payload: dict[str, Any]) -> Candidate:
        return cls(candidate_id, kind, json.dumps(payload, allow_nan=False))

    def payload(self) -> dict[str, Any]:
        """Fresh copy: callers cannot mutate the option set through this value."""
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    state: DecisionState
    candidates: tuple[Candidate, ...]
    allowed_kinds: tuple[ActionKind, ...]
    remaining_tokens: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        _required(self.request_id, "request_id")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported decision schema version")
        if not isinstance(self.state, DecisionState):
            raise ValueError("state must be a DecisionState")
        if type(self.remaining_tokens) is not int or self.remaining_tokens < 0:
            raise ValueError("remaining_tokens must be a nonnegative integer")
        if not isinstance(self.candidates, tuple) or not self.candidates:
            raise ValueError("candidates must be a nonempty tuple")
        if not all(isinstance(c, Candidate) for c in self.candidates):
            raise ValueError("invalid candidate type")
        ids = [c.candidate_id for c in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate ID")
        if not isinstance(self.allowed_kinds, tuple) or not all(
            isinstance(kind, ActionKind) for kind in self.allowed_kinds
        ):
            raise ValueError("allowed_kinds must be an ActionKind tuple")
        if any(c.kind not in self.allowed_kinds for c in self.candidates):
            raise ValueError("candidate action is not allowed in this state")

    @property
    def request_hash(self) -> str:
        """Bind state, option arguments, legal actions and budget together."""
        return _identity(asdict(self))

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> DecisionRequest:
        data = _record(raw, cls)
        data["state"] = DecisionState(**data["state"])
        data["candidates"] = tuple(
            Candidate(**{**c, "kind": ActionKind(c["kind"])}) for c in data["candidates"]
        )
        data["allowed_kinds"] = tuple(ActionKind(k) for k in data["allowed_kinds"])
        return cls(**data)


@dataclass(frozen=True)
class Decision:
    request_id: str
    request_hash: str
    provider_id: str
    status: DecisionStatus
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "request_hash", "provider_id"):
            _required(getattr(self, name), name)
        if not isinstance(self.status, DecisionStatus):
            raise ValueError("invalid decision status")
        if self.status is DecisionStatus.SELECTED:
            _required(self.candidate_id, "candidate_id")
        elif self.candidate_id is not None:
            raise ValueError("nonselected decisions cannot carry a candidate")

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> Decision:
        data = _record(raw, cls)
        data["status"] = DecisionStatus(data["status"])
        return cls(**data)


class DecisionProvider(Protocol):
    """Driver port. Future model-backed implementations must use the gateway.

    Call orchestration owns deadlines, accounting and explicit fallback policy.
    This interface does not itself enforce a timeout or execute a selection.
    """

    def select(self, request: DecisionRequest) -> Decision: ...


def resolve_decision(
    request: DecisionRequest, decision: Decision, *, current_state: DecisionState,
) -> Candidate | None:
    """Resolve only against current state; downstream policy/evidence still apply."""
    if current_state != request.state:
        raise InvalidDecision("decision state is stale")
    if decision.request_id != request.request_id or decision.request_hash != request.request_hash:
        raise InvalidDecision("decision does not match request")
    if decision.status is not DecisionStatus.SELECTED:
        return None
    for candidate in request.candidates:
        if candidate.candidate_id == decision.candidate_id:
            return candidate
    raise InvalidDecision("unknown candidate ID")
