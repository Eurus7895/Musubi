"""Tests for executor.py — ruff, mypy, pytest subprocess wrappers."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import executor
from executor import (
    FailedTest,
    LintError,
    LintResult,
    RunResult,
    TypeCheckError,
    TypeCheckResult,
    _parse_mypy_output,
    _parse_pytest_output,
    _parse_ruff_output,
    run_all,
    run_lint,
    run_tests,
    run_typecheck,
)


# ── _parse_ruff_output ────────────────────────────────────────────────────────

def test_parse_ruff_output_empty():
    assert _parse_ruff_output("[]") == []


def test_parse_ruff_output_single_error():
    payload = json.dumps([{
        "filename": "foo.py",
        "location": {"row": 10, "column": 4},
        "code": "E501",
        "message": "Line too long",
    }])
    errors = _parse_ruff_output(payload)
    assert len(errors) == 1
    e = errors[0]
    assert e.file == "foo.py"
    assert e.line == 10
    assert e.col == 4
    assert e.code == "E501"
    assert e.message == "Line too long"


def test_parse_ruff_output_multiple_errors():
    payload = json.dumps([
        {"filename": "a.py", "location": {"row": 1, "column": 1},
         "code": "F401", "message": "unused import"},
        {"filename": "b.py", "location": {"row": 5, "column": 2},
         "code": "E302", "message": "expected 2 blank lines"},
    ])
    errors = _parse_ruff_output(payload)
    assert len(errors) == 2
    assert errors[0].file == "a.py"
    assert errors[1].file == "b.py"


def test_parse_ruff_output_invalid_json():
    assert _parse_ruff_output("not json") == []


def test_parse_ruff_output_missing_location_fields():
    payload = json.dumps([{"filename": "x.py", "location": {}, "code": "E1", "message": "m"}])
    errors = _parse_ruff_output(payload)
    assert errors[0].line == 0
    assert errors[0].col == 0


# ── _parse_mypy_output ────────────────────────────────────────────────────────

def test_parse_mypy_output_no_errors():
    assert _parse_mypy_output("Success: no issues found in 1 source file") == []


def test_parse_mypy_output_single_error():
    out = "src/foo.py:12: error: Argument 1 to 'bar' has incompatible type\n"
    errors = _parse_mypy_output(out)
    assert len(errors) == 1
    assert errors[0].file == "src/foo.py"
    assert errors[0].line == 12
    assert "incompatible type" in errors[0].message


def test_parse_mypy_output_skips_notes():
    out = (
        "src/foo.py:12: error: Incompatible types\n"
        "src/foo.py:12: note: See https://mypy.rtfd.io\n"
        "Found 1 error in 1 file\n"
    )
    errors = _parse_mypy_output(out)
    assert len(errors) == 1


def test_parse_mypy_output_skips_summary_line():
    out = "Found 2 errors in 1 file (checked 3 source files)\n"
    assert _parse_mypy_output(out) == []


def test_parse_mypy_output_multiple_errors():
    out = (
        "a.py:1: error: Missing return statement\n"
        "b.py:20: error: Incompatible types in assignment\n"
    )
    errors = _parse_mypy_output(out)
    assert len(errors) == 2
    assert errors[0].file == "a.py"
    assert errors[1].file == "b.py"


# ── _parse_pytest_output ──────────────────────────────────────────────────────

def test_parse_pytest_output_no_failures():
    out = "1 passed in 0.12s"
    assert _parse_pytest_output(out) == []


def test_parse_pytest_output_single_failure():
    out = "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 got 2\n"
    failures = _parse_pytest_output(out)
    assert len(failures) == 1
    assert failures[0].test_name == "tests/test_foo.py::test_bar"
    assert "AssertionError" in failures[0].reason


def test_parse_pytest_output_multiple_failures():
    out = (
        "FAILED tests/test_a.py::test_one - AssertionError: wrong value\n"
        "FAILED tests/test_b.py::test_two - TypeError: bad type\n"
    )
    failures = _parse_pytest_output(out)
    assert len(failures) == 2
    assert failures[0].test_name == "tests/test_a.py::test_one"
    assert failures[1].test_name == "tests/test_b.py::test_two"


def test_parse_pytest_output_ignores_non_fail_lines():
    out = (
        "collected 3 items\n"
        "PASSED tests/test_a.py::test_ok\n"
        "FAILED tests/test_b.py::test_bad - ValueError: oops\n"
        "1 failed, 1 passed in 0.5s\n"
    )
    failures = _parse_pytest_output(out)
    assert len(failures) == 1


# ── run_lint ──────────────────────────────────────────────────────────────────

def test_run_lint_empty_files_passes():
    result = run_lint([])
    assert result.passed is True
    assert result.errors == []


def test_run_lint_clean_file(tmp_path: Path):
    f = tmp_path / "clean.py"
    f.write_text("x = 1\n")
    result = run_lint([str(f)])
    assert result.passed is True
    assert result.errors == []


def test_run_lint_detects_unused_import(tmp_path: Path):
    f = tmp_path / "bad.py"
    f.write_text("import os\n\nx = 1\n")
    result = run_lint([str(f)])
    assert result.passed is False
    assert any(e.code == "F401" for e in result.errors)


def test_run_lint_returns_lint_result_type():
    result = run_lint([])
    assert isinstance(result, LintResult)


def test_run_lint_command_not_found():
    with patch("executor._run", return_value=(1, "", "Command not found: ruff")):
        result = run_lint(["x.py"])
    assert result.passed is False


# ── run_typecheck ─────────────────────────────────────────────────────────────

def test_run_typecheck_empty_files_passes():
    result = run_typecheck([])
    assert result.passed is True
    assert result.errors == []


def test_run_typecheck_clean_file(tmp_path: Path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    result = run_typecheck([str(f)])
    assert result.passed is True


def test_run_typecheck_type_error(tmp_path: Path):
    f = tmp_path / "bad.py"
    f.write_text(textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b

        add("not", "ints")
    """))
    result = run_typecheck([str(f)])
    assert result.passed is False
    assert len(result.errors) > 0
    assert result.errors[0].file.endswith("bad.py")


