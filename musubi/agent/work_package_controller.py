"""Root adaptive Work Package lifecycle over immutable execution contracts.

musubi-tier: substrate
expires-when: never - evidence-backed goal completion is core governance
"""

from __future__ import annotations

import fnmatch
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.budget import ChildTokenBudget, TokenBudgetEnforcer
from storage import db
from validation.goal_contract import FrozenGoalContract, validate_and_freeze_goal_contract
from validation.stage_gate import GateResult, evaluate_execution_gate, fingerprint_file
from validation.work_package_contract import (
    FrozenWorkPackageContract,
    validate_and_freeze_work_package,
)
from workspace.grants import RootRegistry

CriterionStatus = Literal["pending", "pass", "fail", "blocked"]
AttemptStatus = Literal["running", "pass", "fail", "blocked", "budget_exhausted"]
RECOVERABLE_FAILURES = frozenset({"test_failure", "missing_artifact", "transient_error"})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class CriterionState:
    criterion_id: str
    status: CriterionStatus = "pending"
    evidence_refs: list[str] = field(default_factory=list)
    last_work_package_id: str | None = None
    reason: str = "not evaluated"
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
            "last_work_package_id": self.last_work_package_id,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WorkPackageAttempt:
    attempt_id: str
    number: int
    contract_hash: str
    snapshot: Mapping[str, Any]
    token_budget: ChildTokenBudget
    max_turns: int


@dataclass(frozen=True)
class GapReport:
    goal_contract_id: str
    passed: tuple[str, ...]
    failed: tuple[Mapping[str, Any], ...]
    blocked: tuple[Mapping[str, Any], ...]
    pending: tuple[str, ...]
    regressions: tuple[str, ...]
    candidate_work_packages: tuple[str, ...]
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_contract_id": self.goal_contract_id,
            "passed": list(self.passed),
            "failed": [dict(item) for item in self.failed],
            "blocked": [dict(item) for item in self.blocked],
            "pending": list(self.pending),
            "regressions": list(self.regressions),
            "candidate_work_packages": list(self.candidate_work_packages),
            "complete": self.complete,
        }


