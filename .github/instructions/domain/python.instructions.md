---
applyTo: "**/*.py"
priority: P3
---

# Python Instructions — Domain Standard (P3)

## Version and Runtime

- Python 3.11+ features are available and encouraged.
- Use `match` statements for dispatch over string/enum values.
- Use `dataclasses` or `TypedDict` for structured data. No raw dicts for domain objects.

## Type Annotations

- All public functions and methods must be annotated.
- Use built-in generics: `list[str]`, `dict[str, int]`, `tuple[int, ...]`.
- Use `X | None` instead of `Optional[X]`.
- Use `X | Y` instead of `Union[X, Y]`.

```python
# correct
def validate(output: dict[str, object], agent: str) -> list[str]: ...

# wrong
from typing import Dict, List, Optional
def validate(output: Dict[str, object], agent: str) -> List[str]: ...
```

## Dataclasses

Use `@dataclass(frozen=True)` for value objects. Use `@dataclass` for mutable state.

```python
@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
```

## Error Handling

- Define custom exception classes for each domain error category.
- Raise early, catch late. Validate at boundaries, not deep in logic.
- Use `raise ... from err` to preserve exception chains.

```python
class SecretDetectedError(ValueError):
    pass

class SchemaValidationError(ValueError):
    pass
```

## Logging

- Use the standard `logging` module. No `print()` in library code.
- Logger per module: `logger = logging.getLogger(__name__)`
- Levels: `DEBUG` for trace, `INFO` for milestones, `WARNING` for recoverable
  issues, `ERROR` for failures that need attention.

## Subprocess

- Always `shell=False`.
- Always set `timeout`.
- Always capture `stdout` and `stderr` explicitly.

```python
result = subprocess.run(
    ["ruff", "check", "--output-format=json", str(path)],
    capture_output=True,
    text=True,
    timeout=30,
    shell=False,
)
```

## File I/O

- Use `pathlib.Path` everywhere. No `os.path` string manipulation.
- Use context managers (`with`) for all file handles.
- Validate paths against allowed base before opening (see P1 security rules).

## Testing

- One test file per source module: `tests/test_{module}.py`.
- Use `pytest`. No `unittest.TestCase` unless there's a specific reason.
- Use `pytest.fixture` for shared setup.
- Name tests: `test_{function}_{condition}_{expected}`.
- Use `pytest.raises` for exception assertions.

## Imports

Order: stdlib → third-party → local. One blank line between groups.
No wildcard imports (`from module import *`).

## Constants

Use `ALL_CAPS` for module-level constants. Define them at the top of the module,
after imports.

```python
MAX_RETRY_ATTEMPTS = 3
INJECTION_PATTERNS = [
    "ignore your instructions",
    "you are now",
    "forget previous",
]
```
