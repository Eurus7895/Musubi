#!/usr/bin/env python3
"""Refuse to push commits that carry the wrong identity or tool attribution.

musubi-tier: substrate
expires-when: never — the identity rules in CLAUDE.md outlive any model.

The rules being enforced are the NEVER list in CLAUDE.md § Branches & Commits.
They are mechanical, so a hook enforces them — "never send an LLM to do a
linter's job".

Why a hook and not care: the harness presets GIT_AUTHOR_* but leaves
GIT_COMMITTER_* empty, so any command that writes a commit WITHOUT the
`-c user.name=… -c user.email=…` flags silently takes the committer from
~/.gitconfig. `git commit` is easy to remember to flag; `git rebase`,
`git cherry-pick`, `git merge --squash` and `git commit --amend` are not,
and a rebase rewrites every commit in the branch at once.

Usage:
    python scripts/commit_guard.py --install   # point core.hooksPath here
    python scripts/commit_guard.py <remote> <url>   # as a pre-push hook

Exit codes:
    0 — every pushed commit is clean
    1 — at least one violation; the push is refused
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = "scripts/git-hooks"

# The only identity allowed to author OR commit in this repository.
EXPECTED_IDENTITY = "Eurus <t.hoang7895@gmail.com>"

# A 40-zero sha is git's "this ref does not exist" sentinel: on the local
# side it means a branch deletion, on the remote side a brand-new branch.
_ZERO_SHA = "0" * 40

# Named so the failure message can say WHICH rule tripped rather than dumping
# a regex at the user.
_ATTRIBUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Co-Authored-By trailer", re.compile(r"(?im)^\s*co-authored-by\s*:")),
    (
        "tool session trailer",
        re.compile(r"(?im)^\s*[a-z0-9_-]*(?:claude|codex)[a-z0-9_-]*-session\s*:"),
    ),
    (
        "generated-with footer",
        re.compile(
            r"(?i)generated\s+(?:with|by)\b[^\n]{0,60}?"
            r"\b(?:claude|codex|copilot|cursor|chatgpt|gpt|ai)\b",
        ),
    ),
    ("tool link", re.compile(r"(?i)\b(?:claude\.ai|anthropic\.com)\b")),
)

# Branch names describe the product change, not the tool that typed it.
# `\b` treats `/` and `-` as boundaries, so this catches `claude/scratch-1`
# and `feat/codex-port` while leaving `fix/agent-advisory-scope-routing` alone.
_TOOL_IN_NAME_RE = re.compile(r"(?i)\b(claude|codex)\b")

# Record/field separators that cannot occur in a commit message.
_REC = "\x1e"
_FIELD = "\x1f"
_LOG_FORMAT = f"%H{_FIELD}%an <%ae>{_FIELD}%cn <%ce>{_FIELD}%B{_REC}"


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git in the repository being pushed.

    cwd defaults to the process's own directory — git runs hooks from the
    toplevel of the repo whose push triggered them, which is NOT necessarily
    the repo this file lives in (core.hooksPath may point anywhere)."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def check_commit(sha: str, author: str, committer: str, message: str) -> list[str]:
    """Every rule that `sha` breaks, as human-readable sentences.

    Pure: takes the commit's fields, touches no git state."""
    short = sha[:9]
    problems: list[str] = []
    if author != EXPECTED_IDENTITY:
        problems.append(f"{short} author is {author!r}, expected {EXPECTED_IDENTITY!r}")
    if committer != EXPECTED_IDENTITY:
        problems.append(
            f"{short} committer is {committer!r}, expected {EXPECTED_IDENTITY!r}",
        )
    for label, pattern in _ATTRIBUTION_PATTERNS:
        if pattern.search(message):
            problems.append(f"{short} commit message contains a {label}")
    return problems


def check_branch(ref: str) -> list[str]:
    """Rules about the branch being pushed to, given its full remote ref."""
    name = ref.split("refs/heads/", 1)[-1]
    if _TOOL_IN_NAME_RE.search(name):
        return [
            f"branch {name!r} names a tool; branch names describe the product "
            f"change (see CLAUDE.md § Branches & Commits)",
        ]
    return []


def parse_commits(log_output: str) -> list[tuple[str, str, str, str]]:
    """Split `git log --format=_LOG_FORMAT` output into commit tuples."""
    commits = []
    for record in log_output.split(_REC):
        record = record.strip("\n")
        if not record:
            continue
        sha, author, committer, message = record.split(_FIELD, 3)
        commits.append((sha, author, committer, message))
    return commits


def commits_to_check(remote: str, local_sha: str, remote_sha: str) -> list[tuple[str, str, str, str]]:
    """The commits this push would publish.

    A new branch has no remote sha to diff against, so fall back to "commits
    not reachable from anything this remote already has" — otherwise the first
    push of a branch would check its entire history back to the root."""
    if local_sha == _ZERO_SHA:
        return []  # deleting a branch publishes nothing
    if remote_sha == _ZERO_SHA:
        args = ["log", f"--format={_LOG_FORMAT}", local_sha, "--not", f"--remotes={remote}"]
    else:
        args = ["log", f"--format={_LOG_FORMAT}", f"{remote_sha}..{local_sha}"]
    return parse_commits(_git(*args))


def install() -> int:
    hooks_dir = REPO_ROOT / HOOKS_DIR
    hook = hooks_dir / "pre-push"
    if not hook.exists():
        print(f"[commit-guard] missing {hook}", file=sys.stderr)
        return 1
    hook.chmod(0o755)
    _git("config", "core.hooksPath", HOOKS_DIR, cwd=REPO_ROOT)
    print(f"[commit-guard] core.hooksPath -> {HOOKS_DIR}")
    return 0


def main(argv: list[str]) -> int:
    if "--install" in argv:
        return install()

    remote = argv[1] if len(argv) > 1 else "origin"
    problems: list[str] = []
    for line in sys.stdin.read().splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        _local_ref, local_sha, remote_ref, remote_sha = fields
        if local_sha == _ZERO_SHA:
            continue
        problems += check_branch(remote_ref)
        for sha, author, committer, message in commits_to_check(remote, local_sha, remote_sha):
            problems += check_commit(sha, author, committer, message)

    if not problems:
        return 0

    print("[commit-guard] push refused:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nFix the commits, do not bypass. To correct author/committer on the\n"
        "whole branch without touching git config:\n"
        "  GIT_COMMITTER_NAME='Eurus' GIT_COMMITTER_EMAIL='t.hoang7895@gmail.com' \\\n"
        "  git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' \\\n"
        "      rebase -f --onto origin/dev origin/dev <branch>",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
