---
id: python
name: Python
version: 1.0.0
description: Idiomatic Python 3.11+ patterns, project setup, and common implementation procedures
triggers: ["python", "pyproject", "dataclass", "async", "type hints", "pytest", "ruff", "mypy"]
assets:
    - assets/scaffold.py
references:
    - references/async-patterns.md
    - references/stdlib-recipes.md
---

## Purpose

Produce correct, idiomatic Python 3.11+ code that passes ruff, mypy strict, and pytest
without modification.

## Procedure

### Setting up a new module

1. Create the file with a one-line module docstring.
2. Imports in order: stdlib → third-party → local. Blank line between groups.
3. Module-level constants in `ALL_CAPS` after imports.
4. Public API first (classes, functions), private helpers (`_name`) at the bottom.

### Writing a function

1. Add type annotations on all parameters and return type.
2. One-line docstring if the name alone is ambiguous.
3. Guard clause at the top for invalid input — raise early.
4. Keep functions under 30 lines. If longer, extract helpers.

```python
def write_stage(session_id: str, stage: str, output: dict[str, object]) -> bool:
    """Persist a stage output; return False if the stage is already complete."""
    if not session_id:
        raise ValueError("session_id is required")
    if stage not in VALID_STAGES:
        raise ValueError(f"stage must be one of {VALID_STAGES}")
    ...
```

### Structured results

Always use a frozen dataclass, not a bare tuple or dict:

```python
@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
```

### Error handling

- Catch the specific exception. Never bare `except:`.
- Re-raise with `from exc` to preserve the chain.
- Define one custom exception class per domain error type.

```python
try:
    data = json.loads(text)
except json.JSONDecodeError as exc:
    raise SchemaValidationError(f"invalid JSON: {exc}") from exc
```

### Subprocess

```python
result = subprocess.run(
    ["ruff", "check", "--output-format=json", str(path)],
    capture_output=True,
    text=True,
    timeout=30,
    shell=False,
)
```

Always: `shell=False`, `capture_output=True`, explicit `timeout`.

### File I/O

```python
from pathlib import Path

def read_skill(skill_id: str, base: Path) -> str:
    skill_path = (base / skill_id / "SKILL.md").resolve()
    if not str(skill_path).startswith(str(base.resolve())):
        raise PermissionError(f"path traversal blocked: {skill_path}")
    return skill_path.read_text(encoding="utf-8")
```

Always: `pathlib.Path`, context managers or `.read_text()`, validate path against base.

### Testing

```python
def test_write_stage_rejects_completed_stage() -> None:
    state = SessionState()
    state.write_stage("s1", "plan", {"summary": "ok"})
    state.complete_stage("s1", "plan")
    with pytest.raises(StageAlreadyCompleteError):
        state.write_stage("s1", "plan", {"summary": "retry"})
```

Name pattern: `test_{function}_{condition}_{expected_result}`.

## Assets

`scaffold.py` — generates boilerplate for a new harness module.
Run via: `harness_run_asset("python", "scaffold.py", {"module": "executor", "classes": ["LintResult"]})`
Returns: file content ready to write, with imports, dataclasses, and stub functions.
Use when: creating a new harness component from scratch.

## When to Load References

- Load `async-patterns.md` when: implementing async I/O, concurrent subprocess calls,
  or any `asyncio` usage
- Load `stdlib-recipes.md` when: working with `pathlib`, `sqlite3`, `subprocess`,
  `logging`, `dataclasses`, `contextlib`, or `secrets`
