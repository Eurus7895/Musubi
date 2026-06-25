"""Driver-side context controls — system prompt, verbosity steering,
deterministic context fitting, and effort routing.

musubi-tier: substrate
expires-when: never — the token economics of the LM-call boundary are
  permanent. Every transform here is deterministic and zero-LLM (HI #1);
  the *learned* equivalents in Headroom (Kompress-base, learned-importance
  IntelligentContext) are intentionally not adopted so the substrate keeps
  making zero model calls. These are the Musubi counterparts of Headroom's
  verbosity steering, effort routing, and IntelligentContext.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── System prompt + verbosity steering ───────────────────────────────────────

_BASE_SYSTEM = (
    "You are Musubi's standalone agent. You drive MCP tools to complete the "
    "user's software-engineering task and then report the outcome."
)

#: Appended to the system prompt to cut output tokens (Headroom's
#: "verbosity steering"). Deterministic — it is just text in the prompt.
_VERBOSITY_NOTE = (
    "Be concise. Do not restate the task, the tool catalog, or context the "
    "user already has, and do not narrate what you are about to do. Prefer "
    "acting over explaining: call tools directly. When finished, give a short, "
    "direct answer covering only what changed or what was found — no preamble, "
    "no filler, no summary of your own process unless asked."
)


def build_system_prompt(extra: str | None = None) -> str:
    """The top-level agent's system prompt, including verbosity steering.

    `extra` lets a caller append task-specific guidance; it is placed after
    the steering note so the steering always survives.
    """
    parts = [_BASE_SYSTEM, _VERBOSITY_NOTE]
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)


# ── Effort routing ───────────────────────────────────────────────────────────

#: Output-token cap for a routine cycle. Most cycles only emit a small
#: tool_use block; capping low saves nothing the model needed but bounds a
#: runaway turn. The loop escalates to the ceiling when a call actually stops
#: on `max_tokens`, so the cap never truncates a real answer.
DEFAULT_EFFORT_FLOOR = 2048


def effort_floor() -> int:
    """Starting output-token cap per cycle. `MUSUBI_EFFORT_TOKENS` overrides."""
    raw = os.environ.get("MUSUBI_EFFORT_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_EFFORT_FLOOR


# ── IntelligentContext (deterministic) ───────────────────────────────────────

#: Conversation-size budget in characters before trimming kicks in. Tracks
#: CLAUDE.md's per-call sizing rule (warn ~50k chars) and leaves headroom for
#: the tool catalog, which is sent separately. `MUSUBI_CONTEXT_BUDGET` (chars)
#: overrides; 0 disables trimming entirely.
DEFAULT_CONTEXT_BUDGET = 40_000


def context_budget() -> int:
    raw = os.environ.get("MUSUBI_CONTEXT_BUDGET", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_CONTEXT_BUDGET


def _content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(json.dumps(b, default=str)) for b in content)
    return len(str(content))


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_content_chars(m.get("content")) for m in messages)


def _retrieve_hint(text: str) -> str:
    """Preserve a `musubi_retrieve("ref")` marker so an elided tool result is
    still recoverable by the model from the reversible store."""
    marker = 'musubi_retrieve("'
    i = text.find(marker)
    if i == -1:
        return ""
    j = text.find('")', i)
    if j == -1:
        return ""
    return " — recover with " + text[i:j + 2]


def fit_context(
    messages: list[dict[str, Any]],
    *,
    budget_chars: int | None = None,
    keep_last_turns: int = 4,
) -> list[dict[str, Any]]:
    """Trim an over-budget conversation deterministically, biggest-and-oldest
    first, without breaking tool_use/tool_result pairing.

    Importance heuristic (no learned weights): the leading system message and
    the first user message (the task) are always kept; so are the last
    `keep_last_turns` messages (recency). Among the remaining middle messages,
    the *content* of the largest `tool_result` blocks is replaced with a short
    stub — blocks are never removed (that would orphan a `tool_use`), only
    their bulky content is dropped — until the conversation fits the budget.
    A `musubi_retrieve(...)` marker in an elided result is preserved so the
    model can still pull the original back.

    Returns the same list object when already under budget; otherwise a new
    list with copies of only the messages it changed.
    """
    budget = context_budget() if budget_chars is None else budget_chars
    if budget <= 0:
        return messages
    total = _total_chars(messages)
    if total <= budget:
        return messages

    n = len(messages)
    protected = set()
    if messages and messages[0].get("role") == "system":
        protected.add(0)
        if n > 1:
            protected.add(1)  # the task (first user message)
    elif messages:
        protected.add(0)  # no system prompt → index 0 is the task
    for i in range(max(0, n - keep_last_turns), n):
        protected.add(i)

    # Eligible tool_result blocks, largest first.
    candidates: list[tuple[int, int, int]] = []  # (size, msg_idx, block_idx)
    for mi, m in enumerate(messages):
        if mi in protected:
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if block.get("type") == "tool_result":
                size = len(json.dumps(block.get("content"), default=str))
                if size > 200:  # don't bother eliding already-small results
                    candidates.append((size, mi, bi))
    candidates.sort(reverse=True)

    out = list(messages)
    changed: dict[int, dict[str, Any]] = {}
    for size, mi, bi in candidates:
        if total <= budget:
            break
        msg = changed.get(mi)
        if msg is None:
            msg = dict(messages[mi])
            msg["content"] = [dict(b) for b in messages[mi]["content"]]
            changed[mi] = msg
            out[mi] = msg
        block = msg["content"][bi]
        original = block.get("content")
        original_text = original if isinstance(original, str) else json.dumps(
            original, default=str
        )
        stub = (
            f"[context-trimmed: {len(original_text)} chars elided to save "
            f"tokens{_retrieve_hint(original_text)}]"
        )
        block["content"] = stub
        total -= max(0, len(original_text) - len(stub))

    return out
