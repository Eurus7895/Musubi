"""Decision interpretation and bounded recovery selection. No tool execution.

musubi-tier: substrate
expires-when: never - explicit driver and execution boundaries
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent.decision_contracts import (
    Decision,
    DecisionRequest,
    DecisionStatus,
    InvalidDecision,
    resolve_decision,
)
from agent.vendors.base import LMResponse


@dataclass(frozen=True)
class ResponseDecision:
    """Legacy LLM decision proposal, never an execution/completion verdict.

    This migration adapter separates interpreting the model's selected actions
    from executing them. It makes no extra model call and is not a Jev adapter.
    Existing truncation, permission and completion gates remain authoritative.
    """

    text: str
    tool_uses: list[dict[str, Any]]
    discarded_markup: bool


def interpret_response(response: LMResponse) -> ResponseDecision:
    text = _extract_text(response.content)
    discarded_markup = bool(text and _looks_like_vendor_tool_markup(text))
    return ResponseDecision(
        text="" if discarded_markup else text,
        tool_uses=[block for block in response.content if block.get("type") == "tool_use"],
        discarded_markup=discarded_markup,
    )


class FailureKind(StrEnum):
    """Typed cause of a worker's terminal failure, derived from control flow
    (turn counters, marker branches, raised exceptions) — never from parsing
    summary prose."""

    TURN_CAP = "turn_cap"
    BLOCKED = "blocked"
    BUDGET = "budget"
    POLICY = "policy"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    AUTO_REPLACE = "auto_replace"
    ROOT_ANALYZE = "root_analyze"
    HALT = "halt"


@dataclass(frozen=True)
class WorkerOutcome:
    """Terminal state retained by the parent for a possible replacement."""

    role: str
    status: str
    summary: str
    touched_files: tuple[str, ...] = ()
    #: The firewalled brief this worker ran on — an automatic replacement
    #: re-runs the same contract, not a paraphrase of it.
    brief: str = ""
    #: None on success or on a legacy/untyped failure (which keeps the
    #: root-analysis path); set from control flow for typed failures.
    failure_kind: FailureKind | None = None
    #: The skill id the root pushed into this worker's spawn, if any. Replayed
    #: on an automatic replacement so the continuation runs the SAME worker
    #: contract — a direct worker carries no native skill tool, so dropping it
    #: would resume the artifact without the pushed procedure.
    pushed_skill_id: str | None = None
    work_package_id: str | None = None
    contract_hash: str | None = None


def decide_recovery(
    outcome: WorkerOutcome,
    *,
    same_role_failures: int,
    worker_slots: int,
) -> RecoveryAction:
    """Deterministic verdict for one terminal worker failure.

    Exhausted worker slots or a second same-role failure always halt —
    one audited continuation is the limit, never a replacement loop. A first
    turn-cap failure that left real artifacts behind is genuinely unfinished
    work: replace it automatically. Budget/policy failures stay fail-closed.
    Everything else (blocked, unknown, no surviving evidence) goes to the
    root's bounded analysis window.
    """
    if worker_slots <= 0 or same_role_failures >= 2:
        return RecoveryAction.HALT
    if outcome.failure_kind is FailureKind.TURN_CAP and outcome.touched_files:
        return RecoveryAction.AUTO_REPLACE
    if outcome.failure_kind in {FailureKind.BUDGET, FailureKind.POLICY}:
        return RecoveryAction.HALT
    return RecoveryAction.ROOT_ANALYZE


def _extract_text(content_blocks: list[dict[str, Any]]) -> str:
    parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "".join(parts).strip()


def _looks_like_vendor_tool_markup(text: str) -> bool:
    """True when `text` is a vendor's tool-call syntax rather than prose.

    Fail-closed by design: the caller discards the text and reports that the
    worker did not answer, rather than trying to salvage a plan out of markup.
    """
    return _VENDOR_TOOL_MARKUP_RE.search(text or "") is not None


_VENDOR_TOOL_MARKUP_RE = re.compile(
    r"(?i)(\bDSML\b|<[|｜]+\s*tool[_▁]?calls?|<tool_call\b|"
    r"</?function_calls?\b|<invoke\s+name\s*=|\bantml:invoke\b|"
    r"<[|｜]python_tag[|｜]>)"
)


class ResponseSelectionProvider:
    """Interpret an already-accounted LLM selection response through the port.

    The caller supplies a response from LMRouter. This adapter makes no call,
    never generates parameters, and never retries or switches providers. It is
    for the typed selection path, separate from legacy tool-call interpretation.
    """

    def __init__(self, response: LMResponse, *, provider_id: str) -> None:
        self.response = response
        self.provider_id = provider_id

    def select(self, request: DecisionRequest) -> Decision:
        if self.response.stop_reason != "end_turn":
            raise InvalidDecision("selection response is incomplete or requested tools")
        if any(block.get("type") != "text" for block in self.response.content):
            raise InvalidDecision("selection response must contain only selection text")
        try:
            payload = json.loads(_extract_text(self.response.content))
        except (ValueError, TypeError) as exc:
            raise InvalidDecision("selection response is not JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"candidate_id"}:
            raise InvalidDecision("selection must contain only candidate_id")
        candidate_id = payload["candidate_id"]
        if candidate_id is not None and not isinstance(candidate_id, str):
            raise InvalidDecision("candidate_id must be a string or null")
        decision = Decision(
            request.request_id, request.request_hash, self.provider_id,
            DecisionStatus.ABSTAINED if candidate_id is None else DecisionStatus.SELECTED,
            candidate_id,
        )
        resolve_decision(request, decision, current_state=request.state)
        return decision
