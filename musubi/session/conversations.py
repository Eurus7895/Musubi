"""Conversation message log (Phase C.1).

musubi-tier: substrate
expires-when: never — Conversation-message append-only store.


The agent runner replays prior chat turns on every user message
(locked decision: replay-on-each-turn). This module is the storage seam:
the runner appends `user` / `assistant` / `tool` rows as they happen and
fetches a token-budgeted, chronological history before the next
`vscode.lm.sendRequest`.

Truncation is **newest-first**: when a budget is set, older messages are
dropped first so the model always sees the most recent context. The
returned list is then reversed back into chronological order for prompt
construction. Phase C.2 layers reactive 80/90/99% compaction on top of
this; C.1 is the append + bounded-fetch primitive.

Token estimation mirrors `validation.verifier._CHARS_PER_TOKEN = 4` so a
budget enforced here matches the cap `verify_subagent_summary` uses
elsewhere. The harness has no real tokenizer; both call sites use the
same heuristic.

The harness treats `chat_id` as opaque — runners mint it (Phase B.2 used
a heuristic; Phase C.2 plugs a stable id). Roles are validated
fail-closed against `VALID_ROLES`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from storage import db
from validation.verifier import _CHARS_PER_TOKEN

# Closed enum — the agent runner never writes anything else, and an
# unknown role indicates a runner bug worth surfacing immediately.
VALID_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "tool", "system"}
)

# Per-turn replay budget. Was 100 k and dominated token spend on long chats —
# the agent replays the budgeted history on every user message, so a
# default that fills the entire window every turn is wasted spend. 50 k keeps
# enough recent context for typical multi-turn debugging while letting the
# 80%/90% reactive compaction in agentCore drop the rest.
DEFAULT_MAX_TOKENS: int = 50_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(text: str) -> int:
    """Char-based token estimate; mirrors verifier.py's heuristic.

    Returns at least 1 for any non-empty string so a single-character
    message still counts toward the budget.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ── append ───────────────────────────────────────────────────────────────────

def append_message(
    chat_id: str,
    role: str,
    content: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Append a message to `chat_id`.

    Returns ``{message_id, ts, tokens_estimate}``. Raises ``ValueError`` on
    empty `chat_id`, empty `content`, or unknown `role`. Roles outside
    `VALID_ROLES` are rejected fail-closed.
    """
    if not chat_id or not chat_id.strip():
        raise ValueError("chat_id must be non-empty")
    if role not in VALID_ROLES:
        raise ValueError(
            f"role must be one of {sorted(VALID_ROLES)}; got {role!r}"
        )
    if content is None or content == "":
        raise ValueError("content must be non-empty")

    ts = _now_iso()
    message_id = db.insert_conversation_message(
        chat_id=chat_id,
        role=role,
        content=content,
        ts=ts,
        db_path=db_path,
    )
    return {
        "message_id": message_id,
        "ts": ts,
        "tokens_estimate": estimate_tokens(content),
    }


# ── read ─────────────────────────────────────────────────────────────────────

def get_history(
    chat_id: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    role_filter: frozenset[str] | set[str] | list[str] | None = None,
    db_path: Path | None = None,
) -> dict:
    """Return a token-budgeted, chronological history for `chat_id`.

    Newest-first truncation: when the running token total would exceed
    `max_tokens`, older messages are dropped first. The returned list is
    then reversed back to chronological order.

    Edge case — a single message larger than the whole budget: still
    returned (the runner needs *something*). `truncated=True` flags it.

    Returns::

        {
          messages: [{id, role, content, ts}, ...],   # chronological
          total_tokens: int,                          # of returned messages
          truncated:   bool,                          # any message dropped?
          dropped_count: int,
        }
    """
    if max_tokens < 0:
        raise ValueError("max_tokens must be >= 0")
    if not chat_id:
        return _empty_history()

    allowed = (
        frozenset(role_filter) if role_filter is not None else None
    )
    if allowed is not None:
        unknown = allowed - VALID_ROLES
        if unknown:
            raise ValueError(
                f"role_filter contains unknown roles: {sorted(unknown)}"
            )

    rows = db.get_conversation_messages(chat_id, db_path=db_path)
    if allowed is not None:
        rows = [r for r in rows if r["role"] in allowed]
    if not rows:
        return _empty_history()

    # Newest-first walk for budgeting; reverse for chronological return.
    kept_rev: list[dict] = []
    total_tokens = 0
    dropped_count = 0
    for row in reversed(rows):
        cost = estimate_tokens(row["content"])
        if not kept_rev:
            # Always keep at least one message — even if it busts the budget,
            # the runner needs context to send.
            kept_rev.append(row)
            total_tokens += cost
            continue
        if total_tokens + cost > max_tokens:
            dropped_count += 1
            continue
        kept_rev.append(row)
        total_tokens += cost

    # Add anything we skipped over before the first kept row to dropped_count
    # (defensive: the loop above only counts rows skipped after the first
    # kept one, but with the "always keep newest" rule the first kept row
    # IS the newest, so no rows are skipped before it).
    truncated = dropped_count > 0 or (
        len(kept_rev) == 1 and total_tokens > max_tokens
    )

    messages = [
        {
            "id":      r["id"],
            "role":    r["role"],
            "content": r["content"],
            "ts":      r["ts"],
        }
        for r in reversed(kept_rev)
    ]
    return {
        "messages":      messages,
        "total_tokens":  total_tokens,
        "truncated":     truncated,
        "dropped_count": dropped_count,
    }


def _empty_history() -> dict:
    return {
        "messages":      [],
        "total_tokens":  0,
        "truncated":     False,
        "dropped_count": 0,
    }
