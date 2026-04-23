# stdlib Recipes — Python Skill Reference

Correct usage patterns for stdlib modules used throughout the harness.
Load this reference when implementing any of the modules listed below.

---

## pathlib

```python
from pathlib import Path

BASE = Path(__file__).parent.resolve()

def safe_read(relative: str) -> str:
    target = (BASE / relative).resolve()
    # guard against path traversal
    if not str(target).startswith(str(BASE)):
        raise PermissionError(f"path outside base: {target}")
    return target.read_text(encoding="utf-8")
```

- Always `.resolve()` before comparison
- Use `/` operator for joins, not `os.path.join`
- `.read_text(encoding="utf-8")` and `.write_text(content, encoding="utf-8")`
- `.exists()`, `.is_file()`, `.is_dir()` before operating
- `Path.glob("**/*.py")` for recursive file search

---

## sqlite3

```python
import sqlite3
from contextlib import closing

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def fetch_stage(conn: sqlite3.Connection, session_id: str, stage: str) -> dict | None:
    with closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT output FROM stage_outputs WHERE session_id = ? AND stage = ? ORDER BY attempt DESC LIMIT 1",
            (session_id, stage),
        )
        row = cur.fetchone()
    return dict(row) if row else None
```

- `conn.row_factory = sqlite3.Row` — access columns by name
- Always use `?` placeholders — never f-strings or `.format()`
- `closing()` context manager on cursors
- `conn.commit()` after writes; `conn.rollback()` in except blocks
- Use `INSERT OR IGNORE` / `INSERT OR REPLACE` for idempotent inserts

---

## subprocess

```python
import subprocess
from dataclasses import dataclass

@dataclass(frozen=True)
class ProcResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int

def run(args: list[str], timeout: int = 30) -> ProcResult:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return ProcResult(ok=False, stdout="", stderr="timeout", returncode=-1)
    except FileNotFoundError as exc:
        return ProcResult(ok=False, stdout="", stderr=str(exc), returncode=-1)
    return ProcResult(ok=r.returncode == 0, stdout=r.stdout, stderr=r.stderr, returncode=r.returncode)
```

Never omit: `shell=False`, `timeout`, `capture_output=True`.

---

## logging

```python
import logging

logger = logging.getLogger(__name__)

# module init — not basicConfig (that's for CLI entry points only)
logger.debug("context detail: %s", value)   # lazy %-formatting, not f-string
logger.info("session %s started", session_id)
logger.warning("attempt %d failed: %s", attempt, reason)
logger.error("unrecoverable: %s", exc, exc_info=True)
```

- One logger per module: `logging.getLogger(__name__)`
- Never `print()` in library code
- Use `%s` lazy formatting, not f-strings (avoids formatting cost when log level is off)
- Pass `exc_info=True` to include traceback in error logs

---

## dataclasses

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class LintResult:          # immutable value object
    ok: bool
    errors: list[str]

@dataclass
class SessionState:        # mutable state object
    session_id: str
    stages: dict[str, str] = field(default_factory=dict)
    attempt: int = 1
```

- `frozen=True` for result/value objects — prevents accidental mutation
- `field(default_factory=...)` for mutable defaults (never `stages: dict = {}`)
- Use `dataclasses.asdict(obj)` to convert to dict for JSON serialization

---

## contextlib

```python
from contextlib import contextmanager, suppress

@contextmanager
def db_transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise

# suppress specific expected errors
with suppress(FileNotFoundError):
    path.unlink()
```

- `@contextmanager` for custom context managers
- `suppress(ExcType)` to silently skip a specific exception — use sparingly

---

## secrets

```python
import secrets

token = secrets.token_hex(8)        # 8-byte hex → "a3f9c1d2e5b4..."
session_id = secrets.token_hex(4)   # 4-byte → 8-char hex ID
```

Always use `secrets` for any token, ID, or nonce that must be unpredictable.
Never use `random` for security-sensitive values.

---

## json

```python
import json

def safe_parse(text: str) -> dict[str, object]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaValidationError("expected JSON object")
    return data

# write with consistent formatting
json.dumps(data, indent=2, ensure_ascii=False)
```

- Always handle `json.JSONDecodeError` at the parse site
- Use `ensure_ascii=False` to preserve unicode in output
- `json.dumps` for serialization; never `str(dict)` (not valid JSON)
