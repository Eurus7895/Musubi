"""Strict validation and freezing for bounded Root work packages.

musubi-tier: substrate
expires-when: never - bounded execution contracts remain audit provenance
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
    "id", "version", "goal_contract_id", "criterion_ids", "scope",
    "expected_delta", "verifier_refs", "acceptance_predicates", "budget",
    "rollback_point", "dependencies", "reversibility", "supersedes",
    "contract_hash",
})
_SCOPE_FIELDS = frozenset({"include", "exclude"})
_DELTA_FIELDS = frozenset({"criterion_id", "from", "to"})
_BUDGET_FIELDS = frozenset({"max_tokens", "max_turns", "max_attempts"})
_ROLLBACK_FIELDS = frozenset({"type", "ref"})
_STATUSES = frozenset({"pending", "pass", "fail", "blocked"})
_ROLLBACK_TYPES = frozenset({"file_journal", "checkpoint", "manual"})
_REVERSIBILITY = frozenset({"automatic", "manual", "irreversible"})
_CHECK_TYPES = frozenset({
    "file_exists", "file_created_or_modified", "dom_count",
    "dom_distinct_text", "dom_text_set", "named_command", "lint_clean",
})
_PATH_CHECKS = frozenset({
    "file_exists", "file_created_or_modified", "dom_count",
    "dom_distinct_text", "dom_text_set",
})


@dataclass(frozen=True)
class WorkPackageBudget:
    max_tokens: int
    max_turns: int
    max_attempts: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_tokens": self.max_tokens,
            "max_turns": self.max_turns,
            "max_attempts": self.max_attempts,
        }


@dataclass(frozen=True)
class ExpectedDelta:
    criterion_id: str
    from_status: str
    to_status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "criterion_id": self.criterion_id,
            "from": self.from_status,
            "to": self.to_status,
        }


@dataclass(frozen=True)
class FrozenWorkPackageContract:
    id: str
    version: int
    goal_contract_id: str
    criterion_ids: tuple[str, ...]
    scope_include: tuple[str, ...]
    scope_exclude: tuple[str, ...]
    expected_delta: tuple[ExpectedDelta, ...]
    verifier_refs: tuple[str, ...]
    acceptance_predicates: tuple[Mapping[str, Any], ...]
    budget: WorkPackageBudget
    rollback_type: str
    rollback_ref: str
    dependencies: tuple[str, ...]
    reversibility: str
    supersedes: str | None
    canonical_json: str
    contract_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "goal_contract_id": self.goal_contract_id,
            "criterion_ids": list(self.criterion_ids),
            "scope": {"include": list(self.scope_include), "exclude": list(self.scope_exclude)},
            "expected_delta": [delta.to_dict() for delta in self.expected_delta],
            "verifier_refs": list(self.verifier_refs),
            "acceptance_predicates": [dict(predicate) for predicate in self.acceptance_predicates],
            "budget": self.budget.to_dict(),
            "rollback_point": {"type": self.rollback_type, "ref": self.rollback_ref},
            "dependencies": list(self.dependencies),
            "reversibility": self.reversibility,
            "supersedes": self.supersedes,
            "contract_hash": self.contract_hash,
        }


def validate_and_freeze_work_package(
    raw: Mapping[str, Any],
    *,
    goal_criterion_ids: set[str] | frozenset[str],
    lineage_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> FrozenWorkPackageContract:
    if not isinstance(raw, Mapping):
        raise ValueError("work package must be an object")
    require_closed_object(raw, allowed=_FIELDS, name="work package")
    work_package_id = require_identifier(raw.get("id"), field="work_package.id", prefix="wp_")
    version = require_positive_int(raw.get("version"), field="work_package.version")
    goal_contract_id = require_identifier(
        raw.get("goal_contract_id"), field="work_package.goal_contract_id", prefix="goal_",
    )
    criterion_ids = require_string_list(
        raw.get("criterion_ids"), field="work_package.criterion_ids",
    )
    if not criterion_ids:
        raise ValueError("work_package.criterion_ids must not be empty")
    unknown_criteria = sorted(set(criterion_ids) - set(goal_criterion_ids))
    if unknown_criteria:
        raise ValueError(f"work package references unknown criteria: {', '.join(unknown_criteria)}")

    scope = raw.get("scope")
    if not isinstance(scope, Mapping):
        raise ValueError("work_package.scope must be an object")
    require_closed_object(scope, allowed=_SCOPE_FIELDS, name="work_package.scope")
    includes = require_string_list(scope.get("include"), field="work_package.scope.include")
    excludes = require_string_list(scope.get("exclude"), field="work_package.scope.exclude")
    if not includes:
        raise ValueError("work_package.scope.include must not be empty")
    overlap = sorted(set(includes) & set(excludes))
    if overlap:
        raise ValueError(f"work package scope include/exclude overlap: {', '.join(overlap)}")

    raw_delta = raw.get("expected_delta")
    if not isinstance(raw_delta, list) or not raw_delta:
        raise ValueError("work_package.expected_delta must be a non-empty list")
    deltas: list[ExpectedDelta] = []
    for index, item in enumerate(raw_delta):
        if not isinstance(item, Mapping):
            raise ValueError(f"expected_delta[{index}] must be an object")
        require_closed_object(item, allowed=_DELTA_FIELDS, name=f"expected_delta[{index}]")
        criterion_id = str(item.get("criterion_id") or "").strip()
        before = str(item.get("from") or "").strip()
        after = str(item.get("to") or "").strip()
        if criterion_id not in criterion_ids:
            raise ValueError(f"expected_delta[{index}] criterion is outside criterion_ids")
        if before not in _STATUSES or after not in _STATUSES or before == after:
            raise ValueError(f"expected_delta[{index}] must describe a real status transition")
        deltas.append(ExpectedDelta(criterion_id, before, after))
    if {delta.criterion_id for delta in deltas} != set(criterion_ids):
        raise ValueError("expected_delta must cover every work package criterion exactly once")
    if len({delta.criterion_id for delta in deltas}) != len(deltas):
        raise ValueError("expected_delta criterion ids must be unique")

    verifier_refs = require_string_list(
        raw.get("verifier_refs"), field="work_package.verifier_refs",
    )
    predicates_raw = raw.get("acceptance_predicates")
    if not isinstance(predicates_raw, list):
        raise ValueError("work_package.acceptance_predicates must be a list")
    predicates: list[Mapping[str, Any]] = []
    for index, predicate in enumerate(predicates_raw):
        if not isinstance(predicate, Mapping):
            raise ValueError(f"acceptance_predicates[{index}] must be an object")
        criterion_id = str(predicate.get("criterion_id") or "").strip()
        if criterion_id not in criterion_ids:
            raise ValueError(f"acceptance_predicates[{index}] has an unknown criterion_id")
        check = predicate.get("check")
        if not isinstance(check, Mapping) or not str(check.get("type") or "").strip():
            raise ValueError(f"acceptance_predicates[{index}].check must declare a type")
        check_type = str(check["type"]).strip()
        if check_type not in _CHECK_TYPES:
            raise ValueError(f"acceptance_predicates[{index}] has unknown check type")
        if check_type in _PATH_CHECKS and (
            not str(check.get("root") or "").strip()
            or not str(check.get("path") or "").strip()
        ):
            raise ValueError(
                f"acceptance_predicates[{index}] path check needs root and path"
            )
        if check_type == "named_command" and not str(
            check.get("command_id") or ""
        ).strip():
            raise ValueError(
                f"acceptance_predicates[{index}] named_command needs command_id"
            )
        predicates.append({"criterion_id": criterion_id, "check": dict(check)})
    if not verifier_refs and not predicates:
        raise ValueError("work package needs verifier_refs or acceptance_predicates")

    budget_raw = raw.get("budget")
    if not isinstance(budget_raw, Mapping):
        raise ValueError("work_package.budget must be an object")
    require_closed_object(budget_raw, allowed=_BUDGET_FIELDS, name="work_package.budget")
    budget = WorkPackageBudget(
        require_positive_int(budget_raw.get("max_tokens"), field="budget.max_tokens"),
        require_positive_int(budget_raw.get("max_turns"), field="budget.max_turns"),
        require_positive_int(budget_raw.get("max_attempts"), field="budget.max_attempts"),
    )
    rollback = raw.get("rollback_point")
    if not isinstance(rollback, Mapping):
        raise ValueError("work_package.rollback_point must be an object")
    require_closed_object(rollback, allowed=_ROLLBACK_FIELDS, name="rollback_point")
    rollback_type = str(rollback.get("type") or "").strip()
    rollback_ref = str(rollback.get("ref") or "").strip()
    if rollback_type not in _ROLLBACK_TYPES or not rollback_ref:
        raise ValueError("rollback_point requires a supported type and non-empty ref")
    reversibility = str(raw.get("reversibility") or "").strip()
    if reversibility not in _REVERSIBILITY:
        raise ValueError(f"reversibility must be one of {sorted(_REVERSIBILITY)}")
    if reversibility == "automatic" and rollback_type != "file_journal":
        raise ValueError("automatic reversibility requires a file_journal rollback point")
    if reversibility == "irreversible" and rollback_type != "manual":
        raise ValueError("irreversible work must use a manual rollback point")
    dependencies = require_string_list(raw.get("dependencies"), field="work_package.dependencies")
    if work_package_id in dependencies:
        raise ValueError("work package cannot depend on itself")

    supersedes_raw = raw.get("supersedes")
    supersedes = str(supersedes_raw).strip() if supersedes_raw is not None else None
    if version == 1 and supersedes is not None:
        raise ValueError("work package version 1 cannot supersede another contract")
    if version > 1 and supersedes is None:
        raise ValueError("work package version > 1 must declare supersedes")
    if supersedes is not None and lineage_lookup is not None:
        previous = lineage_lookup(supersedes)
        if previous is None:
            raise ValueError("work package supersedes does not reference an existing version")
        if str(previous.get("work_package_id") or previous.get("id")) != work_package_id:
            raise ValueError("work package supersedes belongs to a different work package")
        if int(previous.get("version", 0)) >= version:
            raise ValueError("work package version must increase across supersedes")

    value = {
        "id": work_package_id,
        "version": version,
        "goal_contract_id": goal_contract_id,
        "criterion_ids": list(criterion_ids),
        "scope": {"include": list(includes), "exclude": list(excludes)},
        "expected_delta": [delta.to_dict() for delta in deltas],
        "verifier_refs": list(verifier_refs),
        "acceptance_predicates": [dict(predicate) for predicate in predicates],
        "budget": budget.to_dict(),
        "rollback_point": {"type": rollback_type, "ref": rollback_ref},
        "dependencies": list(dependencies),
        "reversibility": reversibility,
        "supersedes": supersedes,
    }
    digest = contract_hash(value)
    echoed = str(raw.get("contract_hash") or "")
    if echoed and echoed != digest:
        raise ValueError("work package hash does not match canonical content")
    return FrozenWorkPackageContract(
        id=work_package_id,
        version=version,
        goal_contract_id=goal_contract_id,
        criterion_ids=criterion_ids,
        scope_include=includes,
        scope_exclude=excludes,
        expected_delta=tuple(deltas),
        verifier_refs=verifier_refs,
        acceptance_predicates=tuple(predicates),
        budget=budget,
        rollback_type=rollback_type,
        rollback_ref=rollback_ref,
        dependencies=dependencies,
        reversibility=reversibility,
        supersedes=supersedes,
        canonical_json=canonical_json(value),
        contract_hash=digest,
    )