def test_run_typecheck_returns_typecheck_result_type():
    result = run_typecheck([])
    assert isinstance(result, TypeCheckResult)


def test_run_typecheck_command_not_found():
    with patch("executor._run", return_value=(1, "", "Command not found: mypy")):
        result = run_typecheck(["x.py"])
    assert result.passed is False


# ── run_tests ─────────────────────────────────────────────────────────────────

def test_run_tests_passing_suite(tmp_path: Path):
    t = tmp_path / "test_ok.py"
    t.write_text("def test_always_passes():\n    assert 1 == 1\n")
    result = run_tests(str(tmp_path))
    assert result.passed is True
    assert result.failures == []


def test_run_tests_failing_suite(tmp_path: Path):
    t = tmp_path / "test_fail.py"
    t.write_text("def test_always_fails():\n    assert 1 == 2\n")
    result = run_tests(str(tmp_path))
    assert result.passed is False


def test_run_tests_returns_test_result_type(tmp_path: Path):
    t = tmp_path / "test_x.py"
    t.write_text("def test_x():\n    pass\n")
    result = run_tests(str(tmp_path))
    assert isinstance(result, RunResult)


def test_run_tests_raw_output_populated(tmp_path: Path):
    t = tmp_path / "test_x.py"
    t.write_text("def test_x():\n    pass\n")
    result = run_tests(str(tmp_path))
    assert len(result.raw) > 0


# ── run_all ───────────────────────────────────────────────────────────────────

def test_run_all_clean_code(tmp_path: Path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    t = tmp_path / "test_clean.py"
    t.write_text("from clean import add\ndef test_add():\n    assert add(1, 2) == 3\n")
    result = run_all([str(f)], str(tmp_path))
    assert result.lint is not None
    assert result.typecheck is not None
    assert result.tests is not None


def test_run_all_stops_at_lint_failure(tmp_path: Path):
    f = tmp_path / "bad.py"
    f.write_text("import os\nx = 1\n")
    result = run_all([str(f)], str(tmp_path))
    assert result.passed is False
    assert result.lint is not None
    assert result.lint.passed is False
    assert result.typecheck is None  # short-circuited
    assert result.tests is None


def test_run_all_passed_flag_reflects_tests(tmp_path: Path):
    f = tmp_path / "clean.py"
    f.write_text("x = 1\n")
    t = tmp_path / "test_fail.py"
    t.write_text("def test_bad():\n    assert False\n")
    result = run_all([str(f)], str(tmp_path))
    assert result.passed is False
    assert result.lint is not None
    assert result.lint.passed is True
