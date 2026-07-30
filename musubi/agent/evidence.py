"""What the record actually establishes about a turn, before anyone judges it.

musubi-tier: substrate
expires-when: never - these are facts about the record, not opinions about
  meaning. A better model does not make "does this path exist?" unnecessary;
  it makes the answer more useful.

The layer this replaces asked questions no code can answer. `_BROAD_PRODUCT_RE`
asked *is this request broad?* — text was the only input and nothing could check
the verdict, so "create a website" and "fix the typo" were sorted by vocabulary.
This module asks a different KIND of question: not what the sentence means, but
what the record contains. Every answer here is checkable against the filesystem
or the audit DB, which is why it can be trusted enough to route on later.

Concretely, the difference in one line:

    judging   "is this a big change?"        → an opinion, unverifiable
    evidence  "does `agent/run.py` exist?"   → a stat() call

Nothing routes on this vector yet. It renders into the root's prompt beside the
existing scope hint and prints one log line per turn, so the distribution of
real conversations can be measured before any behavior depends on it — see
docs/superpowers/plans/2026-07-29-llm-owned-scope-with-evidence-gate.md step 1.

The DB-derived facts are passed IN rather than queried here. `run_agent`
already reads all three for its own purposes, and a second query path would be
a second thing to keep true; keeping storage out also leaves this module
importable by tests with no database at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tools.fs import _workspace_root

#: Tokens that can plausibly BE a path. A bare word ("website", "faster") is
#: not one: without a separator or a suffix there is nothing to resolve, and
#: treating every noun as a candidate path would make `path_exists` a lottery.
#: Backticks and quotes are stripped because a request that names a file
#: usually quotes it.
_PATH_CANDIDATE = re.compile(
    r"[`'\"(\[]?"
    r"([A-Za-z0-9_.\-/\\]*[/\\][A-Za-z0-9_.\-/\\]*"  # has a separator
    r"|[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,8})"           # or a file suffix
    r"[`'\")\],.:;]?"
)

#: A trailing sentence period is not part of a filename; a leading `./` is.
_TRIM = "`'\"()[],:;"

#: A URI is not a filesystem path. `https://example.com/docs/page.html` matched
#: the path pattern from the `//` onward, resolved outside the workspace, and
#: the prompt then told the root that no worker could reach it — which is false
#: when an HTTP or browser MCP server is configured, and that server is exactly
#: the thing the request was about. Schemes are stripped from consideration
#: entirely: this module measures the filesystem, and a URL is not on it.
_URI_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]{1,31}://\S+")

#: Turns in a row that ended without writing a file before the vector says so
#: out loud. Same threshold the driver already warns at, kept in one place.
NO_PROGRESS_TURNS = 3


@dataclass(frozen=True)
class EvidenceVector:
    """Six facts about a turn. No field is an opinion about the request."""

    #: A token in the request resolves to a path INSIDE the workspace root.
    #: False both when nothing looks like a path and when what does look like
    #: one escapes the root — those are different situations for the model, so
    #: `escaped_paths` keeps them apart.
    names_workspace_path: bool = False
    #: …and that path is on disk right now.
    path_exists: bool = False
    #: Paths named in the request that resolve OUTSIDE the workspace root. A
    #: turn asking about one of these cannot be served by any worker, and the
    #: refusal is worth predicting before a spawn rather than after.
    escaped_paths: tuple[str, ...] = field(default_factory=tuple)
    #: Resolved workspace paths, workspace-relative, for the prompt block.
    named_paths: tuple[str, ...] = field(default_factory=tuple)
    #: This chat has prior messages, so "it" and "that" may have referents.
    has_conversation: bool = False
    #: An explorer (or investigator) has already reported into this turn.
    explorer_findings: bool = False
    #: This turn carries the answer to the one deterministic clarification.
    clarification_answered: bool = False
    #: Trailing count of turns in this chat that wrote no file.
    barren_turns: int = 0

    @property
    def target_is_unknown(self) -> bool:
        """No one has established WHAT this turn is about.

        The enforceable core of "collect enough information first": no named
        path inside the workspace, nothing on disk, and no worker has looked.
        Step 2 refuses a coder spawn while this holds; today it is only
        printed.
        """
        return not (
            self.names_workspace_path
            or self.path_exists
            or self.explorer_findings
        )

    def log_line(self) -> str:
        return (
            "[agent] evidence: "
            f"names_path={self.names_workspace_path} "
            f"exists={self.path_exists} "
            f"history={self.has_conversation} "
            f"explorer={self.explorer_findings} "
            f"clarified={self.clarification_answered} "
            f"barren={self.barren_turns} "
            f"target_unknown={self.target_is_unknown}"
        )

    def prompt_block(self) -> str:
        """What the root is told it has — and what it is told it lacks.

        Deliberately states absences as loudly as presences. A model that is
        not told "no path in this request resolves inside the workspace" will
        assume the file it invented is there, which is the exact failure the
        traced session ended in.
        """
        # `explorer_findings` is deliberately absent. The system prompt is built
        # once and never rewritten, while that fact changes the moment a
        # read-only worker reports — so printing it here would freeze
        # `explorer_findings=False` into an immutable block and contradict the
        # worker outcome the root reads later in the same turn. It stays in
        # `log_line`, which is a snapshot of one moment and honest about it.
        lines = [
            "[agent-evidence]",
            f"names_workspace_path={self.names_workspace_path}",
            f"path_exists={self.path_exists}",
            f"has_conversation={self.has_conversation}",
            f"clarification_answered={self.clarification_answered}",
            f"barren_turns={self.barren_turns}",
        ]
        if self.named_paths:
            lines.append("paths=" + ",".join(self.named_paths))
        if self.escaped_paths:
            lines.append(
                "outside_workspace=" + ",".join(self.escaped_paths)
                + " (no worker can reach these; say so and stop)"
            )
        if self.target_is_unknown:
            # Phrased as a starting condition, not a standing fact, because
            # this text outlives the moment it was true: an explorer summoned
            # later in this turn is exactly how it stops being true.
            lines.append(
                "note=as this turn begins, nothing in the record establishes "
                "what it targets. Do not send a coder at a guess — either ask, "
                "or summon an explorer first and route on what it reports."
            )
        if self.barren_turns >= NO_PROGRESS_TURNS:
            lines.append(
                f"note={self.barren_turns} turns in a row produced no file. "
                "Change approach or say plainly what is blocking."
            )
        lines.append("[/agent-evidence]")
        return "\n".join(lines) + "\n"


def collect(
    request: str,
    *,
    has_conversation: bool = False,
    explorer_findings: bool = False,
    clarification_answered: bool = False,
    barren_turns: int = 0,
    root: Path | None = None,
) -> EvidenceVector:
    """Build the vector. Never raises — an unreadable fact is simply absent."""
    inside, outside, exists = _classify_paths(request, root)
    return EvidenceVector(
        names_workspace_path=bool(inside),
        path_exists=exists,
        escaped_paths=outside,
        named_paths=inside,
        has_conversation=bool(has_conversation),
        explorer_findings=bool(explorer_findings),
        clarification_answered=bool(clarification_answered),
        barren_turns=max(0, int(barren_turns or 0)),
    )


def _classify_paths(
    request: str, root: Path | None,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Split path-shaped tokens into inside-root, outside-root, and existence.

    This is the same containment test the firewall makes
    (`tools/fs.resolve_path`), reused rather than reimplemented so the vector
    cannot disagree with what the tools will actually permit. It is inlined
    instead of called because `resolve_path` raises on escape and this needs
    the escape as DATA.
    """
    try:
        base = (root or _workspace_root()).resolve()
    except OSError:
        return ((), (), False)
    inside: list[str] = []
    outside: list[str] = []
    exists = False
    # Blank out URIs before looking for paths, so their host and path segments
    # never reach the containment test. Replacing with spaces keeps every other
    # offset intact.
    text = _URI_RE.sub(lambda m: " " * len(m.group(0)), str(request or ""))
    for match in _PATH_CANDIDATE.finditer(text):
        token = match.group(1).strip(_TRIM)
        if not token or token in {".", ".."}:
            continue
        try:
            candidate = Path(token)
            resolved = (
                candidate if candidate.is_absolute() else base / candidate
            ).resolve()
        except (OSError, ValueError):
            continue
        try:
            relative = resolved.relative_to(base)
        except ValueError:
            if token not in outside:
                outside.append(token)
            continue
        rel = relative.as_posix()
        if rel and rel not in inside:
            inside.append(rel)
        try:
            if resolved.exists():
                exists = True
        except OSError:
            pass
    return (tuple(inside), tuple(outside), exists)
