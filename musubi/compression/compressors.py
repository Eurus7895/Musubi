"""Deterministic, pure-Python compressors. Zero LLM calls (HI #1).

musubi-tier: substrate
expires-when: never — token cost is permanent.

Each compressor maps text -> shorter text while preserving meaning. They
are allowed to be *lossy* (drop comments, whitespace, JSON indentation)
because the verbatim original is always kept in `store` and reachable via
`musubi_retrieve`. They must never raise on valid input; the router
treats any exception as "skip compression".
"""

from __future__ import annotations

import json
import re

# A line that, after leading whitespace, begins a single-line comment.
# Lossy (a `#`/`//` line inside a multi-line string would be dropped from
# the model's reading copy) but the original is recoverable via the store.
_LINE_COMMENT = re.compile(r"^[ \t]*(#|//)")
_MULTI_BLANK = re.compile(r"\n{3,}")


def minify_json(text: str) -> str:
    """Re-emit JSON with no indentation or inter-token spaces (lossless)."""
    obj = json.loads(text)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def collapse_text(text: str) -> str:
    """Strip trailing whitespace per line; collapse 3+ blank lines to one."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    return _MULTI_BLANK.sub("\n\n", "\n".join(lines))


def strip_code(text: str) -> str:
    """Drop full-line comments + blank runs; keep code structure.

    Whole-line `#` / `//` comments and runs of blank lines are removed;
    indentation and code lines are preserved verbatim. Lossy on comments
    only — the original is retrievable.
    """
    kept: list[str] = []
    prev_blank = False
    for raw in text.split("\n"):
        line = raw.rstrip()
        if _LINE_COMMENT.match(line):
            continue
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        kept.append(line)
    # Drop a leading/trailing blank introduced by the pass.
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)
