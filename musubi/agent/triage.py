"""The root states what kind of turn this is, and the harness records it.

musubi-tier: substrate
expires-when: never - a decision nobody wrote down cannot be reviewed. This
  records what the ROOT chose and why, which stays useful however the routing
  hint above it changes or disappears.

Why a declaration at all
------------------------
The lexical layer decided the route from the sentence and handed the model a
verdict. When that verdict was wrong there was nothing to look at afterwards:
the log recorded what the REGEX chose, never what the model did with it, so
"the harness mis-routed" and "the model ignored a correct hint" produced
identical evidence. Every post-mortem started by guessing which had happened.

As the hint softens into something overridable (`agent/scope.py`
`prompt_block`), that gap widens — an overridable hint with no record of the
override is just a hint nobody can audit. So the root is asked to name its
choice in one line:

    [triage] work: dashboard.html exists and the change is one file

Reading that line back is PARSING, not judging. The harness does not check
whether the shape is *correct* — it cannot, and pretending otherwise would
rebuild the guess this track removes. It records what was claimed, next to
what the turn actually did, so the two can be compared by a human later.

An absent declaration is recorded as absent. It is never inferred from the
turn's behaviour: a triage the harness made up would be indistinguishable in
the DB from one the model stated, which would poison the only record that
makes an override reviewable.
"""

from __future__ import annotations

import re

#: Turn shapes the root may declare. Deliberately coarse — four buckets a human
#: can scan in a list of a hundred turns. Finer distinctions belong to the
#: model's own reasoning, not to a vocabulary the harness polices.
TRIAGE_SHAPES: frozenset[str] = frozenset({
    "conversation",  # no work: greeting, thanks, meta-question about the chat
    "question",      # answerable from reasoning; no workspace fact needed
    "inspect",       # needs to READ the workspace, changes nothing
    "work",          # something will be written
})

#: `[triage] <shape>: <reason>`. The reason is required — a bare shape records
#: the verdict but not the thinking, and the thinking is the reviewable part.
_TRIAGE_RE = re.compile(
    r"\[triage\]\s*(?P<shape>[a-z_]{1,24})\s*:\s*(?P<reason>[^\n]{1,300})",
    re.IGNORECASE,
)

#: Cap on the stored reason. Long enough for a real clause, short enough that a
#: model dumping a paragraph cannot bloat every turn row.
MAX_TRIAGE_REASON = 200


def prompt_block() -> str:
    """What the root is asked to state. Appended after the evidence block.

    Kept deliberately short. `test_simple_root_two_call_projection_stays_below_
    3k_tokens` caps the simple-root projection, and the first draft of this
    block overran it by ~50 tokens — a ratchet doing its job. Every clause here
    earns its place: the form (so it parses), the vocabulary (so the column can
    be counted), and one line saying nothing waits on it (so the model does not
    treat it as a permission request and stall).
    """
    return (
        "[agent-triage]\n"
        "Open your first reply with one line:\n"
        "  [triage] <shape>: <why, one clause>\n"
        "shape: conversation | question | inspect | work\n"
        "Nothing waits on this line; it records what you chose so an "
        "overridden hint can be reviewed later.\n"
        "[/agent-triage]\n"
    )


def parse_triage(text: str) -> tuple[str, str] | None:
    """`(shape, reason)` the root declared, or None if it declared nothing.

    Takes the FIRST well-formed declaration. A root that restates its triage
    mid-turn has changed its mind, and the first call is the one the turn was
    actually planned around; a later one would silently rewrite history.

    An unrecognised shape is dropped rather than stored, so the column holds
    only the closed vocabulary and can be counted without normalising.
    """
    for match in _TRIAGE_RE.finditer(str(text or "")):
        shape = match.group("shape").strip().lower()
        if shape not in TRIAGE_SHAPES:
            continue
        reason = " ".join(match.group("reason").split())[:MAX_TRIAGE_REASON]
        return (shape, reason.strip(" .·-"))
    return None


def encode_triage(declared: tuple[str, str] | None) -> str | None:
    """One column value: `shape: reason`, or None when nothing was declared."""
    if declared is None:
        return None
    shape, reason = declared
    return f"{shape}: {reason}" if reason else shape
