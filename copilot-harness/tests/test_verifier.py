"""Tests for verifier.py — schema validation, secrets scan, cross-stage contracts."""

import pytest
from pathlib import Path

import state
import verifier
from storage import db as _db


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _db.init_db(p)
    return p


@pytest.fixture()
def session(db: Path) -> str:
    return state.create_session("build a login endpoint", db_path=db)


# ── Valid outputs ─────────────────────────────────────────────────────────────

def test_valid_planner_output() -> None:
    output = {"summary": "Build login flow", "tasks": [{"id": "T1", "description": "Add route"}]}
    result = verifier.validate(output, "planner")
    assert result.valid is True
    assert result.errors == []


def test_valid_designer_output() -> None:
    output = {
        "architecture": "REST API with JWT",
        "files": [{"path": "app.py", "purpose": "main app"}],
    }
    result = verifier.validate(output, "designer")
    assert result.valid is True


def test_valid_coder_output() -> None:
    output = {"summary": "Implemented login", "files_modified": ["app.py"]}
    result = verifier.validate(output, "coder")
    assert result.valid is True


def test_valid_reviewer_output_pass() -> None:
    output = {"status": "pass", "attempt": 1, "issues": []}
    result = verifier.validate(output, "reviewer")
    assert result.valid is True


def test_valid_reviewer_output_fail() -> None:
    output = {
        "status": "fail",
        "attempt": 1,
        "issues": [{"severity": "high", "description": "no auth", "fix_instruction": "add JWT"}],
    }
    result = verifier.validate(output, "reviewer")
    assert result.valid is True


def test_valid_reviewer_status_wrong_plan() -> None:
    output = {"status": "wrong_plan", "attempt": 1, "issues": []}
    result = verifier.validate(output, "reviewer")
    assert result.valid is True


def test_unknown_agent_passes_without_schema() -> None:
    result = verifier.validate({"anything": True}, "skill-builder")
    assert result.valid is True


# ── Missing required fields ───────────────────────────────────────────────────

def test_planner_missing_summary() -> None:
    result = verifier.validate({"tasks": []}, "planner")
    assert result.valid is False
    assert any("summary" in e for e in result.errors)


def test_planner_missing_tasks() -> None:
    result = verifier.validate({"summary": "x"}, "planner")
    assert result.valid is False
    assert any("tasks" in e for e in result.errors)


def test_designer_missing_architecture() -> None:
    result = verifier.validate({"files": []}, "designer")
    assert result.valid is False
    assert any("architecture" in e for e in result.errors)


def test_designer_missing_files() -> None:
    result = verifier.validate({"architecture": "REST"}, "designer")
    assert result.valid is False
    assert any("files" in e for e in result.errors)


def test_coder_missing_summary() -> None:
    result = verifier.validate({"files_modified": []}, "coder")
    assert result.valid is False
    assert any("summary" in e for e in result.errors)


def test_coder_missing_files_modified() -> None:
    result = verifier.validate({"summary": "done"}, "coder")
    assert result.valid is False
    assert any("files_modified" in e for e in result.errors)


def test_reviewer_missing_status() -> None:
    result = verifier.validate({"attempt": 1, "issues": []}, "reviewer")
    assert result.valid is False
    assert any("status" in e for e in result.errors)


def test_reviewer_missing_attempt() -> None:
    result = verifier.validate({"status": "pass", "issues": []}, "reviewer")
    assert result.valid is False
    assert any("attempt" in e for e in result.errors)


def test_reviewer_missing_issues() -> None:
    result = verifier.validate({"status": "pass", "attempt": 1}, "reviewer")
    assert result.valid is False
    assert any("issues" in e for e in result.errors)


def test_multiple_missing_fields_reported() -> None:
    result = verifier.validate({}, "planner")
    assert result.valid is False
    assert len(result.errors) >= 2


# ── Type errors ───────────────────────────────────────────────────────────────

def test_planner_tasks_wrong_type() -> None:
    result = verifier.validate({"summary": "x", "tasks": "not a list"}, "planner")
    assert result.valid is False
    assert any("tasks" in e for e in result.errors)


def test_reviewer_attempt_wrong_type() -> None:
    result = verifier.validate({"status": "pass", "attempt": "one", "issues": []}, "reviewer")
    assert result.valid is False
    assert any("attempt" in e for e in result.errors)


def test_output_not_a_dict() -> None:
    result = verifier.validate(["not", "a", "dict"], "planner")
    assert result.valid is False
    assert any("JSON object" in e for e in result.errors)


# ── Invalid status enum ───────────────────────────────────────────────────────

def test_reviewer_invalid_status_value() -> None:
    result = verifier.validate({"status": "maybe", "attempt": 1, "issues": []}, "reviewer")
    assert result.valid is False
    assert any("status" in e for e in result.errors)


# ── Secrets scan ──────────────────────────────────────────────────────────────

