# Async Patterns — Python Skill Reference

Use this reference when implementing `asyncio`-based I/O, concurrent subprocess
calls, or any code that uses `async`/`await`.

---

## When to Use Async

Use `asyncio` only when you have genuine I/O concurrency — multiple network calls,
file reads, or subprocesses that can overlap. For CPU-bound work or sequential I/O,
plain synchronous code is simpler and easier to test.

The harness currently uses synchronous subprocess calls. Only introduce `asyncio`
if the executor needs to run lint, typecheck, and tests concurrently.

---

## Async Function Signature

```python
import asyncio

async def run_checks(files: list[str]) -> list[LintResult]:
    tasks = [run_lint(f) for f in files]
    return await asyncio.gather(*tasks)
```

---

## Concurrent Subprocess with asyncio

```python
import asyncio

async def run_lint_async(file_path: str) -> LintResult:
    proc = await asyncio.create_subprocess_exec(
        "ruff", "check", "--output-format=json", file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return LintResult(ok=False, errors=["timeout after 30s"])
    return parse_ruff_output(stdout.decode())
```

Key points:
- `asyncio.create_subprocess_exec` — not `shell=True`, args as positional params
- `asyncio.wait_for` for timeout — raises `asyncio.TimeoutError`, not `subprocess.TimeoutExpired`
- Always kill and drain the process on timeout

---

## Running Async from Sync Code

```python
result = asyncio.run(run_checks(files))
```

Use `asyncio.run` at the top-level entry point only. Do not nest `asyncio.run` calls.

---

## Async Context Managers

```python
async with aiosqlite.connect(db_path) as conn:
    await conn.execute("SELECT ...", params)
```

If adding async DB access, use `aiosqlite`. The sync `sqlite3` module blocks the
event loop — do not use it inside async functions.

---

## Error Handling in Async

```python
async def run_all(files: list[str]) -> list[LintResult]:
    results = await asyncio.gather(*[run_lint_async(f) for f in files], return_exceptions=True)
    output = []
    for r in results:
        if isinstance(r, Exception):
            output.append(LintResult(ok=False, errors=[str(r)]))
        else:
            output.append(r)
    return output
```

Use `return_exceptions=True` in `gather` so one failed task does not cancel the rest.

---

## Testing Async Code

```python
import pytest

@pytest.mark.asyncio
async def test_run_lint_async_timeout() -> None:
    result = await run_lint_async("/nonexistent/file.py")
    assert not result.ok
```

Requires `pytest-asyncio`. Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

---

## Anti-Patterns

```python
# wrong — blocks event loop
async def bad() -> str:
    time.sleep(5)          # use asyncio.sleep(5) instead
    return open("f").read()  # use aiofiles or run in executor

# wrong — nested asyncio.run
async def outer():
    asyncio.run(inner())   # raises RuntimeError

# wrong — fire-and-forget without tracking
async def bad():
    asyncio.create_task(risky())  # exception silently lost
```
