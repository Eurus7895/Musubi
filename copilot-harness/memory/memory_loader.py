"""Memory injection — loads Tier 1 and Tier 2 memory files into agent context.

harness-tier: substrate
expires-when: never — MEMORY.md + tier-2 read path.


Tier 1 — MEMORY.md (~200 tokens, always injected by harness_read_stage)
    Pointers index: what decisions were made, where Tier 2 knowledge lives.

Tier 2 — .github/memory/*.md (loaded on demand via get_tier2_entry)
    Distilled decisions and past failure patterns. Never auto-loaded in full;
    agents request specific entries via harness_get_reference("memory", name).

Tier 3 — cross-session query (Week 4 Day 4)
    query_sessions() searches the sessions table for requests / review excerpts
    matching a keyword. Returns session IDs + short excerpts, never full
    transcripts, to keep the main-agent context small.

Zero LLM calls. Pure file I/O + structured DB reads.

Public API:
    get_tier1_index(repo_root?) → str | None
    get_tier2_entry(name, repo_root?) → str | None
    list_tier2_entries(repo_root?) → list[str]
    get_memory_context(repo_root?) → dict
    query_sessions(query, limit=?, db_path=?) → list[dict]
"""

import json
from pathlib import Path

from storage import db as _db

# memory_loader.py lives in copilot-harness/memory/ → repo root is two levels up
_DEFAULT_REPO_ROOT = Path(__file__).parent.parent.parent

_MEMORY_DIR = ".github/memory"
_TIER1_FILE = "MEMORY.md"


def _memory_dir(repo_root: Path | None = None) -> Path:
    return (repo_root or _DEFAULT_REPO_ROOT) / _MEMORY_DIR


def get_tier1_index(repo_root: Path | None = None) -> str | None:
    """Return MEMORY.md content (Tier 1 index), or None if not found."""
    path = _memory_dir(repo_root) / _TIER1_FILE
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def get_tier2_entry(name: str, repo_root: Path | None = None) -> str | None:
    """Return a named Tier 2 memory file, or None if not found.

    name must be a plain filename (no path separators). Returns None for
    path-traversal attempts or missing files.
    """
    if "/" in name or "\\" in name or ".." in name:
        return None
    path = _memory_dir(repo_root) / name
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def list_tier2_entries(repo_root: Path | None = None) -> list[str]:
    """Return filenames of all Tier 2 memory files (excludes MEMORY.md)."""
    mem_dir = _memory_dir(repo_root)
    if not mem_dir.exists():
        return []
    return sorted(
        p.name
        for p in mem_dir.iterdir()
        if p.is_file() and p.name != _TIER1_FILE and p.suffix == ".md"
    )


def get_memory_context(repo_root: Path | None = None) -> dict:
    """Return a dict with Tier 1 index ready for injection into harness_read_stage.

    If MEMORY.md does not exist, returns an empty dict (no memory injected).

    Shape:
        { "tier1_index": "<MEMORY.md content>",
          "tier2_available": ["architecture.md", "failure-patterns.md"] }
    """
    tier1 = get_tier1_index(repo_root)
    if not tier1:
        return {}
    return {
        "tier1_index": tier1,
        "tier2_available": list_tier2_entries(repo_root),
    }


# ── Week 4 Day 4: cross-session memory query ────────────────────────────────

_EXCERPT_LEN = 160


def _extract_issue_snippets(review_json: str, max_snippets: int = 3) -> list[str]:
    """Pull short issue descriptions from a stored review output row."""
    try:
        review = json.loads(review_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(review, dict):
        return []
    issues = review.get("issues") or []
    snippets: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        desc = issue.get("description", "")
        if isinstance(desc, str) and desc:
            snippets.append(desc[:_EXCERPT_LEN])
        if len(snippets) >= max_snippets:
            break
    return snippets


def query_sessions(
    query: str,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[dict]:
    """Return prior sessions whose request or review output matches the query.

    Case-insensitive substring match. Each result is structured and capped —
    callers receive session IDs + short excerpts, never the raw transcript.

    Result shape:
        {
            "session_id": "abc123",
            "request": "<full request>",
            "created_at": "<iso>",
            "match_source": "request" | "review" | "both",
            "review_snippets": ["...", ...]   # present when review matched
        }

    Useful for an agent that wants to answer "has this class of task come up
    before?" without re-reading the whole DB.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []

    _db.init_db(db_path)
    sessions = _db.get_all_sessions(db_path)
    results: list[dict] = []

    for session in sessions:
        sid = session["session_id"]
        request_text = session.get("request", "") or ""
        request_matches = needle in request_text.lower()

        review_row = _db.get_latest_written_stage_row(sid, "review", db_path)
        review_matches = False
        review_snippets: list[str] = []
        if review_row and review_row.get("output"):
            output = review_row["output"]
            if needle in output.lower():
                review_matches = True
                review_snippets = _extract_issue_snippets(output)

        if not (request_matches or review_matches):
            continue

        source = (
            "both" if request_matches and review_matches
            else ("request" if request_matches else "review")
        )
        result: dict = {
            "session_id": sid,
            "request": request_text[:_EXCERPT_LEN * 2],
            "created_at": session.get("created_at"),
            "match_source": source,
        }
        if review_snippets:
            result["review_snippets"] = review_snippets
        results.append(result)
        if len(results) >= limit:
            break

    return results