class WorkPackageController:
    """Own one Root goal's contracts, mutable criterion projection and attempts."""

    def __init__(
        self,
        *,
        session_id: str,
        root_budget: TokenBudgetEnforcer,
        roots: RootRegistry,
        db_path: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self.root_budget = root_budget
        self.roots = roots
        self.db_path = db_path
        self.goal: FrozenGoalContract | None = None
        self.criteria: dict[str, CriterionState] = {}
        self.work_packages: dict[str, FrozenWorkPackageContract] = {}
        self.active_work_package_id: str | None = None
        self.active_attempt: WorkPackageAttempt | None = None
        self._regressions: set[str] = set()

    def restore(self, goal_id: str) -> FrozenGoalContract:
        """Replay the latest Goal/WP versions and criterion projection."""
        row = db.latest_goal_contract(self.session_id, goal_id, self.db_path)
        if row is None:
            raise ValueError(f"no persisted Goal Contract for {goal_id}")
        raw_goal = json.loads(row["canonical_json"])
        raw_goal["contract_hash"] = row["contract_hash"]
        goal = validate_and_freeze_goal_contract(raw_goal)
        self.goal = goal
        folded = db.fold_criterion_states(self.session_id, goal.id, self.db_path)
        self.criteria = {}
        for criterion in goal.criteria:
            persisted = folded.get(criterion.id)
            state = CriterionState(criterion.id)
            if persisted is not None:
                state.status = persisted["status"]
                state.evidence_refs = list(persisted["evidence_refs"])
                state.last_work_package_id = persisted["work_package_id"]
                state.reason = persisted["reason"]
                state.updated_at = persisted["created_at"]
            self.criteria[criterion.id] = state
        self.work_packages = {}
        for wp_row in db.latest_work_packages_for_goal(
            self.session_id, goal.contract_hash, self.db_path,
        ):
            raw_wp = json.loads(wp_row["canonical_json"])
            raw_wp["contract_hash"] = wp_row["contract_hash"]
            contract = validate_and_freeze_work_package(
                raw_wp, goal_criterion_ids=frozenset(self.criteria),
            )
            self.work_packages[contract.id] = contract
        return goal

    def freeze_goal(self, raw: Mapping[str, Any]) -> FrozenGoalContract:
        contract = validate_and_freeze_goal_contract(
            raw,
            lineage_lookup=lambda digest: db.get_goal_contract_version(digest, self.db_path),
        )
        previous = db.latest_goal_contract(self.session_id, contract.id, self.db_path)
        if previous is not None and int(previous["version"]) >= contract.version:
            raise ValueError("goal contract version already exists or moves backwards")
        if previous is not None and contract.supersedes != previous["contract_hash"]:
            raise ValueError("goal contract must supersede the latest frozen version")
        db.insert_goal_contract_version(
            session_id=self.session_id,
            goal_id=contract.id,
            version=contract.version,
            canonical_json=contract.canonical_json,
            contract_hash=contract.contract_hash,
            supersedes_hash=contract.supersedes,
            created_at=_now(),
            db_path=self.db_path,
        )
        self.goal = contract
        existing = db.fold_criterion_states(self.session_id, contract.id, self.db_path)
        self.criteria = {}
        for criterion in contract.criteria:
            row = existing.get(criterion.id)
            state = CriterionState(criterion.id)
            if row is not None:
                state.status = row["status"]
                state.evidence_refs = list(row["evidence_refs"])
                state.last_work_package_id = row["work_package_id"]
                state.reason = row["reason"]
                state.updated_at = row["created_at"]
            else:
                self._record_state(state, reason="goal contract frozen")
            self.criteria[criterion.id] = state
        return contract

    def freeze_work_package(self, raw: Mapping[str, Any]) -> FrozenWorkPackageContract:
        goal = self._require_goal()
        if (
            len(self.work_packages) >= goal.total_budget.max_work_packages
            and str(raw.get("id") or "") not in self.work_packages
        ):
            raise ValueError("goal max_work_packages budget is exhausted")
        contract = validate_and_freeze_work_package(
            raw,
            goal_criterion_ids=frozenset(self.criteria),
            lineage_lookup=lambda digest: db.get_work_package_version(digest, self.db_path),
        )
        if contract.goal_contract_id != goal.id:
            raise ValueError("work package references a different goal contract")
        previous = db.latest_work_package_version(self.session_id, contract.id, self.db_path)
        if previous is not None and int(previous["version"]) >= contract.version:
            raise ValueError("work package version already exists or moves backwards")
        if previous is not None and contract.supersedes != previous["contract_hash"]:
            raise ValueError("work package must supersede the latest frozen version")
        for dependency in contract.dependencies:
            if dependency not in self.work_packages:
                raise ValueError(f"work package dependency is not frozen: {dependency}")
            dependency_criteria = self.work_packages[dependency].criterion_ids
            if any(self.criteria[item].status != "pass" for item in dependency_criteria):
                raise ValueError(f"work package dependency is not complete: {dependency}")
        for delta in contract.expected_delta:
            actual = self.criteria[delta.criterion_id].status
            if actual != delta.from_status:
                raise ValueError(
                    f"criterion {delta.criterion_id} is {actual}, expected {delta.from_status}"
                )
        db.insert_work_package_version(
            session_id=self.session_id,
            work_package_id=contract.id,
            version=contract.version,
            goal_contract_hash=goal.contract_hash,
            canonical_json=contract.canonical_json,
            contract_hash=contract.contract_hash,
            supersedes_hash=contract.supersedes,
            created_at=_now(),
            db_path=self.db_path,
        )
        self.work_packages[contract.id] = contract
        self.active_work_package_id = contract.id
        return contract

    def start_attempt(self, work_package_id: str, echoed_hash: str) -> WorkPackageAttempt:
        if self.active_attempt is not None:
            raise ValueError("another work package attempt is already running")
        work_package = self._work_package(work_package_id)
        if echoed_hash != work_package.contract_hash:
            raise ValueError("retry contract hash does not match the frozen work package")
        attempts = db.get_work_package_attempts(
            self.session_id, work_package_id, self.db_path,
        )
        number = len(attempts) + 1
        if number > work_package.budget.max_attempts:
            raise ValueError("work package max_attempts budget is exhausted")
        usage = db.goal_attempt_usage(self.session_id, self._require_goal().id, self.db_path)
        remaining_turns = self._require_goal().total_budget.max_worker_turns - usage["turns"]
        if remaining_turns <= 0:
            raise ValueError("goal max_worker_turns budget is exhausted")
        goal_token_remaining = (
            self._require_goal().total_budget.max_tokens - self.root_budget.tokens_used
        )
        if self.root_budget.remaining <= 0 or goal_token_remaining <= 0:
            raise ValueError("goal token budget is exhausted")
        allowance = min(
            work_package.budget.max_tokens,
            self.root_budget.remaining,
            goal_token_remaining,
        )
        attempt_id = "attempt_" + uuid.uuid4().hex[:16]
        attempt = WorkPackageAttempt(
            attempt_id=attempt_id,
            number=number,
            contract_hash=work_package.contract_hash,
            snapshot=self._snapshot(work_package),
            token_budget=ChildTokenBudget(self.root_budget, allowance),
            max_turns=min(work_package.budget.max_turns, remaining_turns),
        )
        db.insert_work_package_attempt(
            attempt_id=attempt_id,
            session_id=self.session_id,
            goal_id=self._require_goal().id,
            work_package_id=work_package_id,
            contract_hash=work_package.contract_hash,
            attempt=number,
            status="running",
            created_at=_now(),
            db_path=self.db_path,
        )
        db.append_budget_event(
            session_id=self.session_id,
            goal_id=self._require_goal().id,
            work_package_id=work_package_id,
            attempt_id=attempt_id,
            event="lease_granted",
            tokens=allowance,
            turns=attempt.max_turns,
            detail={"contract_hash": work_package.contract_hash},
            created_at=_now(),
            db_path=self.db_path,
        )
        self.active_work_package_id = work_package_id
        self.active_attempt = attempt
        return attempt

    def finish_attempt(
        self,
        *,
        worker_status: str,
        touched_files: Sequence[str],
        failure_class: str | None = None,
        command_runner: Callable[[str], Any] | None = None,
        turns_used: int = 0,
    ) -> GateResult | None:
        attempt = self._require_attempt()
        work_package = self._work_package(self.active_work_package_id or "")
        self.assert_paths_in_scope(work_package.id, touched_files)
        gate: GateResult | None = None
        criterion_delta: dict[str, str] = {}
        if worker_status == "done" and work_package.acceptance_predicates:
            gate = evaluate_execution_gate(
                work_package.acceptance_predicates,
                attempt.snapshot,
                [],
                command_runner,
                roots=self.roots,
            )
            grouped: dict[str, list[Any]] = {item: [] for item in work_package.criterion_ids}
            for predicate, result in zip(work_package.acceptance_predicates, gate.checks):
                criterion_id = str(predicate["criterion_id"])
                grouped[criterion_id].append(result)
                evidence_ref = f"evidence:{attempt.attempt_id}:{len(grouped[criterion_id])}"
                db.append_verification_evidence(
                    attempt_id=attempt.attempt_id,
                    criterion_id=criterion_id,
                    verifier_ref=evidence_ref,
                    status=result.status,
                    evidence={"message": result.message, **dict(result.evidence)},
                    created_at=_now(),
                    db_path=self.db_path,
                )
            for criterion_id, results in grouped.items():
                if not results:
                    continue
                status: CriterionStatus = (
                    "pass" if all(item.status == "pass" for item in results) else "fail"
                )
                refs = [
                    f"evidence:{attempt.attempt_id}:{index}"
                    for index in range(1, len(results) + 1)
                ]
                previous = self.criteria[criterion_id].status
                self.set_criterion_state(
                    criterion_id,
                    status,
                    evidence_refs=refs,
                    work_package_id=work_package.id,
                    reason="mechanical acceptance predicates evaluated",
                )
                if status == "pass" and previous != "pass":
                    criterion_delta[criterion_id] = "pass"
                if previous == "pass" and status != "pass":
                    self._regressions.add(criterion_id)
        terminal: AttemptStatus
        if attempt.token_budget.remaining <= 0 and worker_status != "done":
            terminal = "budget_exhausted"
            failure_class = failure_class or "budget_exhausted"
        elif worker_status != "done":
            terminal = "blocked"
            failure_class = failure_class or "worker_failure"
        elif gate is not None and gate.status != "pass":
            terminal = "fail"
            failure_class = failure_class or "test_failure"
        elif all(self.criteria[item].status == "pass" for item in work_package.criterion_ids):
            terminal = "pass"
        else:
            terminal = "blocked"
            failure_class = failure_class or "semantic_evidence_required"
        db.finish_work_package_attempt(
            attempt_id=attempt.attempt_id,
            status=terminal,
            failure_class=failure_class,
            tokens_used=attempt.token_budget.tokens_used,
            turns_used=turns_used,
            criterion_delta=criterion_delta,
            completed_at=_now(),
            db_path=self.db_path,
        )
        db.append_budget_event(
            session_id=self.session_id,
            goal_id=self._require_goal().id,
            work_package_id=work_package.id,
            attempt_id=attempt.attempt_id,
            event="lease_closed",
            tokens=attempt.token_budget.tokens_used,
            turns=turns_used,
            detail={"status": terminal, "failure_class": failure_class},
            created_at=_now(),
            db_path=self.db_path,
        )
        self.active_attempt = None
        return gate

    def set_criterion_state(
        self,
        criterion_id: str,
        status: CriterionStatus,
        *,
        evidence_refs: Sequence[str],
        work_package_id: str | None,
        reason: str,
    ) -> CriterionState:
        if criterion_id not in self.criteria:
            raise ValueError(f"unknown criterion: {criterion_id}")
        if status == "pass" and not evidence_refs:
            raise ValueError("a passing criterion requires evidence")
        state = self.criteria[criterion_id]
        if state.status == "pass" and status != "pass":
            self._regressions.add(criterion_id)
        elif status == "pass":
            self._regressions.discard(criterion_id)
        state.status = status
        state.evidence_refs = list(evidence_refs)
        state.last_work_package_id = work_package_id
        state.reason = reason.strip() or "criterion state updated"
        state.updated_at = _now()
        self._record_state(state, reason=state.reason)
        return state

    def gap_report(self) -> GapReport:
        goal = self._require_goal()
        required = {item.id for item in goal.criteria if item.required}
        passed = tuple(sorted(
            key for key, state in self.criteria.items() if state.status == "pass"
        ))
        failed = tuple(
            {"criterion_id": key, "evidence_refs": list(state.evidence_refs),
             "failure_class": state.reason}
            for key, state in sorted(self.criteria.items()) if state.status == "fail"
        )
        blocked = tuple(
            {
                "criterion_id": key,
                "evidence_refs": list(state.evidence_refs),
                "reason": state.reason,
            }
            for key, state in sorted(self.criteria.items()) if state.status == "blocked"
        )
        pending = tuple(sorted(
            key for key, state in self.criteria.items() if state.status == "pending"
        ))
        candidates = tuple(
            item.id for item in self.work_packages.values()
            if any(self.criteria[key].status != "pass" for key in item.criterion_ids)
        )
        complete = required.issubset(passed) and not self._regressions
        return GapReport(
            goal.id, passed, failed, blocked, pending, tuple(sorted(self._regressions)),
            candidates, complete,
        )

    def retry_allowed(self, work_package_id: str, failure_class: str) -> tuple[bool, str]:
        work_package = self._work_package(work_package_id)
        attempts = db.get_work_package_attempts(self.session_id, work_package_id, self.db_path)
        if failure_class not in RECOVERABLE_FAILURES:
            return False, "failure is not recoverable under the same contract"
        if len(attempts) >= work_package.budget.max_attempts:
            return False, "max_attempts exhausted"
        if self.root_budget.remaining <= 0:
            return False, "goal token budget exhausted"
        terminal = [item for item in attempts if item["status"] != "running"]
        if len(terminal) >= 2:
            latest = terminal[-2:]
            if all(not item["criterion_delta"] for item in latest):
                return False, "plateau: two attempts created no positive criterion delta"
            if latest[0]["failure_class"] == latest[1]["failure_class"]:
                return False, "plateau: repeated failure class requires a strategy change"
        return True, "same-contract retry is allowed"

    def assert_paths_in_scope(self, work_package_id: str, paths: Sequence[str]) -> None:
        work_package = self._work_package(work_package_id)
        for reference in paths:
            path = reference.split("::", 1)[-1]
            included = any(fnmatch.fnmatch(path, pattern) for pattern in work_package.scope_include)
            excluded = any(fnmatch.fnmatch(path, pattern) for pattern in work_package.scope_exclude)
            if not included or excluded:
                raise ValueError(f"out-of-scope mutation: {reference}")

    def resolved_brief(self, work_package_id: str) -> str:
        work_package = self._work_package(work_package_id)
        goal = self._require_goal()
        payload = work_package.to_dict()
        return (
            f"Goal: {goal.objective}\n"
            f"Frozen Work Package (do not change):\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "Return evidence for this contract only. Do not widen scope or budget."
        )

    def _snapshot(self, work_package: FrozenWorkPackageContract) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        for predicate in work_package.acceptance_predicates:
            check = predicate.get("check", {})
            if not isinstance(check, Mapping) or "path" not in check or "root" not in check:
                continue
            key = f"{check['root']}:{check['path']}"
            try:
                snapshot[key] = fingerprint_file(
                    self.roots.resolve(str(check["root"]), str(check["path"])),
                )
            except (ValueError, PermissionError):
                snapshot[key] = None
        return snapshot

    def _record_state(self, state: CriterionState, *, reason: str) -> None:
        goal = self._require_goal()
        db.append_criterion_event(
            session_id=self.session_id,
            goal_id=goal.id,
            goal_contract_hash=goal.contract_hash,
            criterion_id=state.criterion_id,
            status=state.status,
            evidence_refs=list(state.evidence_refs),
            work_package_id=state.last_work_package_id,
            reason=reason,
            created_at=state.updated_at,
            db_path=self.db_path,
        )

    def _require_goal(self) -> FrozenGoalContract:
        if self.goal is None:
            raise ValueError("no frozen Goal Contract is active")
        return self.goal

    def _require_attempt(self) -> WorkPackageAttempt:
        if self.active_attempt is None:
            raise ValueError("no Work Package attempt is active")
        return self.active_attempt

    def _work_package(self, work_package_id: str) -> FrozenWorkPackageContract:
        try:
            return self.work_packages[work_package_id]
        except KeyError as exc:
            raise ValueError(f"unknown or unfrozen work package: {work_package_id}") from exc
