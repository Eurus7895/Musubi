"""What a tool call would actually destroy, measured before it runs.

musubi-tier: substrate
expires-when: never - counting the files a call would remove or overwrite is a
  measurement, and a human gate on an irreversible action belongs at the
  boundary where that count is knowable.

Why this exists, and what it replaces
-------------------------------------
The previous guard read the USER'S SENTENCE and refused the turn when it saw
"delete" near "files". It caught the honest request — "delete all
*-dashboard.html files" — and missed `run rm -rf build`, which routed to a
coder holding `musubi_run_command`, a tool whose own contract states it does
no dangerous-command detection. So the one path that could actually destroy a
workspace was the one path nobody watched.

This module asks a different question. Not *"does this sentence sound
destructive?"* — an opinion nothing can check — but *"how many files does this
specific call remove, and which ones?"* — a number, obtained by resolving the
paths against the workspace root and counting what is there.

Thresholds
----------
Deleting is irreversible, so ONE file is enough to stop for
(`DELETE_CONFIRM_THRESHOLD`). Overwriting is recoverable from version control
and routine during normal work, so it stops at `OVERWRITE_CONFIRM_THRESHOLD`
files ACROSS THE RUN — one worker rewriting five files has stopped doing what
its plan said and started doing something else.

`musubi_edit_file` and `musubi_append_file` are not counted: a targeted
replacement or an append changes a file, it does not replace or remove it.
Only `musubi_write_file` onto an EXISTING path counts as an overwrite.

Shell commands
--------------
`musubi_run_command` takes arbitrary shell, and no static analysis can say
what an arbitrary pipeline deletes. So it is read in three bands:

1. no delete verb  → not destructive, nothing to measure, pass through;
2. a delete verb with resolvable targets → expand and COUNT them;
3. a delete verb the parser cannot follow (pipes, subshells, `xargs`) →
   `unanalyzable`, which is treated as over the threshold. Fail closed: an
   unreadable `rm` is exactly the case where guessing low is worst.

Note what band 1 means: this module never blocks a command it does not
understand *unless* that command is trying to delete. Everything else keeps
`musubi_run_command`'s stated position that the user is in control.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

#: Deleting is irreversible: the first file is enough to stop for.
DELETE_CONFIRM_THRESHOLD = 1
#: Overwriting is recoverable and routine; five across one run is not.
OVERWRITE_CONFIRM_THRESHOLD = 5

#: Tools that replace a whole file's content when the path already exists.
_OVERWRITE_TOOLS = frozenset({"musubi_write_file"})
#: Shell commands whose job is to remove files.
_DELETE_COMMANDS = frozenset({
    "rm", "rmdir", "unlink", "del", "erase", "rd",
    "remove-item", "ri", "shred",
})
#: `git clean` removes untracked files; `git` alone does not.
_DELETE_SUBCOMMANDS = {("git", "clean")}
#: Shell constructs the target parser cannot follow. Their presence alongside a
#: delete verb makes the command unanalyzable rather than safe.
_OPAQUE_SHELL_RE = re.compile(r"[|;&`]|\$\(|\bxargs\b|\bfind\b|\beval\b")
#: How many affected paths to name in the message before summarising the rest.
MAX_NAMED_PATHS = 10


@dataclass(frozen=True)
class BlastRadius:
    """What one tool call would remove or replace, as counted on disk."""

    deletes: tuple[str, ...] = ()
    overwrites: tuple[str, ...] = ()
    #: A delete was requested but its targets could not be resolved. Treated as
    #: over every threshold — an `rm` nobody can read is the worst case to
    #: assume small.
    unanalyzable: bool = False
    #: The command or path the measurement was taken from, for the message.
    subject: str = ""

    @property
    def delete_count(self) -> int:
        return len(self.deletes)

    @property
    def overwrite_count(self) -> int:
        return len(self.overwrites)

    @property
    def is_empty(self) -> bool:
        return not (self.deletes or self.overwrites or self.unanalyzable)

    @property
    def keys(self) -> tuple[str, ...]:
        """Stable identifiers for what this call would destroy.

        A resolved call is identified by its PATHS, so approving three files
        approves those three and nothing else. An unanalyzable call has no
        paths to name, so it is identified by the command text itself — which
        means approval there is narrower, not broader: the exact same command,
        or nothing.
        """
        if self.unanalyzable:
            return (f"cmd:{self.subject}",)
        return tuple(sorted({*self.deletes, *self.overwrites}))


@dataclass
class RunningTotals:
    """Overwrites accumulated across a run, so the ceiling is per-run.

    A worker rewriting one file per cycle never trips a per-call check, which
    is precisely the drift the ceiling exists to catch.
    """

    overwritten: set[str] = field(default_factory=set)

    def add(self, radius: BlastRadius) -> None:
        self.overwritten.update(radius.overwrites)

    @property
    def overwrite_count(self) -> int:
        return len(self.overwritten)


def _workspace_root() -> Path:
    """Same root the filesystem tools resolve against."""
    from tools import fs

    return fs._workspace_root()


def _resolve(candidate: str, root: Path) -> Path | None:
    """`candidate` under the workspace root, or None if it escapes or is junk."""
    try:
        path = Path(candidate)
        resolved = (path if path.is_absolute() else root / path).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return None
    return resolved


def _existing_files_under(path: Path) -> list[str]:
    """Every existing file at `path`, or under it when it is a directory."""
    try:
        if path.is_file():
            return [str(path)]
        if path.is_dir():
            return [str(p) for p in path.rglob("*") if p.is_file()]
    except OSError:
        return []
    return []


def _expand(target: str, root: Path) -> list[str]:
    """Existing files a shell target names, glob included."""
    if any(ch in target for ch in "*?["):
        try:
            return sorted(
                str(p) for p in root.glob(target.lstrip("./")) if p.is_file()
            )
        except (ValueError, OSError):
            return []
    resolved = _resolve(target, root)
    return sorted(_existing_files_under(resolved)) if resolved else []


#: Tokens after which a NEW command begins. A delete verb only counts when it
#: stands in command position: `grep -r rm .` searches for the letters "rm" and
#: must pass straight through, while `… | xargs rm` really does delete.
_COMMAND_SEPARATORS = frozenset({"|", "||", "&&", ";", "&", "xargs"})


def _normalize(token: str) -> str:
    return token.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _command_heads(tokens: list[str]) -> list[int]:
    """Indices where a command begins: position 0 and after each separator."""
    heads = [0]
    for index, token in enumerate(tokens):
        if _normalize(token) in _COMMAND_SEPARATORS and index + 1 < len(tokens):
            heads.append(index + 1)
    return heads


def _delete_targets(command: str) -> list[str] | None:
    """Non-flag arguments of a delete command, or None when it deletes nothing.

    Returns an EMPTY list when a delete verb is present but its targets cannot
    be attributed — because the delete is not the leading command
    (`find … | xargs rm`) or the arguments are unreadable. The caller reads
    that as unanalyzable, not as harmless.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return None

    def _is_delete(index: int) -> bool:
        head = _normalize(tokens[index])
        if head in _DELETE_COMMANDS:
            return True
        following = _normalize(tokens[index + 1]) if index + 1 < len(tokens) else ""
        return (head, following) in _DELETE_SUBCOMMANDS

    positions = _command_heads(tokens)
    if not any(_is_delete(index) for index in positions):
        return None  # band 1: nothing here removes a file
    if not _is_delete(0):
        return []  # the delete is downstream of a pipe — targets unattributable

    rest = tokens[1:]
    if rest and (_normalize(tokens[0]), _normalize(rest[0])) in _DELETE_SUBCOMMANDS:
        rest = rest[1:]
    return [token for token in rest if not token.startswith("-")]


