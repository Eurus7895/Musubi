---
name: testing
description: pytest-based testing procedures for harness components — unit, integration, and correction loop tests. Use when the user is writing tests, fixtures, mocks, or asking about pytest or coverage.
---

## Purpose

Produce a complete, passing test suite for harness components using pytest.
Tests must be deterministic, isolated, and runnable with `pytest tests/` and no
external services.

## Procedure

### 1. Identify what to test

For each function or class being implemented, write tests that cover:
- **Happy path** — correct input, expected output
- **Failure path** — invalid input, expected exception or error result
- **Boundary conditions** — empty input, max values, None, zero

For harness components specifically, also cover:
- State transition rules (e.g., completed stage cannot be overwritten)
- Context firewall rules (e.g., planner sees no stage outputs)
- Secrets/injection scan triggers

### 2. File and naming conventions

```
tests/
    test_state.py          ← mirrors copilot-harness/state.py
    test_context_builder.py
    test_verifier.py
    test_correction_loop.py
    test_executor.py
```

Function name pattern: `test_{function}_{condition}_{expected}`

```python
def test_write_stage_completed_stage_raises()
def test_scan_injection_override_pattern_returns_true()
def test_build_context_planner_excludes_stage_outputs()
```

### 3. Structure every test the same way

```python
def test_write_stage_rejects_duplicate_attempt() -> None:
    # Arrange
    state = SessionState(session_id="abc123")
    state.write_stage("plan", {"summary": "ok"}, attempt=1)

    # Act / Assert
    with pytest.raises(StageAlreadyWrittenError):
        state.write_stage("plan", {"summary": "retry"}, attempt=1)
```

Arrange → Act → Assert. One assertion concept per test. No logic in tests.

### 4. Use fixtures for shared setup

```python
@pytest.fixture
def session(tmp_path: Path) -> SessionState:
    db_path = tmp_path / "test.db"
    return SessionState(db_path=str(db_path))
```

Use `tmp_path` (pytest built-in) for any test that needs a real file or DB.
Never share mutable state between tests via module-level variables.

### 5. Run coverage check

After writing tests, run:

```
harness_run_asset("testing", "coverage-check.py", {"test_dir": "tests/", "source_dir": "copilot-harness/"})
```

Returns per-module coverage. Target: 80% minimum per module.
If a module is below 80%, add tests for the uncovered branches before finishing.

## Assets

`coverage-check.py` — runs pytest with coverage and returns structured per-module report.
Input: `{"test_dir": "tests/", "source_dir": "copilot-harness/", "min_coverage": 80}`
Output: `{"ok": true, "modules": [{"name": "state", "coverage": 94, "missing_lines": []}]}`
Use when: verifying coverage before submitting code output.

## When to Load References

- Load `pytest-patterns.md` when: writing fixtures, parametrize, tmp_path, monkeypatch,
  or capsys usage; or when a test needs to check logs or stdout
- Load `mocking-guide.md` when: the code under test calls subprocess, sqlite3, file I/O,
  or any external dependency that must be replaced in tests
