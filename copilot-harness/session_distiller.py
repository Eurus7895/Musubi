"""Session distiller — extracts Tier 2 memory entries from completed sessions.

Reads structured review output and fail_patterns from the DB, then appends
distilled failure-pattern entries to .github/memory/failure-patterns.md.

Rules:
- Only processes sessions whose review stage has a written output.
- Only records issues with severity "critical" or "high".
- Deduplicates: an (agent, issue) pair is not appended if already present in the file.
- Keeps each entry under 500 tokens by truncating issue text at 300 chars.

Zero LLM calls. Pure text extraction from structured data.

Public API:
    distill_session(session_id, db_path?, repo_root?) → list[str]  (appended entries)
    distill_all_completed(db_path?, repo_root?) → dict[str, list[str]]
"""

from datetime import datetime, timezone
from pathlib import Path

from storage import db as _db

_FAILURE_PATTERNS_FILE = ".github/memory/failure-patterns.md"
_MAX_ISSUE_LEN = 300
_SEVERITY_KEEP = {"critical", "high"}

_DEFAULT_REPO_ROOT = Path(__file__).parent.parent


def _repo_root(override: Path | None) -> Path:
    return override or _DEFAULT_REPO_ROOT


def _patterns_path(repo_root: Path) -> Path:
    return repo_root / _FAILURE_PATTERNS_FILE


def _load_existing_patterns(path: Path) -> set[tuple[str, str]]:
    """Return (agent, issue_prefix) pairs already present in the file."""
    if not path.exists():
        return set()
    seen: set[tuple[str, str]] = set()
    current_agent: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            # Format: ### coder — <issue> (N occurrences, ...)
            parts = line[4:].split(" — ", 1)
            if len(parts) == 2:
                current_agent = parts[0].strip()
                issue_part = parts[1].split(" (")[0].strip()
                seen.add((current_agent, issue_part[:_MAX_ISSUE_LEN]))
    return seen


def _format_entry(
    agent: str,
    issue: str,
    count: int,
    session_ids: list[str],
    date_str: str,
) -> str:
    issue_short = issue[:_MAX_ISSUE_LEN]
    sessions_str = ", ".join(session_ids[:5])  # cap at 5 session IDs per entry
    return (
        f"\n### {agent} — {issue_short}"
        f" ({count} occurrence{'s' if count != 1 else ''}, last seen: {date_str})\n"
        f"Sessions: {sessions_str}\n"
    )


def distill_session(
    session_id: str,
    db_path: Path | None = None,
    repo_root: Path | None = None,
) -> list[str]:
    """Distill failure patterns from one completed session into failure-patterns.md.

    Returns list of issue strings that were appended (empty if nothing new).
    """
    import json

    root = _repo_root(repo_root)
    path = _patterns_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Pull review output for this session.
    row = _db.get_latest_written_stage_row(session_id, "review", db_path)
    if row is None:
        return []

    try:
        review = json.loads(row["output"])
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(review, dict) or review.get("status") == "pass":
        return []

    existing = _load_existing_patterns(path)
    appended: list[str] = []
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for issue in review.get("issues", []):
        if not isinstance(issue, dict):
            continue
        severity = issue.get("severity", "low")
        if severity not in _SEVERITY_KEEP:
            continue
        desc = issue.get("description", "").strip()
        if not desc:
            continue
        key = ("coder", desc[:_MAX_ISSUE_LEN])
        if key in existing:
            continue

        entry = _format_entry("coder", desc, 1, [session_id], date_str)
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
        existing.add(key)
        appended.append(desc)

    return appended


def distill_all_completed(
    db_path: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, list[str]]:
    """Distill all completed sessions that have a written review output.

    Returns mapping of session_id → list of appended issue strings.
    """
    _db.init_db(db_path)
    sessions = _db.get_all_sessions(db_path)
    results: dict[str, list[str]] = {}
    for session in sessions:
        sid = session["session_id"]
        appended = distill_session(sid, db_path, repo_root)
        if appended:
            results[sid] = appended
    return results