def measure(tool_name: str, args: dict[str, object]) -> BlastRadius:
    """Count what this call would remove or replace. Never raises."""
    root = _workspace_root()

    if tool_name in _OVERWRITE_TOOLS:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return BlastRadius()
        resolved = _resolve(path, root)
        if resolved is None or not resolved.is_file():
            return BlastRadius()  # creating a new file destroys nothing
        return BlastRadius(overwrites=(str(resolved),), subject=path)

    if tool_name != "musubi_run_command":
        return BlastRadius()

    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return BlastRadius()
    targets = _delete_targets(command)
    if targets is None:
        return BlastRadius()  # band 1: not a delete, not this module's business
    if _OPAQUE_SHELL_RE.search(command) or not targets:
        return BlastRadius(unanalyzable=True, subject=command)  # band 3
    found: list[str] = []
    for target in targets:
        found.extend(_expand(target, root))
    return BlastRadius(deletes=tuple(sorted(set(found))), subject=command)


def exceeds_threshold(radius: BlastRadius, totals: RunningTotals) -> bool:
    """True when this call needs the user's word before it runs."""
    if radius.unanalyzable:
        return True
    if radius.delete_count >= DELETE_CONFIRM_THRESHOLD:
        return True
    return (
        totals.overwrite_count + radius.overwrite_count
        >= OVERWRITE_CONFIRM_THRESHOLD
    )


