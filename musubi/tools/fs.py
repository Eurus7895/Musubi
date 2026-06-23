"""Workspace-scoped filesystem + command tools.

musubi-tier: substrate
expires-when: never — these are the file ops MCP clients need to
  do real work; deterministic, vendor-neutral, HI #1-compliant.

Every operation resolves paths against `_workspace_root()` and refuses
anything that escapes it. There is intentionally no "is this command
dangerous?" heuristic — the user gave the model an API key + this
catalog, and detection would just generate false confidence. The
substrate's job here is path-safety and audit, not paternalism.

Public API (all return JSON-shaped dicts with `status`):

    read_file(path)                                  → {"status": "ok", "content": str, "bytes": int}
    write_file(path, content, create_parents=True)   → {"status": "ok", "bytes_written": int}
    edit_file(path, old_string, new_string, replace_all=False)
                                                     → {"status": "ok", "replacements": int}
    run_command(command, timeout_seconds=60, cwd=None)
                                                     → {"status": "ok", "stdout": str, "stderr": str, "exit_code": int}

On any error each returns `{"status": "error", "error": str}` — the
MCP boundary serialises the dict to JSON for the caller.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Cap reads + command output at ~5 MB so a single tool call can't OOM
# the model's context or the harness process. Tunable later if real
# use cases hit it.
_MAX_READ_BYTES = 5 * 1024 * 1024
_MAX_OUTPUT_CHARS = 1_000_000


# ── Path resolution ────────────────────────────────────────────────────────


def _workspace_root() -> Path:
    """Workspace root the tools resolve against.

    Resolution order matches the rest of the harness:
      1. `MUSUBI_ROOT` env var (the extension's convention).
      2. Current working directory of the server process (set when the
         user starts the MCP server — typically the repo root).
    """
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def resolve_path(path: str) -> Path:
    """Resolve `path` against the workspace root, rejecting traversal.

    Accepts workspace-relative or absolute paths. Raises PermissionError
    when the resolved target escapes the workspace root (the only
    safety boundary the tools enforce).
    """
    if not path:
        raise ValueError("path must be a non-empty string")
    root = _workspace_root()
    p = Path(path)
    candidate = (p if p.is_absolute() else root / p).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            f"path {path!r} resolves to {candidate}, which is outside "
            f"the workspace root {root}"
        ) from exc
    return candidate


# ── Tool implementations ───────────────────────────────────────────────────


def read_file(path: str) -> dict[str, Any]:
    """Read a text file from the workspace."""
    try:
        target = resolve_path(path)
    except (ValueError, PermissionError) as exc:
        return _error(exc)
    if not target.exists():
        return {"status": "error", "error": f"file not found: {path}"}
    if not target.is_file():
        return {"status": "error", "error": f"not a regular file: {path}"}
    try:
        size = target.stat().st_size
        if size > _MAX_READ_BYTES:
            return {
                "status": "error",
                "error": (
                    f"file too large: {size} bytes (cap "
                    f"{_MAX_READ_BYTES}); read a slice with run_command "
                    f"if you really need this"
                ),
            }
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return {"status": "error", "error": f"non-utf-8 file: {exc}"}
    except OSError as exc:
        return _error(exc)
    _audit("read", target)
    return {"status": "ok", "content": content, "bytes": len(content.encode("utf-8"))}


def write_file(
    path: str,
    content: str,
    *,
    create_parents: bool = True,
) -> dict[str, Any]:
    """Write `content` to `path`, replacing the file if it exists.

    Creates parent directories by default so the model doesn't need a
    separate mkdir tool. Pass `create_parents=False` to require the
    parent directory exist already.
    """
    try:
        target = resolve_path(path)
    except (ValueError, PermissionError) as exc:
        return _error(exc)
    if target.exists() and target.is_dir():
        return {"status": "error", "error": f"path is a directory: {path}"}
    parent = target.parent
    if not parent.exists():
        if not create_parents:
            return {
                "status": "error",
                "error": f"parent directory does not exist: {parent}",
            }
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _error(exc)
    try:
        encoded = content.encode("utf-8")
        target.write_bytes(encoded)
    except OSError as exc:
        return _error(exc)
    _audit("write", target, f"bytes={len(encoded)}")
    return {"status": "ok", "bytes_written": len(encoded)}


def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Find `old_string` in `path` and replace it with `new_string`.

    Defaults to "must be unique" semantics — the classic source of
    surprise in search-and-replace tools is matching more than the
    caller intended. When `replace_all=True`, every occurrence is
    replaced and the count is returned.
    """
    if not old_string:
        return {"status": "error", "error": "old_string must be non-empty"}
    try:
        target = resolve_path(path)
    except (ValueError, PermissionError) as exc:
        return _error(exc)
    if not target.is_file():
        return {"status": "error", "error": f"file not found: {path}"}
    try:
        text = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return _error(exc)
    occurrences = text.count(old_string)
    if occurrences == 0:
        return {"status": "error", "error": "old_string not found in file"}
    if not replace_all and occurrences > 1:
        return {
            "status": "error",
            "error": (
                f"old_string occurs {occurrences} times in the file; "
                f"pass replace_all=true or include more surrounding context "
                f"to make the match unique"
            ),
        }
    new_text = text.replace(old_string, new_string) if replace_all \
        else text.replace(old_string, new_string, 1)
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return _error(exc)
    replacements = occurrences if replace_all else 1
    _audit("edit", target, f"replacements={replacements}")
    return {"status": "ok", "replacements": replacements}


def run_command(
    command: str,
    *,
    timeout_seconds: int = 60,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Run `command` via `sh -c`. Returns stdout/stderr/exit_code.

    Shell features (pipes, redirects, `&&`, env vars) work because
    `shell=True` is what makes this a useful tool. cwd defaults to the
    workspace root; an explicit cwd is resolved against the workspace
    root and rejected if it escapes.
    """
    if not command or not command.strip():
        return {"status": "error", "error": "command must be non-empty"}
    work_dir = _workspace_root()
    if cwd:
        try:
            resolved = resolve_path(cwd)
        except (ValueError, PermissionError) as exc:
            return _error(exc)
        if not resolved.is_dir():
            return {"status": "error", "error": f"cwd is not a directory: {cwd}"}
        work_dir = resolved
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        partial_out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        partial_err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        _audit("run_timeout", work_dir, f"command={command!r}")
        return {
            "status": "error",
            "error": f"command timed out after {timeout_seconds}s",
            "stdout": _truncate(partial_out),
            "stderr": _truncate(partial_err),
        }
    except OSError as exc:
        return _error(exc)
    _audit("run", work_dir, f"command={command!r} exit={result.returncode}")
    return {
        "status": "ok",
        "stdout": _truncate(result.stdout),
        "stderr": _truncate(result.stderr),
        "exit_code": result.returncode,
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def _error(exc: BaseException) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _truncate(text: str) -> str:
    """Cap output to keep tool results from blowing context budget."""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    head = _MAX_OUTPUT_CHARS // 2
    tail = _MAX_OUTPUT_CHARS - head
    return (
        text[:head]
        + f"\n\n[truncated by harness — {len(text)} chars total; "
        f"showing first {head} + last {tail}]\n\n"
        + text[-tail:]
    )


def _audit(action: str, target: Path, detail: str = "") -> None:
    """Stderr log every fs/command call.

    Deliberately not yet wired to a SQL audit table — the existing
    `agent_cycles` / `stage_metrics` patterns are for stage-shaped
    work, and fs tool use is per-call. If demand emerges, a dedicated
    `fs_audit` table is a small follow-up.
    """
    suffix = f" {detail}" if detail else ""
    print(f"[harness.tools.fs] {action} {target}{suffix}", file=sys.stderr)
