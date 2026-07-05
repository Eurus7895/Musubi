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
    append_file(path, content, create_parents=True,
                expected_offset=None)                → {"status": "ok", "bytes_written": int, "total_bytes": int}
    edit_file(path, old_string, new_string, replace_all=False)
                                                     → {"status": "ok", "replacements": int}
    run_command(command, timeout_seconds=60, cwd=None)
                                                     → {"status": "ok", "stdout": str, "stderr": str, "exit_code": int}
    glob(pattern="**/*", path=None)                  → {"status": "ok", "matches": list[str], "count": int, "truncated": bool}
    grep(pattern, path=None, file_glob=None, ignore_case=False)
                                                     → {"status": "ok", "matches": list[dict], "count": int, "files_scanned": int, "truncated": bool}

On any error each returns `{"status": "error", "error": str}` — the
MCP boundary serialises the dict to JSON for the caller.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Cap reads + command output at ~5 MB so a single tool call can't OOM
# the model's context or the Musubi process. Tunable later if real
# use cases hit it.
_MAX_READ_BYTES = 5 * 1024 * 1024
_MAX_OUTPUT_CHARS = 1_000_000

# Read-only discovery (glob/grep): directories never worth walking for
# source search, plus result caps so a discovery call over a huge tree
# cannot blow the model's context budget.
_DISCOVERY_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "target", "dist", "build", ".next", ".tauri", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".idea", ".gradle",
})
_MAX_GLOB_MATCHES = 2000
_MAX_GREP_MATCHES = 300
_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024
_GREP_MAX_LINE_CHARS = 500


# ── Path resolution ────────────────────────────────────────────────────────


def _workspace_root() -> Path:
    """Workspace root the tools resolve against.

    Resolution order matches the rest of the Musubi:
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


def append_file(
    path: str,
    content: str,
    *,
    create_parents: bool = True,
    expected_offset: int | None = None,
) -> dict[str, Any]:
    """Append `content` to `path`, creating the file when needed.

    When `expected_offset` is provided, it must match the target's current byte
    size before the append happens. This lets chunked writers detect dropped or
    reordered chunks without turning the tool into a heavier file-session API.
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
        current_size = target.stat().st_size if target.exists() else 0
        if expected_offset is not None and current_size != expected_offset:
            return {
                "status": "error",
                "error": (
                    f"expected offset {expected_offset} but current size "
                    f"{current_size}"
                ),
            }
        encoded = content.encode("utf-8")
        with target.open("ab") as handle:
            handle.write(encoded)
        total = current_size + len(encoded)
    except OSError as exc:
        return _error(exc)
    _audit("append", target, f"bytes={len(encoded)} total={total}")
    return {
        "status": "ok",
        "bytes_written": len(encoded),
        "total_bytes": total,
    }


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
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            # Never inherit the server's stdin: a command that reads stdin
            # (e.g. a bare `python` that drops into its REPL, or a mangled
            # `python -c`) would otherwise block until the timeout, stalling
            # the whole agent. Feed EOF so such commands exit immediately.
            stdin=subprocess.DEVNULL,
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


# ── Read-only discovery (Grep/Glob capabilities) ────────────────────────────


def glob(pattern: str = "**/*", *, path: str | None = None) -> dict[str, Any]:
    """List workspace files matching `pattern` (read-only discovery).

    `pattern` is matched with fnmatch against each file's workspace-relative
    POSIX path and its basename, so `*.py`, `gui/src/**`, and `**/*.jsx` all
    work; the whole-tree default `**/*` lists every file. `path` optionally
    scopes the search to a sub-directory. Heavy build/VCS directories
    (`.git`, `node_modules`, …) are never walked. Results are sorted and
    capped at `_MAX_GLOB_MATCHES`.
    """
    if not (pattern or "").strip():
        return {"status": "error", "error": "pattern must be non-empty"}
    try:
        base = resolve_path(path) if path else _workspace_root()
    except (ValueError, PermissionError) as exc:
        return _error(exc)
    if not base.is_dir():
        return {"status": "error", "error": f"not a directory: {path}"}
    matches: list[str] = []
    truncated = False
    try:
        for rel, _full in _iter_workspace_files(base, pattern):
            matches.append(rel)
            if len(matches) >= _MAX_GLOB_MATCHES:
                truncated = True
                break
    except OSError as exc:
        return _error(exc)
    matches.sort()
    _audit("glob", base, f"pattern={pattern!r} matches={len(matches)}")
    return {
        "status": "ok",
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
    }


def grep(
    pattern: str,
    *,
    path: str | None = None,
    file_glob: str | None = None,
    ignore_case: bool = False,
) -> dict[str, Any]:
    """Search workspace file contents for a regex (read-only).

    Returns up to `_MAX_GREP_MATCHES` `{"file","line","text"}` hits. `path`
    scopes the search to a sub-directory; `file_glob` limits which files are
    scanned (same fnmatch semantics as `glob`). Oversized, binary, or
    non-UTF-8 files are skipped silently. Heavy build/VCS directories are
    never walked.
    """
    if not pattern:
        return {"status": "error", "error": "pattern must be non-empty"}
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        return {"status": "error", "error": f"invalid regex: {exc}"}
    try:
        base = resolve_path(path) if path else _workspace_root()
    except (ValueError, PermissionError) as exc:
        return _error(exc)
    if not base.is_dir():
        return {"status": "error", "error": f"not a directory: {path}"}
    hits: list[dict[str, Any]] = []
    files_scanned = 0
    truncated = False
    try:
        for rel, full in _iter_workspace_files(base, file_glob):
            try:
                if full.stat().st_size > _GREP_MAX_FILE_BYTES:
                    continue
                text = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            files_scanned += 1
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    hits.append({
                        "file": rel,
                        "line": lineno,
                        "text": line[:_GREP_MAX_LINE_CHARS],
                    })
                    if len(hits) >= _MAX_GREP_MATCHES:
                        truncated = True
                        break
            if truncated:
                break
    except OSError as exc:
        return _error(exc)
    _audit("grep", base, f"pattern={pattern!r} hits={len(hits)} files={files_scanned}")
    return {
        "status": "ok",
        "matches": hits,
        "count": len(hits),
        "files_scanned": files_scanned,
        "truncated": truncated,
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def _iter_workspace_files(
    base: Path, file_glob: str | None
) -> Iterator[tuple[str, Path]]:
    """Yield `(rel_posix, full_path)` for files under `base`, pruning heavy
    build/VCS directories. When `file_glob` is set, only files whose relative
    POSIX path or basename fnmatches it are yielded."""
    root = _workspace_root()
    for dirpath, dirnames, filenames in os.walk(base):
        # Prune in place so os.walk never descends into skipped trees.
        dirnames[:] = sorted(d for d in dirnames if d not in _DISCOVERY_SKIP_DIRS)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(root).as_posix()
            except ValueError:
                continue
            if file_glob and not _glob_match(rel, name, file_glob):
                continue
            yield rel, full


def _glob_match(rel_posix: str, base_name: str, pattern: str) -> bool:
    """True when `pattern` selects a file. The whole-tree patterns match
    everything; otherwise fnmatch against the relative path or the basename."""
    if pattern in ("", "*", "**", "**/*"):
        return True
    return fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(base_name, pattern)


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
        + f"\n\n[truncated by Musubi — {len(text)} chars total; "
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
    print(f"[musubi.tools.fs] {action} {target}{suffix}", file=sys.stderr)