def describe(radius: BlastRadius, totals: RunningTotals) -> str:
    """The message the user reads. Names files; never says only "destructive"."""
    if radius.unanalyzable:
        return (
            f"This command deletes files, but its targets cannot be resolved "
            f"statically, so the harness cannot say how many: "
            f"`{radius.subject}`. Confirm with the user, naming the command "
            f"verbatim, before running it."
        )
    if radius.delete_count:
        named = ", ".join(radius.deletes[:MAX_NAMED_PATHS])
        more = radius.delete_count - MAX_NAMED_PATHS
        tail = f" and {more} more" if more > 0 else ""
        return (
            f"This would DELETE {radius.delete_count} file(s): {named}{tail}. "
            f"Deletion cannot be undone from here — report exactly this to the "
            f"user and get an explicit go-ahead before retrying."
        )
    total = totals.overwrite_count + radius.overwrite_count
    return (
        f"This is overwrite number {total} in this run "
        f"(ceiling {OVERWRITE_CONFIRM_THRESHOLD}), on {radius.subject!r}. A run "
        f"rewriting this many files has outgrown what it was asked to do. "
        f"Report what has been rewritten so far and confirm before continuing."
    )


# ── one-time approval, verifiable without reading prose ─────────────────────
#
# The harness cannot tell "yes, delete them" from "no, don't" — judging a
# sentence is the thing this whole redesign removes. So consent arrives as a
# token the HARNESS mints, shows in its refusal, and later matches literally
# against the USER's message. The comparison is string equality against a value
# the harness itself generated; no interpretation happens anywhere.
#
# What makes it sound is structural, not clever: a model cannot author a user
# turn. `_append_chat_message(chat_id, "user", task)` is fed by the CLI
# argument or the Console input box, never by model output. So a token found in
# a user message is proof a human put it there — the same class of guarantee as
# "a worker cannot set its own process env", without the env.

#: Prefix that makes an approval token unmistakable in a chat transcript.
GRANT_PREFIX = "allow-"
#: Hex digits of the digest. Six is ~16.7M combinations — far beyond guessing
#: for a value that is displayed anyway, and short enough to retype by hand.
GRANT_DIGEST_CHARS = 6


def grant_token(keys: tuple[str, ...]) -> str:
    """A stable one-time token for exactly this set of destructions.

    Derived from the SORTED key set, so the same files always produce the same
    token and one extra file produces a different one. Approval therefore
    cannot silently widen: a call reaching beyond what was shown to the user
    hashes differently and is refused with a fresh token.
    """
    digest = hashlib.sha256("\x00".join(sorted(keys)).encode("utf-8")).hexdigest()
    return f"{GRANT_PREFIX}{digest[:GRANT_DIGEST_CHARS]}"


def covered_by(radius: BlastRadius, approved: frozenset[str]) -> bool:
    """True when everything this call destroys was already approved."""
    keys = radius.keys
    return bool(keys) and all(key in approved for key in keys)


def encode_pending(grants: list[tuple[str, tuple[str, ...]]]) -> str:
    """Serialize `(token, keys)` pairs for the agent_turns row."""
    return json.dumps(
        [{"token": token, "keys": list(keys)} for token, keys in grants],
        separators=(",", ":"),
    )


def approved_keys_from(pending: str | None, user_message: str) -> frozenset[str]:
    """Keys the user approved by echoing a token, or an empty set.

    Reads only two things: the tokens this chat is waiting on, and whether the
    literal token appears in what the user typed. Malformed storage yields an
    empty set — the safe direction, since an unreadable grant means the gate
    stays shut.
    """
    if not pending or not user_message:
        return frozenset()
    try:
        entries = json.loads(pending)
    except (json.JSONDecodeError, TypeError):
        return frozenset()
    if not isinstance(entries, list):
        return frozenset()
    approved: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        token = entry.get("token")
        keys = entry.get("keys")
        if not isinstance(token, str) or not isinstance(keys, list):
            continue
        if token and token in user_message:
            approved.update(key for key in keys if isinstance(key, str))
    return frozenset(approved)
