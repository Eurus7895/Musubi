"""Strict validation and freezing for versioned Root goal contracts.

musubi-tier: substrate
expires-when: never - the immutable definition of done is durable provenance
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from validation.execution_contract import (
    canonical_json,
    contract_hash,
    require_closed_object,
    require_identifier,
    require_positive_int,
    require_string_list,
)

_FIELDS = frozenset({
    "id", "version", "objective", "deliverables", "criteria", "invariants",
    "constraints", "excluded_scope", "total_budget", "stop_conditions",
    "supersedes", "contract_hash",
})
_CRITERION_FIELDS = frozenset({
    "id", "description", "required", "verifier_refs", "review_owner",
})
_BUDGET_FIELDS = frozenset({"max_tokens", "max_worker_turns", "max_work_packages"})


@dataclass(frozen=True)
class GoalCriterion:
    id: str
    description: str
    required: bool
    verifier_refs: tuple[str, ...]
    review_owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "required": self.required,
            "verifier_refs": list(self.verifier_refs),
        }
        if self.review_owner is not None:
            value["review_owner"] = self.review_owner
        return value


@dataclass(frozen=True)
class GoalBudget:
    max_tokens: int
    max_worker_turns: int
    max_work_packages: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_tokens": self.max_tokens,
            "max_worker_turns": self.max_worker_turns,
            "max_work_packages": self.max_work_packages,
        }


@dataclass(frozen=True)
class FrozenGoalContract:
    id: str
    version: int
    objective: str
    deliverables: tuple[str, ...]
    criteria: tuple[GoalCriterion, ...]
    invariants: tuple[str, ...]
    constraints: tuple[str, ...]
    excluded_scope: tuple[str, ...]
    total_budget: GoalBudget
    stop_conditions: tuple[str, ...]
    supersedes: str | None
    canonical_json: str
    contract_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "objective": self.objective,
            "deliverables": list(self.deliverables),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "invariants": list(self.invariants),
            "constraints": list(self.constraints),
            "excluded_scope": list(self.excluded_scope),
            "total_budget": self.total_budget.to_dict(),
            "stop_conditions": list(self.stop_conditions),
            "supersedes": self.supersedes,
            "contract_hash": self.contract_hash,
        }


def validate_and_freeze_goal_contract(
    raw: Mapping[str, Any],
    *,
    lineage_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> FrozenGoalContract:
    if not isinstance(raw, Mapping):
        raise ValueError("goal contract must be an object")
    require_closed_object(raw, allowed=_FIELDS, name="goal contract")
    goal_id = require_identifier(raw.get("id"), field="goal_contract.id", prefix="goal_")
    version = require_positive_int(raw.get("version"), field="goal_contract.version")
    objective = str(raw.get("objective") or "").strip()
    if not objective:
        raise ValueError("goal_contract.objective must be non-empty")
    deliverables = require_string_list(raw.get("deliverables"), field="goal_contract.deliverables")
    if not deliverables:
        raise ValueError("goal_contract.deliverables must not be empty")

    raw_criteria = raw.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ValueError("goal_contract.criteria must be a non-empty list")
    criteria: list[GoalCriterion] = []
    for index, item in enumerate(raw_criteria):
        if not isinstance(item, Mapping):
            raise ValueError(f"goal_contract.criteria[{index}] must be an object")
        require_closed_object(item, allowed=_CRITERION_FIELDS, name=f"criterion[{index}]")
        criterion_id = require_identifier(
            item.get("id"), field=f"criteria[{index}].id", prefix="C",
        )
        description = str(item.get("description") or "").strip()
        if not description:
            raise ValueError(f"criteria[{index}].description must be non-empty")
        required = item.get("required")
        if not isinstance(required, bool):
            raise ValueError(f"criteria[{index}].required must be boolean")
        refs = require_string_list(
            item.get("verifier_refs"), field=f"criteria[{index}].verifier_refs",
        )
        owner_raw = item.get("review_owner")
        owner = str(owner_raw).strip() if owner_raw is not None else None
        if owner == "":
            raise ValueError(f"criteria[{index}].review_owner must be non-empty")
        if not refs and owner is None:
            raise ValueError(
                f"criteria[{index}] needs verifier_refs or an explicit review_owner"
            )
        criteria.append(GoalCriterion(criterion_id, description, required, refs, owner))
    if len({criterion.id for criterion in criteria}) != len(criteria):
        raise ValueError("goal_contract criterion ids must be unique")

    budget_raw = raw.get("total_budget")
    if not isinstance(budget_raw, Mapping):
        raise ValueError("goal_contract.total_budget must be an object")
    require_closed_object(budget_raw, allowed=_BUDGET_FIELDS, name="total_budget")
    budget = GoalBudget(
        require_positive_int(budget_raw.get("max_tokens"), field="total_budget.max_tokens"),
        require_positive_int(
            budget_raw.get("max_worker_turns"), field="total_budget.max_worker_turns",
        ),
        require_positive_int(
            budget_raw.get("max_work_packages"), field="total_budget.max_work_packages",
        ),
    )
    supersedes_raw = raw.get("supersedes")
    supersedes = str(supersedes_raw).strip() if supersedes_raw is not None else None
    if version == 1 and supersedes is not None:
        raise ValueError("goal contract version 1 cannot supersede another contract")
    if version > 1 and supersedes is None:
        raise ValueError("goal contract version > 1 must declare supersedes")
    if supersedes is not None and lineage_lookup is not None:
        previous = lineage_lookup(supersedes)
        if previous is None:
            raise ValueError("goal contract supersedes does not reference an existing version")
        if str(previous.get("goal_id") or previous.get("id")) != goal_id:
            raise ValueError("goal contract supersedes belongs to a different goal")
        if int(previous.get("version", 0)) >= version:
            raise ValueError("goal contract version must increase across supersedes")

    invariants = require_string_list(
        raw.get("invariants"), field="goal_contract.invariants",
    )
    constraints = require_string_list(
        raw.get("constraints"), field="goal_contract.constraints",
    )
    excluded_scope = require_string_list(
        raw.get("excluded_scope"), field="goal_contract.excluded_scope",
    )
    stop_conditions = require_string_list(
        raw.get("stop_conditions"), field="goal_contract.stop_conditions",
    )
    value = {
        "id": goal_id,
        "version": version,
        "objective": objective,
        "deliverables": list(deliverables),
        "criteria": [criterion.to_dict() for criterion in criteria],
        "invariants": list(invariants),
        "constraints": list(constraints),
        "excluded_scope": list(excluded_scope),
        "total_budget": budget.to_dict(),
        "stop_conditions": list(stop_conditions),
        "supersedes": supersedes,
    }
    digest = contract_hash(value)
    echoed = str(raw.get("contract_hash") or "")
    if echoed and echoed != digest:
        raise ValueError("goal contract hash does not match canonical content")
    return FrozenGoalContract(
        id=goal_id,
        version=version,
        objective=objective,
        deliverables=deliverables,
        criteria=tuple(criteria),
        invariants=invariants,
        constraints=constraints,
        excluded_scope=excluded_scope,
        total_budget=budget,
        stop_conditions=stop_conditions,
        supersedes=supersedes,
        canonical_json=canonical_json(value),
        contract_hash=digest,
    )
