"""Execution layer — runs ruff, mypy, and pytest as subprocesses.

Zero LLM calls. Each function returns a structured result dict.
Called by server.py harness_run_lint / harness_run_typecheck / harness_run_tests.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_TIMEOUT = 30  # seconds per subprocess call


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class LintError:
    file: str
    line: int
    col: int
    code: str
    message: str


@dataclass
class LintResult:
    passed: bool
    errors: list[LintError] = field(default_factory=list)
    raw: str = ""


@dataclass
class TypeCheckError:
    file: str
    line: int
    message: str


@dataclass
class TypeCheckResult:
    passed: bool
    errors: list[TypeCheckError] = field(default_factory=list)
    raw: str = ""


@dataclass
class FailedTest:
    test_name: str
    reason: str


@dataclass
class RunResult:
    passed: bool
    failures: list[FailedTest] = field(default_factory=list)
    raw: str = ""


@dataclass
class ExecutionResult:
    passed: bool
    lint: LintResult | None = None
    typecheck: TypeCheckResult | None = None
    tests: RunResult | None = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=cwd,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 1, "", f"Command not found: {exc}"
    except subprocess.TimeoutExpired:
        return 1, "", f"Command timed out after {_TIMEOUT}s: {' '.join(cmd)}"


def _parse_ruff_output(output: str) -> list[LintError]:
    """Parse ruff JSON output into LintError list."""
    errors: list[LintError] = []
    try:
        items = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return errors
    for item in items:
        loc = item.get("location", {})
        errors.append(LintError(
            file=item.get("filename", ""),
            line=loc.get("row", 0),
            col=loc.get("column", 0),
            code=item.get("code", ""),
            message=item.get("message", ""),
        ))
    return errors


_MYPY_LINE_RE = re.compile(r"^(.+):(\d+):\s*(?:error|note):\s*(.+)$")


def _parse_mypy_output(output: str) -> list[TypeCheckError]:
    """Parse mypy stdout into TypeCheckError list (errors only, not notes)."""
    errors: list[TypeCheckError] = []
    for line in output.splitlines():
        # Skip summary lines like "Found N errors in N files"
        if re.match(r"^Found \d+ error", line) or line.startswith("Success:"):
            continue
        m = _MYPY_LINE_RE.match(line)
        if m and ": error:" in line:
            errors.append(TypeCheckError(
                file=m.group(1),
                line=int(m.group(2)),
                message=m.group(3),
            ))
    return errors


_PYTEST_FAIL_RE = re.compile(r"^FAILED (.+) - (.+)$")


def _parse_pytest_output(output: str) -> list[FailedTest]:
    """Parse pytest stdout into FailedTest list."""
    failures: list[FailedTest] = []
    for line in output.splitlines():
        m = _PYTEST_FAIL_RE.match(line.strip())
        if m:
            failures.append(FailedTest(test_name=m.group(1), reason=m.group(2)))
    return failures


# ── Public API ────────────────────────────────────────────────────────────────

def run_lint(files: list[str]) -> LintResult:
    """Run ruff check on the given files. Returns structured LintResult."""
    if not files:
        return LintResult(passed=True)

    returncode, stdout, stderr = _run(
        ["ruff", "check", "--output-format=json", "--", *files]
    )
    raw = stdout or stderr
    errors = _parse_ruff_output(stdout)
    return LintResult(passed=returncode == 0, errors=errors, raw=raw)


def run_typecheck(files: list[str]) -> TypeCheckResult:
    """Run mypy on the given files. Returns structured TypeCheckResult."""
    if not files:
        return TypeCheckResult(passed=True)

    returncode, stdout, stderr = _run(["mypy", "--", *files])
    raw = stdout + stderr
    errors = _parse_mypy_output(stdout)
    return TypeCheckResult(passed=returncode == 0, errors=errors, raw=raw)


def run_tests(test_dir: str) -> RunResult:
    """Run pytest in test_dir. Returns structured RunResult."""
    returncode, stdout, stderr = _run(
        ["python", "-m", "pytest", test_dir, "-v", "--tb=short"],
        cwd=Path(test_dir).parent if Path(test_dir).is_dir() else None,
    )
    raw = stdout + stderr
    failures = _parse_pytest_output(stdout)
    return RunResult(passed=returncode == 0, failures=failures, raw=raw)


def run_all(files_modified: list[str], test_dir: str) -> ExecutionResult:
    """Run lint → typecheck → tests in sequence. Stops early on lint failure."""
    lint = run_lint(files_modified)
    if not lint.passed:
        return ExecutionResult(passed=False, lint=lint)

    typecheck = run_typecheck(files_modified)
    if not typecheck.passed:
        return ExecutionResult(passed=False, lint=lint, typecheck=typecheck)

    tests = run_tests(test_dir)
    return ExecutionResult(
        passed=tests.passed,
        lint=lint,
        typecheck=typecheck,
        tests=tests,
    )
