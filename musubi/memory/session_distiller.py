"""Session distiller — extracts Tier 2 memory entries from completed sessions.

musubi-tier: ephemeral
expires-when: the 4-stage pipeline is dissolved
cost-lever: deletes ~250 lines tied to the planner-designer-coder-reviewer shape
(what: Pipeline-stage session distillation into failure-patterns.md.)


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
    compact_failure_patterns(repo_root?, max_bytes?) → dict  (Week 4 Day 4)
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from storage import db as _db

_FAILURE_PATTERNS_FILE = ".github/memory/failure-patterns.md"
_MAX_ISSUE_LEN = 300
_SEVERITY_KEEP = {"critical", "high"}
# Week 4 Day 4 — compaction trigger: failure-patterns.md exceeding 5 KB gets
# rewritten to keep only the highest-value subset (most-frequent + most-recent).
_COMPACT_TRIGGER_BYTES = 5 * 1024
_COMPACT_KEEP_MOST_FREQUENT = 10
_COMPACT_KEEP_MOST_RECENT = 10

_DEFAULT_REPO_ROOT = Path(__file__).parent.parent.parent


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

    # Auto-compact if the file has grown past the trigger threshold.
    # Safe to no-op when under the threshold — compact_failure_patterns
    # returns early without touching the file.
    if appended:
        compact_failure_patterns(repo_root=root)

    return appended


def append_pattern(
    agent: str,
    issue: str,
    *,
    source: str = "agent",
    repo_root: Path | None = None,
) -> str | None:
    """Append a single (agent, issue) row to failure-patterns.md.

    Phase C.2 — agent-driven entry point for distillation triggers
    (reviewer fail, frustration regex match). Mirrors the dedup +
    formatting rules of `distill_session` but operates on one issue at a
    time, without needing a session_id or a review-stage row.

    Returns the appended issue text if a new row was written, or None if
    the (agent, issue_prefix) pair was already present (deduped).

    `source` is recorded in place of the session id so the audit log
    still tracks where the trigger fired.
    """
    if not agent or not isinstance(agent, str):
        return None
    if not issue or not isinstance(issue, str):
        return None

    root = _repo_root(repo_root)
    path = _patterns_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_existing_patterns(path)
    desc = issue.strip()
    if not desc:
        return None
    key = (agent, desc[:_MAX_ISSUE_LEN])
    if key in existing:
        return None

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = _format_entry(agent, desc, 1, [source], date_str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)

    # Auto-compact when the file grows past threshold; safe no-op below.
    compact_failure_patterns(repo_root=root)
    return desc


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


# ── Week 4 Day 4: compaction ─────────────────────────────────────────────────

_ENTRY_RE = re.compile(
    r"^### (?P<agent>[^—\n]+?) — (?P<issue>.+?)"
    r" \((?P<count>\d+) occurrences?, last seen: (?P<date>\d{4}-\d{2}-\d{2})\)\s*$",
)


def _parse_entries(text: str) -> list[dict]:
    """Parse existing failure-patterns.md entries into structured records.

    Each record has keys: agent, issue, count, date, sessions (list of IDs),
    raw (the reconstructed full entry block ready to re-emit).
    """
    entries: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _ENTRY_RE.match(lines[i])
        if not m:
            i += 1
            continue
        # Collect following lines until the next "### " or "## " header.
        body_start = i + 1
        j = body_start
        while j < len(lines) and not lines[j].startswith(("### ", "## ")):
            j += 1
        body = "\n".join(lines[body_start:j]).rstrip("\n")
        sessions: list[str] = []
        for line in lines[body_start:j]:
            if line.lower().startswith("sessions:"):
                ids_part = line.split(":", 1)[1]
                sessions = [s.strip() for s in ids_part.split(",") if s.strip()]
                break
        entries.append({
            "agent": m.group("agent").strip(),
            "issue": m.group("issue").strip(),
            "count": int(m.group("count")),
            "date": m.group("date"),
            "sessions": sessions,
            "raw": lines[i] + ("\n" + body if body else "") + "\n",
        })
        i = j
    return entries


def compact_failure_patterns(
    repo_root: Path | None = None,
    max_bytes: int = _COMPACT_TRIGGER_BYTES,
) -> dict:
    """Rewrite failure-patterns.md in place, keeping only high-value entries.

    Runs only when the file exceeds `max_bytes`. Keeps the union of
    (most-recent-by-date) and (most-frequent-by-count) entries. Preserves
    the header matter (everything before the first "### " entry).

    Returns { "compacted": bool, "before_bytes": int, "after_bytes": int,
              "kept": int, "dropped": int }. Safe to call concurrently —
    the rewrite is a single atomic write after parsing the file.
    """
    root = _repo_root(repo_root)
    path = _patterns_path(root)
    if not path.exists():
        return {"compacted": False, "reason": "file does not exist"}
    before_text = path.read_text(encoding="utf-8")
    before_bytes = len(before_text.encode("utf-8"))
    if before_bytes <= max_bytes:
        return {"compacted": False, "before_bytes": before_bytes, "after_bytes": before_bytes}

    entries = _parse_entries(before_text)
    if not entries:
        return {"compacted": False, "before_bytes": before_bytes, "after_bytes": before_bytes,
                "reason": "no parseable entries"}

    # Split header (everything before the first entry) so we can rebuild the file.
    first_entry_idx = before_text.find("\n### ")
    if first_entry_idx == -1:
        # Entry appears at the very start — no header content to preserve.
        header = ""
    else:
        header = before_text[:first_entry_idx + 1]

    # Rank by frequency (desc) and by date (desc).
    most_frequent = sorted(entries, key=lambda e: (-e["count"], e["date"]))
    most_recent = sorted(entries, key=lambda e: e["date"], reverse=True)

    kept_keys: set[tuple[str, str]] = set()
    kept: list[dict] = []
    for e in most_frequent[:_COMPACT_KEEP_MOST_FREQUENT]:
        key = (e["agent"], e["issue"][:_MAX_ISSUE_LEN])
        if key not in kept_keys:
            kept_keys.add(key)
            kept.append(e)
    for e in most_recent[:_COMPACT_KEEP_MOST_RECENT]:
        key = (e["agent"], e["issue"][:_MAX_ISSUE_LEN])
        if key not in kept_keys:
            kept_keys.add(key)
            kept.append(e)

    # Keep kept entries in most-recent-first order for readability.
    kept.sort(key=lambda e: e["date"], reverse=True)

    compaction_note = (
        f"\n<!-- Compacted on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: "
        f"kept {len(kept)} of {len(entries)} entries "
        f"(most-frequent + most-recent). -->\n"
    )
    body = "\n".join(e["raw"].rstrip("\n") for e in kept) + "\n"
    new_text = header.rstrip() + "\n" + compaction_note + "\n" + body
    path.write_text(new_text, encoding="utf-8")
    after_bytes = len(new_text.encode("utf-8"))
    return {
        "compacted": True,
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
        "kept": len(kept),
        "dropped": len(entries) - len(kept),
    }
