"""Shaping oversized text for a prompt or a log line.

musubi-tier: substrate
expires-when: never - any host with a context window has to cut text it
  cannot afford to send, and the cut has to be visible to whoever reads it.

Three call sites cut text three different ways before this module existed —
`"… [truncated]"`, a bare `"…"`, and `"…[N chars elided on replay]"` — so a
reader could not tell that all three meant the same thing, and a model
reading its own replayed context could not tell a stylistic ellipsis from a
harness cut. One marker prefix, `TRUNCATION_MARK`, now covers all of them.

NOT to be confused with `context.py`'s `[musubi:elided-tool-arg …]`, which is
a semantic placeholder the dispatcher REJECTS if a worker tries to write it
into a file. That one is a guard, not a display cut.
"""

from __future__ import annotations

#: Prefix every harness-side cut carries, so one grep finds them all.
TRUNCATION_MARK = "… [truncated"


def bounded(value: str, limit: int, *, collapse: bool = True) -> str:
    """`value` cut to `limit` characters, with the cut declared in the text.

    `collapse` folds runs of whitespace first — right for prose destined for
    a one-line field, wrong for content whose layout matters.
    """
    text = " ".join((value or "").split()) if collapse else (value or "")
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    suffix = f"{TRUNCATION_MARK} {dropped} chars]"
    keep = max(limit - len(suffix), 0)
    return text[:keep].rstrip() + suffix