def test_aws_access_key_detected() -> None:
    output = {"summary": "done", "files_modified": [], "key": "AKIAIOSFODNN7EXAMPLE"}
    result = verifier.validate(output, "coder")
    assert result.valid is False
    assert any("AWS access key" in e for e in result.errors)


def test_private_key_detected() -> None:
    output = {
        "summary": "done",
        "files_modified": [],
        "data": "-----BEGIN RSA PRIVATE KEY-----",
    }
    result = verifier.validate(output, "coder")
    assert result.valid is False
    assert any("private key" in e for e in result.errors)


def test_github_token_detected() -> None:
    output = {
        "summary": "done",
        "files_modified": [],
        "token": "ghp_" + "A" * 36,
    }
    result = verifier.validate(output, "coder")
    assert result.valid is False
    assert any("GitHub token" in e for e in result.errors)


def test_generic_api_key_detected() -> None:
    output = {
        "summary": "done",
        "files_modified": [],
        "config": "api_key=supersecretvalue12345678",
    }
    result = verifier.validate(output, "coder")
    assert result.valid is False
    assert any("API key" in e for e in result.errors)


def test_clean_output_no_secrets() -> None:
    output = {"summary": "done", "files_modified": ["app.py"]}
    result = verifier.validate(output, "coder")
    assert result.valid is True


# ── Cross-stage: design references plan task IDs ──────────────────────────────

def test_design_references_all_plan_tasks(session: str, db: Path) -> None:
    state.write_stage(session, "plan", {
        "summary": "login",
        "tasks": [{"id": "T1", "description": "route"}, {"id": "T2", "description": "model"}],
    }, db_path=db)
    design = {
        "architecture": "REST",
        "files": [{"path": "app.py", "purpose": "T1 and T2 implementation"}],
    }
    result = verifier.validate(design, "designer", session_id=session, db_path=db)
    assert result.valid is True


def test_design_missing_plan_task_id(session: str, db: Path) -> None:
    state.write_stage(session, "plan", {
        "summary": "login",
        "tasks": [{"id": "T1", "description": "route"}, {"id": "T2", "description": "model"}],
    }, db_path=db)
    design = {
        "architecture": "REST",
        "files": [{"path": "app.py", "purpose": "only T1 here"}],
    }
    result = verifier.validate(design, "designer", session_id=session, db_path=db)
    assert result.valid is False
    assert any("T2" in e for e in result.errors)


def test_design_skips_contract_check_when_no_plan(session: str, db: Path) -> None:
    design = {"architecture": "REST", "files": []}
    result = verifier.validate(design, "designer", session_id=session, db_path=db)
    assert result.valid is True


# ── Cross-stage: code only modifies declared files ───────────────────────────

def test_code_modifies_only_declared_files(session: str, db: Path) -> None:
    state.write_stage(session, "plan", {"summary": "x", "tasks": []}, db_path=db)
    state.write_stage(session, "design", {
        "architecture": "x",
        "files": [{"path": "app.py", "purpose": "main"}, {"path": "models.py", "purpose": "db"}],
    }, db_path=db)
    code = {"summary": "done", "files_modified": ["app.py", "models.py"]}
    result = verifier.validate(code, "coder", session_id=session, db_path=db)
    assert result.valid is True


def test_code_modifies_undeclared_file(session: str, db: Path) -> None:
    state.write_stage(session, "plan", {"summary": "x", "tasks": []}, db_path=db)
    state.write_stage(session, "design", {
        "architecture": "x",
        "files": [{"path": "app.py", "purpose": "main"}],
    }, db_path=db)
    code = {"summary": "done", "files_modified": ["app.py", "secret.py"]}
    result = verifier.validate(code, "coder", session_id=session, db_path=db)
    assert result.valid is False
    assert any("secret.py" in e for e in result.errors)


def test_code_skips_contract_check_when_no_design(session: str, db: Path) -> None:
    state.write_stage(session, "plan", {"summary": "x", "tasks": []}, db_path=db)
    code = {"summary": "done", "files_modified": ["anything.py"]}
    result = verifier.validate(code, "coder", session_id=session, db_path=db)
    assert result.valid is True


def test_code_skips_contract_check_when_design_has_no_files(session: str, db: Path) -> None:
    state.write_stage(session, "plan", {"summary": "x", "tasks": []}, db_path=db)
    state.write_stage(session, "design", {"architecture": "x", "files": []}, db_path=db)
    code = {"summary": "done", "files_modified": ["app.py"]}
    result = verifier.validate(code, "coder", session_id=session, db_path=db)
    assert result.valid is True


# ── ValidationResult helpers ──────────────────────────────────────────────────

def test_validation_result_ok() -> None:
    r = verifier.ValidationResult.ok()
    assert r.valid is True
    assert r.errors == []


def test_validation_result_failed() -> None:
    r = verifier.ValidationResult.failed(["oops"])
    assert r.valid is False
    assert r.errors == ["oops"]
