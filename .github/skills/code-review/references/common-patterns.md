# Common Patterns and Anti-Patterns — Code Review Reference

Use this reference when you detect potential anti-patterns or want to verify
correct implementation of common patterns in the codebase.

---

## Patterns to Encourage

### Dataclass for Structured Results

```python
@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
```

Use this pattern for any function that returns multiple related values.
Anti-pattern: returning a bare tuple `(bool, list, list)` — unclear at call site.

---

### Early Return / Guard Clauses

```python
# correct — flat, readable
def process(data: dict | None) -> str:
    if data is None:
        return ""
    if not data.get("key"):
        raise ValueError("key required")
    return do_work(data)

# anti-pattern — deeply nested
def process(data):
    if data is not None:
        if data.get("key"):
            return do_work(data)
```

---

### Explicit Exception Chains

```python
# correct
try:
    result = json.loads(text)
except json.JSONDecodeError as exc:
    raise SchemaValidationError(f"invalid JSON: {exc}") from exc

# anti-pattern — loses original traceback
try:
    result = json.loads(text)
except json.JSONDecodeError:
    raise SchemaValidationError("invalid JSON")
```

---

### Context Manager for Resources

```python
# correct
with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    cursor.execute(...)

# anti-pattern — connection leak on exception
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute(...)
conn.close()
```

---

### Subprocess Safety

```python
# correct
result = subprocess.run(
    ["ruff", "check", file_path],
    capture_output=True,
    text=True,
    timeout=30,
    shell=False,
)

# anti-pattern — shell injection risk
result = subprocess.run(f"ruff check {file_path}", shell=True)
```

---

## Anti-Patterns to Flag

### God Function

A function that does more than one thing: validates input, transforms data,
writes to DB, and logs. Split into separate functions.

**Signal:** function > 30 lines, or > 2 levels of nesting, or name contains "and".

---

### Bare Except

```python
# anti-pattern — swallows all errors, including KeyboardInterrupt
try:
    do_thing()
except:
    pass

# correct
try:
    do_thing()
except SpecificError as exc:
    logger.warning("expected failure: %s", exc)
```

---

### Mutable Default Argument

```python
# anti-pattern — list shared across all calls
def append_item(item: str, items: list[str] = []) -> list[str]:
    items.append(item)
    return items

# correct
def append_item(item: str, items: list[str] | None = None) -> list[str]:
    if items is None:
        items = []
    items.append(item)
    return items
```

---

### String Formatting in SQL or Shell

See `owasp-top10.md` AA02. Always a `critical` severity finding.

---

### Silent State Mutation

```python
# anti-pattern — modifies input dict in place
def enrich(data: dict) -> None:
    data["enriched"] = True

# correct — return new object or document mutation clearly
def enrich(data: dict) -> dict:
    return {**data, "enriched": True}
```

---

### Magic Numbers / Strings

```python
# anti-pattern
if attempt > 3:
    escalate()

# correct
MAX_RETRY_ATTEMPTS = 3
if attempt > MAX_RETRY_ATTEMPTS:
    escalate()
```

---

### Ignoring Return Values

```python
# anti-pattern — error result discarded
harness_write_stage(session_id, "plan", output)

# correct — check result
result = harness_write_stage(session_id, "plan", output)
if not result.get("ok"):
    raise StageWriteError(result.get("message"))
```

---

## Correction Loop Specific Patterns

### Fix Instructions Must Be Specific

Anti-pattern: "improve error handling in executor.py"
Correct: "add `except subprocess.TimeoutExpired` handler in `executor.run_lint`
after line 45, log the timeout, and return `LintResult(ok=False, errors=['timeout'])`"

### Retry Context Must Be Minimal

Anti-pattern: passing full review JSON to Coder on retry
Correct: `context_builder` strips to `fix_instructions` list only — Coder
does not need (and must not see) the reviewer's reasoning or severity scores
