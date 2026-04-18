---
applyTo: "**/test_*.py,**/*_test.py"
priority: P3
description: pytest conventions, fixture design, isolation requirements, and test structure for the CopilotHarness project.
---

# Testing Instructions — Domain Standard (P3)

## Framework

- Use `pytest` exclusively. No `unittest.TestCase` subclasses.
- Use `pytest.fixture` for setup. Prefer function scope unless shared state is required.
- Use `tmp_path` (built-in fixture) for all file system operations — never use real paths.
- Use `pytest.mark.parametrize` for data-driven tests. One test body, many inputs.

## Isolation

Every test must be fully isolated:

- Each test gets its own SQLite database via `tmp_path` — never share a DB between tests.
- Never read from or write to the real `.github/` directory in tests.
- Never import production DB paths — always pass `db_path` explicitly.
- Tests must pass in any order and any subset.

```python
# correct
@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    db_module.init_db(p)
    return p

# wrong — shares state between tests
DB = Path("test.db")
```

## Naming

- Test files: `test_{module}.py`
- Test functions: `test_{what}_{condition}` or `test_{what}_raises_{error}`
- Fixtures: noun form matching what they produce (`session_id`, `db`, `full_session`)

```python
def test_write_stage_write_once(session_id: str, db: Path) -> None: ...
def test_write_stage_unknown_stage_raises(session_id: str, db: Path) -> None: ...
```

## Assertions

- One logical assertion per test. If a test needs multiple, split it.
- Use `pytest.raises(ExceptionType, match="pattern")` for error cases.
- Assert exact values, not just truthiness: `assert x == 3` not `assert x`.

## Coverage

- Every public function must have at least one test.
- Happy path + error path for every function that raises.
- Edge cases: empty inputs, None values, boundary conditions.

## Type hints

- All test functions and fixtures must have type annotations.
- Use `Path` not `str` for file paths.
- Fixture return types must be annotated.
