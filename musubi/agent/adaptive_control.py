"""Deterministic Adaptive contract transitions and completion gates.

musubi-tier: substrate
expires-when: never - model proposals must satisfy frozen execution contracts
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agent.goal_state import MUTATION_ROLES, GoalState
from agent.manifest import (
    ROOT_PLAN_CHANGE_SIZES,
    ROOT_PLAN_WORKER_ROLES,
    manifest_schema,
    parse_change_manifest_object,
)
from agent.planning_artifacts import persist_goal_contract, persist_planning_contract
from agent.run_state import Orchestration
from agent.textfmt import bounded

MAX_ROOT_WORKERS_HARD = 8

def _root_completion_blocker(
    state: GoalState | None,
    orchestration: Orchestration | None,
) -> str | None:
    """Return why a governed Root text response cannot be terminal yet."""
    if (
        state is None
        or orchestration is None
        or orchestration.work_package_controller is None
    ):
        return None
    if state.mode == "undecided":
        # A conversational or read-only answer does not need an execution
        # contract. Calling begin_plan is the Root's explicit declaration that
        # this turn will produce governed work; from that transition onward it
        # cannot escape the contract by returning prose or code as chat text.
        return None
    if state.mode == "planning":
        return "The plan and Goal Contract are not frozen; call musubi_commit_plan."
    controller = orchestration.work_package_controller
    if controller.goal is None:
        return "No frozen Goal Contract is active; call musubi_commit_plan."
    report = controller.gap_report()
    state.gap_report = report.to_dict()
    if report.complete:
        return None
    return (
        "Required Goal Contract criteria have not all passed; continue with "
        "a frozen Work Package or record explicit verification evidence. "
        "gap_report=" + json.dumps(report.to_dict(), sort_keys=True)
    )


def _blocked_completion_answer(reason: str) -> str:
    """Return a deterministic failure without leaking an unexecuted draft."""
    return (
        "[incomplete] governed execution did not complete. "
        f"{reason} Assistant draft text was discarded; no implementation was "
        "accepted as complete."
    )


class _PlanningContractError(ValueError):
    """A model-correctable Root plan declaration error."""

    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind


def _root_control_error(
    error_kind: str,
    message: str,
    state: GoalState,
) -> str:
    """Return one closed correction envelope for a bad plan declaration."""
    failures = state.record_planning_contract_failure(error_kind)
    terminal = failures >= 3
    if terminal:
        state.pending_clarification = (
            "[incomplete] run stopped: three consecutive planning-contract "
            "failures occurred before any worker was spawned. Correct the "
            "closed plan declaration and retry the request."
        )
        state.next_role = None
        state.role_chain = ()
    return json.dumps({
        "status": "incomplete" if terminal else "error",
        "error_kind": error_kind,
        "message": message,
        "expected_schema": manifest_schema(),
        "allowed_roles": list(ROOT_PLAN_WORKER_ROLES),
        "consecutive_failures": failures,
    })


def _root_control_terminal_error(
    error_kind: str,
    message: str,
    state: GoalState,
) -> str:
    """Stop immediately when control persistence failed outside model input."""
    state.pending_clarification = (
        "[incomplete] governed execution stopped because Root control could "
        f"not be persisted ({error_kind}). No implementation was accepted as "
        "complete."
    )
    state.next_role = None
    state.role_chain = ()
    return json.dumps({
        "status": "incomplete",
        "error_kind": error_kind,
        "message": message,
        "consecutive_failures": state.planning_contract_failures,
    })


def sanitize_control_result(result: str, tool_name: str) -> str:
    """Project a Root control outcome into a safe, bounded runtime event.

    The full tool response is retained in the tool audit for debugging. The
    Request Log must not repeat raw ``plan_markdown``, manifest fields, or the
    correction schema, which can be much larger and may contain user context.
    """
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    status = str(payload.get("status") or "error").strip().lower()
    if status not in {"ok", "error", "incomplete"}:
        status = "error"
    parts = [f"[agent] control {tool_name} status={status}"]

    error_kind = payload.get("error_kind")
    if isinstance(error_kind, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error_kind):
        parts.append(f"error_kind={error_kind}")
    # Correction responses use `message`; deliberately do not fall back to a
    # generic `error` field, which may contain provider or filesystem detail.
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(f"reason={bounded(message, 240)}")
    failures = payload.get("consecutive_failures")
    if type(failures) is int and failures >= 0:
        parts.append(f"consecutive_failures={failures}")
    return " ".join(parts)


def _handle_root_control_tool(
    name: str,
    args: dict[str, Any],
    orchestration: Orchestration,
) -> str:
    """Apply model-owned mode/plan declarations to driver-owned goal state."""
    state = orchestration.goal_state
    assert state is not None
    try:
        if name == "musubi_begin_plan":
            deliverable = str(args.get("deliverable") or "").strip()
            if not deliverable:
                raise ValueError("deliverable must be a non-empty string")
            state.begin_plan()
            return json.dumps({
                "status": "ok",
                "mode": state.mode,
                "deliverable": deliverable,
            })

        controller = orchestration.work_package_controller
        if name != "musubi_commit_plan":
            if controller is None:
                raise ValueError(f"{name} requires Root Work Package control")
            if name == "musubi_commit_work_package":
                raw = args.get("work_package")
                if not isinstance(raw, dict):
                    raise ValueError("work_package must be an object")
                contract = controller.freeze_work_package(raw)
                state.gap_report = controller.gap_report().to_dict()
                return json.dumps({
                    "status": "ok",
                    "work_package_id": contract.id,
                    "version": contract.version,
                    "contract_hash": contract.contract_hash,
                    "resolved_brief": controller.resolved_brief(contract.id),
                    "gap_report": state.gap_report,
                })
            if name == "musubi_record_criterion_verdict":
                raw_status = str(args.get("status") or "").strip()
                if raw_status not in {"pending", "pass", "fail", "blocked"}:
                    raise ValueError("criterion status is invalid")
                raw_evidence = args.get("evidence_refs")
                if not isinstance(raw_evidence, list) or any(
                    not isinstance(item, str) or not item.strip()
                    for item in raw_evidence
                ):
                    raise ValueError("evidence_refs must be non-empty strings")
                criterion = controller.set_criterion_state(
                    str(args.get("criterion_id") or "").strip(),
                    raw_status,
                    evidence_refs=raw_evidence,
                    work_package_id=(
                        str(args["work_package_id"]).strip()
                        if args.get("work_package_id") else None
                    ),
                    reason=str(args.get("reason") or "").strip(),
                )
                state.gap_report = controller.gap_report().to_dict()
                return json.dumps({
                    "status": "ok",
                    "criterion_state": criterion.to_dict(),
                    "gap_report": state.gap_report,
                })
            if name == "musubi_get_gap_report":
                state.gap_report = controller.gap_report().to_dict()
                return json.dumps({"status": "ok", "gap_report": state.gap_report})
            if name == "musubi_rollback_work_package":
                from agent.rollback import rollback_attempt

                result = rollback_attempt(
                    str(args.get("attempt_id") or "").strip(),
                    roots=controller.roots,
                    db_path=controller.db_path,
                )
                return json.dumps(result)

        raw_plan = args.get("plan_markdown")
        if not isinstance(raw_plan, str) or not raw_plan.strip():
            raise _PlanningContractError(
                "invalid_plan_markdown",
                "plan_markdown must be a non-empty string",
            )
        plan_markdown = raw_plan
        manifest_object = args.get("change_manifest")
        model_dump = getattr(manifest_object, "model_dump", None)
        if callable(model_dump):
            manifest_object = model_dump(mode="python")
        manifest = parse_change_manifest_object(manifest_object)
        if manifest is None:
            raise _PlanningContractError(
                "invalid_change_manifest",
                "change_manifest must match the closed manifest schema",
            )
        raw_size = args.get("change_size")
        if not isinstance(raw_size, str):
            raise _PlanningContractError(
                "invalid_change_size",
                "change_size must be small, medium, or large",
            )
        change_size = raw_size.strip()
        raw_chain = args.get("worker_chain")
        if not isinstance(raw_chain, list):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain must be an array of allowed roles",
            )
        if any(not isinstance(role, str) for role in raw_chain):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain must contain only allowed role strings",
            )
        chain = tuple(role.strip() for role in raw_chain)
        # Validate model declaration before any artifact reaches disk.
        if change_size not in ROOT_PLAN_CHANGE_SIZES:
            raise _PlanningContractError(
                "invalid_change_size",
                "change_size must be small, medium, or large",
            )
        if not chain or any(role not in ROOT_PLAN_WORKER_ROLES for role in chain):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain roles must be one of the allowed non-planner roles",
            )
        if not any(role in MUTATION_ROLES for role in chain):
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain must contain a mutation role",
            )
        needed = orchestration.spawned_workers + len(chain) + 1
        if needed > MAX_ROOT_WORKERS_HARD:
            raise _PlanningContractError(
                "invalid_worker_chain",
                "worker_chain exceeds the hard worker ceiling including recovery",
            )
        if controller is None:
            raise _PlanningContractError(
                "control_unavailable",
                "work package controller is unavailable",
            )
        raw_goal = args.get("goal_contract")
        if not isinstance(raw_goal, dict):
            raise _PlanningContractError(
                "invalid_goal_contract",
                "goal_contract is required",
            )
        # Validate the full declaration before persisting plan.md/manifest.json.
        # A malformed Goal Contract must not leave a partial planning artifact
        # set that looks committed to operators or a later run.
        try:
            controller.validate_goal(raw_goal)
        except ValueError as exc:
            raise _PlanningContractError(
                "invalid_goal_contract", str(exc),
            ) from exc
        if orchestration.planning_artifact_dir is None:
            raise _PlanningContractError(
                "control_unavailable",
                "planning artifact directory is unavailable",
            )
        persisted = persist_planning_contract(
            plan_markdown,
            manifest_object,
            orchestration.planning_artifact_dir,
        )
        if persisted is None:
            raise _PlanningContractError(
                "invalid_plan_markdown",
                "plan_markdown or change_manifest is invalid",
            )
        paths, artifacts = persisted
        goal_path = None
        goal_contract = controller.freeze_goal(raw_goal)
        goal_path = persist_goal_contract(
            goal_contract.to_dict(), orchestration.planning_artifact_dir,
        )
        state.commit_root_plan(
            manifest=artifacts.manifest,
            change_size=change_size,
            worker_chain=chain,
            planning_artifacts=(
                str(path) for path in ((*paths, goal_path) if goal_path else paths)
            ),
        )
        state.next_role = None
        state.role_chain = ()
        state.gap_report = controller.gap_report().to_dict()
        orchestration.max_root_workers = max(
            orchestration.max_root_workers, needed,
        )
        return json.dumps({
            "status": "ok",
            "mode": state.mode,
            "change_size": state.change_size,
            "worker_chain": list(chain),
            "next_role": state.next_role,
            "max_root_workers": orchestration.max_root_workers,
            "planning_artifacts": list(state.planning_artifacts),
            "goal_contract_hash": (
                controller.goal.contract_hash
                if controller is not None and controller.goal is not None else None
            ),
            "gap_report": state.gap_report,
        })
    except _PlanningContractError as exc:
        return _root_control_error(exc.error_kind, str(exc), state)
    except ValueError as exc:
        if name == "musubi_commit_plan":
            return _root_control_error("invalid_plan_contract", str(exc), state)
        return json.dumps({
            "status": "error",
            "error_kind": "invalid_control_input",
            "message": str(exc),
        })
    except (OSError, sqlite3.Error) as exc:
        return _root_control_terminal_error(
            "control_persistence_error", str(exc), state,
        )
