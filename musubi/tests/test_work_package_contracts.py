"""Goal Contract and Work Package control-loop invariants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.budget import TokenBudgetEnforcer
from agent.goal_state import GoalState
from agent.rollback import capture_before, record_after, rollback_attempt
from agent.run import Orchestration, _handle_root_control_tool
from agent.work_package_controller import WorkPackageController
from session import state, sub_sessions
from storage import db
from validation.execution_contract import canonical_json
from validation.goal_contract import validate_and_freeze_goal_contract
from workspace.grants import RootRegistry


def _goal() -> dict[str, Any]:
    return {
        "id": "goal_demo",
        "version": 1,
        "objective": "Produce a verified module",
        "deliverables": ["src/result.py"],
        "criteria": [
            {
                "id": "C1",
                "description": "module exists",
                "required": True,
                "verifier_refs": ["file-created"],
            },
            {
                "id": "C2",
                "description": "module is semantically correct",
                "required": True,
                "verifier_refs": [],
                "review_owner": "root",
            },
        ],
        "invariants": ["no provider imports in substrate"],
        "constraints": [],
        "excluded_scope": ["pipeline"],
        "total_budget": {
            "max_tokens": 10_000,
            "max_worker_turns": 20,
            "max_work_packages": 5,
        },
        "stop_conditions": ["required criterion blocked"],
        "supersedes": None,
    }


def _work_package(
    *, criterion: str = "C1", semantic: bool = False,
) -> dict[str, Any]:
    predicates = [] if semantic else [
        {
            "criterion_id": criterion,
            "check": {
                "type": "file_created_or_modified",
                "root": "musubi",
                "path": "src/result.py",
            },
        }
    ]
    return {
        "id": f"wp_{criterion.lower()}",
        "version": 1,
        "goal_contract_id": "goal_demo",
        "criterion_ids": [criterion],
        "scope": {"include": ["src/*.py"], "exclude": ["src/secrets.py"]},
        "expected_delta": [{"criterion_id": criterion, "from": "pending", "to": "pass"}],
        "verifier_refs": ["root-review"] if semantic else [],
        "acceptance_predicates": predicates,
        "budget": {"max_tokens": 2_000, "max_turns": 4, "max_attempts": 3},
        "rollback_point": {"type": "file_journal", "ref": f"journal-{criterion}"},
        "dependencies": [],
        "reversibility": "automatic",
        "supersedes": None,
    }


def _controller(tmp_path: Path) -> WorkPackageController:
    database = tmp_path / "state.db"
    db.init_db(database)
    session_id = state.create_session("request", db_path=database)
    controller = WorkPackageController(
        session_id=session_id,
        root_budget=TokenBudgetEnforcer(10_000),
        roots=RootRegistry.build(tmp_path),
        db_path=database,
    )
    controller.freeze_goal(_goal())
    return controller


def test_canonicalization_is_stable_across_field_order() -> None:
    left = {"b": [2, 1], "a": {"y": 2, "x": 1}}
    right = {"a": {"x": 1, "y": 2}, "b": [2, 1]}
    assert canonical_json(left) == canonical_json(right)


def test_goal_contract_is_closed_and_requires_verification_owner() -> None:
    first = validate_and_freeze_goal_contract(_goal())
    reordered = dict(reversed(list(_goal().items())))
    second = validate_and_freeze_goal_contract(reordered)
    assert first.contract_hash == second.contract_hash

    invalid = _goal()
    invalid["criteria"][1].pop("review_owner")
    with pytest.raises(ValueError, match="verifier_refs or an explicit review_owner"):
        validate_and_freeze_goal_contract(invalid)


def test_work_package_retry_must_echo_hash_and_same_version_is_immutable(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    frozen = controller.freeze_work_package(_work_package())
    with pytest.raises(ValueError, match="hash"):
        controller.start_attempt(frozen.id, "sha256:wrong")
    with pytest.raises(ValueError, match="version"):
        controller.freeze_work_package(_work_package())


def test_mechanical_evidence_passes_only_its_mapped_criterion(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    frozen = controller.freeze_work_package(_work_package())
    controller.start_attempt(frozen.id, frozen.contract_hash)
    target = tmp_path / "src" / "result.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")

    gate = controller.finish_attempt(
        worker_status="done", touched_files=["src/result.py"],
    )
    report = controller.gap_report()

    assert gate is not None and gate.status == "pass"
    assert report.passed == ("C1",)
    assert report.pending == ("C2",)
    assert report.complete is False


def test_artifact_presence_cannot_satisfy_semantic_criterion(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    frozen = controller.freeze_work_package(_work_package(criterion="C2", semantic=True))
    controller.start_attempt(frozen.id, frozen.contract_hash)
    target = tmp_path / "src" / "result.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")

    assert controller.finish_attempt(
        worker_status="done", touched_files=["src/result.py"],
    ) is None
    assert controller.criteria["C2"].status == "pending"
    assert controller.gap_report().complete is False

    controller.set_criterion_state(
        "C2", "pass", evidence_refs=["review:root:1"],
        work_package_id=frozen.id, reason="root semantic review passed",
    )
    assert "C2" in controller.gap_report().passed


def test_scope_and_plateau_fail_closed(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    frozen = controller.freeze_work_package(_work_package())
    with pytest.raises(ValueError, match="out-of-scope"):
        controller.assert_paths_in_scope(frozen.id, ["docs/readme.md"])
    for _ in range(2):
        controller.start_attempt(frozen.id, frozen.contract_hash)
        controller.finish_attempt(
            worker_status="failed", touched_files=[], failure_class="test_failure",
        )
    allowed, reason = controller.retry_allowed(frozen.id, "test_failure")
    assert allowed is False
    assert "plateau" in reason


def test_file_journal_restores_multiple_files_byte_for_byte(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    db.init_db(database)
    session_id = state.create_session("request", db_path=database)
    roots = RootRegistry.build(tmp_path)
    first = tmp_path / "one.txt"
    first.write_bytes(b"before\x00one")
    for path in ("one.txt", "two.txt"):
        capture_before(
            session_id=session_id, work_package_id="wp_c1",
            attempt_id="attempt_1", root_alias="musubi", path=path,
            roots=roots, db_path=database,
        )
    first.write_bytes(b"after")
    (tmp_path / "two.txt").write_bytes(b"created")
    for path in ("one.txt", "two.txt"):
        record_after(
            attempt_id="attempt_1", root_alias="musubi", path=path,
            roots=roots, db_path=database,
        )

    result = rollback_attempt("attempt_1", roots=roots, db_path=database)

    assert result["status"] == "pass"
    assert first.read_bytes() == b"before\x00one"
    assert not (tmp_path / "two.txt").exists()


def test_ledger_links_goal_work_package_attempt_and_evidence(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    frozen = controller.freeze_work_package(_work_package())
    attempt = controller.start_attempt(frozen.id, frozen.contract_hash)
    target = tmp_path / "src" / "result.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    controller.finish_attempt(worker_status="done", touched_files=["src/result.py"])

    rows = db.get_work_package_attempts(
        controller.session_id, frozen.id, controller.db_path,
    )
    assert rows[0]["attempt_id"] == attempt.attempt_id
    assert rows[0]["contract_hash"] == frozen.contract_hash
    assert controller.goal is not None
    stored = db.get_goal_contract_version(
        controller.goal.contract_hash, controller.db_path,
    )
    assert stored is not None
    assert json.loads(stored["canonical_json"])["id"] == "goal_demo"

    restored = WorkPackageController(
        session_id=controller.session_id,
        root_budget=TokenBudgetEnforcer(10_000),
        roots=RootRegistry.build(tmp_path),
        db_path=controller.db_path,
    )
    restored.restore("goal_demo")
    assert restored.goal is not None
    assert restored.goal.contract_hash == controller.goal.contract_hash
    assert restored.work_packages[frozen.id].contract_hash == frozen.contract_hash
    assert restored.criteria["C1"].status == "pass"

    execution = db.goal_execution_snapshot(
        controller.session_id, "goal_demo", controller.db_path,
    )
    assert execution is not None
    assert execution["goal"]["contract_hash"] == controller.goal.contract_hash
    assert execution["work_packages"][0]["attempts"][0]["evidence"]


def test_sub_session_carries_frozen_execution_identity(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    db.init_db(database)
    session_id = state.create_session("request", db_path=database)

    handle = sub_sessions.spawn(
        session_id,
        "root",
        "coder",
        "Apply the frozen work package.",
        goal_id="goal_demo",
        work_package_id="wp_c1",
        attempt_id="attempt_1",
        contract_hash="sha256:frozen",
        db_path=database,
    )

    row = sub_sessions.get(handle, database)
    assert row is not None
    assert row["goal_id"] == "goal_demo"
    assert row["work_package_id"] == "wp_c1"
    assert row["attempt_id"] == "attempt_1"
    assert row["contract_hash"] == "sha256:frozen"


def test_root_control_commit_persists_goal_then_freezes_work_package(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    db.init_db(database)
    session_id = state.create_session("request", db_path=database)
    roots = RootRegistry.build(tmp_path)
    controller = WorkPackageController(
        session_id=session_id,
        root_budget=TokenBudgetEnforcer(10_000),
        roots=roots,
        db_path=database,
    )
    goal_state = GoalState.create("request", "unknown", "root_decides")
    goal_state.control_mode = "work_package"
    goal_state.begin_plan()
    orchestration = Orchestration(
        parent_session_id=session_id,
        goal_state=goal_state,
        planning_artifact_dir=tmp_path / ".musubi" / "goals" / "demo",
        work_package_controller=controller,
    )
    committed = json.loads(_handle_root_control_tool(
        "musubi_commit_plan",
        {
            "plan_markdown": "# Plan\n\nImplement one verified module.",
            "change_manifest": {"files_expected": 1, "subsystems": ["agent"]},
            "change_size": "small",
            "worker_chain": ["coder"],
            "goal_contract": _goal(),
        },
        orchestration,
    ))

    assert committed["status"] == "ok"
    assert committed["goal_contract_hash"].startswith("sha256:")
    assert orchestration.planning_artifact_dir is not None
    assert (orchestration.planning_artifact_dir / "goal_contract.json").is_file()
    assert goal_state.next_role is None

    frozen = json.loads(_handle_root_control_tool(
        "musubi_commit_work_package", {"work_package": _work_package()}, orchestration,
    ))
    assert frozen["status"] == "ok"
    assert frozen["work_package_id"] == "wp_c1"
    assert frozen["contract_hash"].startswith("sha256:")
