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

One of these facts is now ENFORCED, not merely reported:
`names_workspace_path` is what `GoalState.evidence_gap` reads to decide whether
a mutation worker may be summoned at all. The rest render into the root's
prompt and print one log line per turn.

The DB-derived facts are passed IN rather than queried here. `run_agent`
already reads them for its own purposes, and a second query path would be a
second thing to keep true; keeping storage out also leaves this module
importable by tests with no database at all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.fs import _workspace_root

#: The alias of the fixed harness root. A path under it is named bare
#: (`agent/run.py`); a path under any other granted root is named with its
#: alias (`bamf-updater/temp.log`) because that alias is the `root=` argument
#: every filesystem tool needs to reach it.
PRIMARY_ALIAS = "musubi"

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
    """Five facts about a turn. No field is an opinion about the request."""

    #: A token in the request resolves to a path INSIDE one of the request's
    #: granted roots — the fixed harness root, or any folder the operator
    #: attached to this session (`workspace/grants.py`). False both when
    #: nothing looks like a path and when what does look like one escapes
    #: every root — those are different situations for the model, so
    #: `escaped_paths` keeps them apart.
    names_workspace_path: bool = False
    #: …and that path is on disk right now.
    path_exists: bool = False
    #: Paths named in the request that resolve outside EVERY granted root. A
    #: turn asking about one of these cannot be served by any worker until the
    #: operator attaches that folder, and the refusal is worth predicting
    #: before a spawn rather than after.
    escaped_paths: tuple[str, ...] = field(default_factory=tuple)
    #: Resolved granted paths, root-relative, for the prompt block. Paths under
    #: the fixed harness root are bare; paths under an attached folder carry
    #: that folder's alias, which is the `root=` argument the tools need.
    named_paths: tuple[str, ...] = field(default_factory=tuple)
    #: The attached-folder aliases (never `musubi`) that contain a named path.
    #: Kept apart from `named_paths` because a prefix cannot be recovered from
    #: the joined string: `agent/run.py` and `bamf-updater/run.py` have the
    #: same shape and only this field says which first segment is an alias.
    named_root_aliases: tuple[str, ...] = field(default_factory=tuple)
    #: This chat has prior messages, so "it" and "that" may have referents.
    has_conversation: bool = False
    #: An explorer (or investigator) has already reported into this turn.
    explorer_findings: bool = False
    #: Trailing count of turns in this chat that wrote no file.
    barren_turns: int = 0

    @property
    def target_is_unknown(self) -> bool:
        """No one has established WHAT this turn is about.

        The enforceable core of "collect enough information first": no named
        path inside the workspace, nothing on disk, and no worker has looked.
        `GoalState.evidence_gap` refuses a mutation spawn while this holds.
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
            f"barren_turns={self.barren_turns}",
        ]
        if self.named_paths:
            lines.append("paths=" + ",".join(self.named_paths))
            # A path under an attached folder is written `<alias>/<rest>`, and
            # `<alias>` is not part of the filename — it is the `root=`
            # argument the tool needs. Say so here rather than leaving the
            # root agent to infer it from the roots listing further down.
            aliased = self.named_root_aliases
            if aliased:
                lines.append(
                    "note=paths above are prefixed with the attached root that "
                    "contains them (" + ", ".join(aliased) + "). Pass that name "
                    "as the tool's `root` argument and the REST of the path as "
                    "`path`; do not pass the prefix as part of `path`."
                )
        if self.escaped_paths:
            # Names the remedy, not just the refusal. Every path listed here
            # is one the operator can hand over with the console's Add folder
            # control; telling the user to go run the command by hand — the
            # old wording's only implied next step — hides the affordance the
            # product already has.
            lines.append(
                "outside_granted_roots=" + ",".join(self.escaped_paths)
                + " (no attached root contains these, so no worker can reach "
                "them; say so and tell the user they can attach that folder "
                "to this session, then stop)"
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
    barren_turns: int = 0,
    root: Path | None = None,
    roots: Sequence[tuple[str, Path]] | None = None,
) -> EvidenceVector:
    """Build the vector. Never raises — an unreadable fact is simply absent.

    `roots` is the request's full grant list — `(alias, path)` for the fixed
    harness root and every folder the operator attached, in registry order
    (`workspace/grants.py::RootRegistry.grants`). Pass it, or this module
    measures containment against the harness root alone and reports every
    attached folder as unreachable — which is the opposite of what the
    operator just granted. `root` remains the single-root shorthand.
    """
    inside, outside, exists, aliases = _classify_paths(request, root, roots)
    return EvidenceVector(
        names_workspace_path=bool(inside),
        path_exists=exists,
        escaped_paths=outside,
        named_paths=inside,
        named_root_aliases=aliases,
        has_conversation=bool(has_conversation),
        explorer_findings=bool(explorer_findings),
        barren_turns=max(0, int(barren_turns or 0)),
    )


def _resolved_bases(
    root: Path | None,
    roots: Sequence[tuple[str, Path]] | None,
) -> tuple[tuple[str, Path], ...]:
    """The roots this turn may reach, primary first. Empty when unreadable."""
    pairs: list[tuple[str, Path]] = []
    try:
        if roots:
            for alias, path in roots:
                pairs.append((str(alias), Path(path).resolve()))
        else:
            pairs.append((PRIMARY_ALIAS, (root or _workspace_root()).resolve()))
    except (OSError, ValueError):
        return ()
    return tuple(pairs)


def _label_for(alias: str, relative: Path) -> str:
    """Name a contained path the way the model must address it.

    Under the fixed harness root that is the bare relative path, unchanged.
    Under an attached folder it is `<alias>/<relative>` — and `<alias>` alone
    when the request named the folder itself — because the alias is exactly
    the `root=` argument `musubi_read_file` and friends require, and a bare
    relative path there would resolve against the wrong root.
    """
    rel = relative.as_posix()
    if alias == PRIMARY_ALIAS:
        return "" if rel in {"", "."} else rel
    return alias if rel in {"", "."} else f"{alias}/{rel}"


def _classify_paths(
    request: str,
    root: Path | None,
    roots: Sequence[tuple[str, Path]] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], bool, tuple[str, ...]]:
    """Split path-shaped tokens into inside-a-root, outside-every-root, existence.

    This is the same containment test the firewall makes
    (`tools/fs.resolve_path`), reused rather than reimplemented so the vector
    cannot disagree with what the tools will actually permit. It is inlined
    instead of called because `resolve_path` raises on escape and this needs
    the escape as DATA.

    Containment is tested against every root the request was granted, not just
    the harness root. Testing only the harness root made an attached folder
    indistinguishable from a path nobody granted, and the prompt then told the
    root agent to refuse the very folder the operator had just handed it.
    Relative tokens still resolve against the primary root: a bare
    `docs/plan.md` means the harness workspace, as it always has.
    """
    bases = _resolved_bases(root, roots)
    if not bases:
        return ((), (), False, ())
    primary = bases[0][1]
    inside: list[str] = []
    outside: list[str] = []
    aliases: list[str] = []
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
                candidate if candidate.is_absolute() else primary / candidate
            ).resolve()
        except (OSError, ValueError):
            continue
        label: str | None = None
        matched_alias = ""
        for alias, base in bases:
            try:
                relative = resolved.relative_to(base)
            except ValueError:
                continue
            label = _label_for(alias, relative)
            matched_alias = alias
            break
        if label is None:
            if token not in outside:
                outside.append(token)
            continue
        if label and label not in inside:
            inside.append(label)
        if (
            label
            and matched_alias != PRIMARY_ALIAS
            and matched_alias not in aliases
        ):
            aliases.append(matched_alias)
        try:
            if resolved.exists():
                exists = True
        except OSError:
            pass
    return (tuple(inside), tuple(outside), exists, tuple(aliases))
