"""Reusable mandatory Root Work Package contracts for orchestration tests.

musubi-tier: ephemeral test
expires-when: Root orchestration tests no longer use canned model responses
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from validation.work_package_contract import validate_and_freeze_work_package

GOAL_CONTRACT: dict[str, Any] = {
    "id": "goal_orchestration_test",
    "version": 1,
    "objective": "Exercise the orchestration behavior under test.",
    "deliverables": ["test orchestration result"],
    "criteria": [{
        "id": "C1",
        "description": "The optional worker exercise completes.",
        "required": False,
        "verifier_refs": ["root-review"],
        "review_owner": "root",
    }],
    "invariants": [],
    "constraints": [],
    "excluded_scope": [],
    "total_budget": {
        "max_tokens": 100_000,
        "max_worker_turns": 100,
        "max_work_packages": 4,
    },
    "stop_conditions": [],
    "supersedes": None,
}

WORK_PACKAGE: dict[str, Any] = {
    "id": "wp_orchestration_test",
    "version": 1,
    "goal_contract_id": GOAL_CONTRACT["id"],
    "criterion_ids": ["C1"],
    "scope": {"include": ["*", "**/*"], "exclude": []},
    "expected_delta": [{"criterion_id": "C1", "from": "pending", "to": "pass"}],
    "verifier_refs": ["root-review"],
    "acceptance_predicates": [],
    "budget": {"max_tokens": 50_000, "max_turns": 20, "max_attempts": 3},
    "rollback_point": {"type": "file_journal", "ref": "test-journal"},
    "dependencies": [],
    "reversibility": "automatic",
    "supersedes": None,
}

WORK_PACKAGE_HASH = validate_and_freeze_work_package(
    WORK_PACKAGE,
    goal_criterion_ids={"C1"},
).contract_hash


def spawn_contract_fields() -> dict[str, str]:
    """Return the immutable package identity every canned spawn must echo."""
    return {
        "work_package_id": WORK_PACKAGE["id"],
        "contract_hash": WORK_PACKAGE_HASH,
    }


def make_work_package(work_package_id: str) -> tuple[dict[str, Any], str]:
    """Create a distinct immutable package for parallel-worker fixtures."""
    package = deepcopy(WORK_PACKAGE)
    package["id"] = work_package_id
    digest = validate_and_freeze_work_package(
        package,
        goal_criterion_ids={"C1"},
    ).contract_hash
    return package, digest
