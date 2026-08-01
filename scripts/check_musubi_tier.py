#!/usr/bin/env python3
"""Fail CI when new/modified substrate files lack a `musubi-tier:` tag.

musubi-tier: substrate
expires-when: never — this is the self-enforcement mechanism for HI #9.

Usage:
    python scripts/check_musubi_tier.py            # check ALL files in scope
    python scripts/check_musubi_tier.py --diff     # check only files modified vs origin/dev

Scope (files MUST declare `musubi-tier:`):
  - musubi/**/*.py (excluding tests/, __pycache__)
  - .github/agents/**/*.agent.md
  - .github/skills/**/SKILL.md
  - .github/pipelines/*/pipeline.yaml

The tag may appear:
  - In the module docstring (Python / TS)
  - In YAML frontmatter (markdown + yaml files)

Ephemeral files must ALSO declare `expires-when:` and `cost-lever:` per
the discipline. Substrate files only need the tier label.

Exit codes:
    0 — all in-scope files tagged
    1 — one or more files missing tags / required ephemeral fields
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCOPE_PATTERNS = [
    "musubi/**/*.py",
    ".github/agents/**/*.agent.md",
    ".github/skills/**/SKILL.md",
    ".github/pipelines/*/pipeline.yaml",
    # Console (GUI) — the Rust substrate that reads audit.db. The JS frontend
    # is presentation; the Rust core + Tauri shell carry the tier discipline.
    "gui/src-tauri/src/*.rs",
    "gui/src-tauri/musubi-data/src/*.rs",
]

EXCLUDE_DIR_PARTS = {"tests", "__pycache__", "node_modules", "dist", "out"}

TIER_RE = re.compile(r"musubi-tier:\s*(substrate|ephemeral)", re.IGNORECASE)
EXPIRES_RE = re.compile(r"expires-when:\s*([^\r\n]+)", re.IGNORECASE)
COST_LEVER_RE = re.compile(r"cost-lever:\s*\S", re.IGNORECASE)


def in_scope(path: Path) -> bool:
    if any(part in EXCLUDE_DIR_PARTS for part in path.parts):
        return False
    rel = path.relative_to(REPO_ROOT)
    return any(rel.match(pattern) for pattern in SCOPE_PATTERNS)


def check_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: cannot read ({exc})"]
    tier_match = TIER_RE.search(text)
    if not tier_match:
        return [f"{path}: missing `musubi-tier:` declaration"]
    tier = tier_match.group(1).lower()
    failures: list[str] = []
    if tier == "ephemeral":
        if not EXPIRES_RE.search(text):
            failures.append(f"{path}: ephemeral file missing `expires-when:`")
        if not COST_LEVER_RE.search(text):
            failures.append(f"{path}: ephemeral file missing `cost-lever:`")
    else:
        expires = EXPIRES_RE.search(text)
        if not expires or not expires.group(1).strip().lower().startswith("never"):
            failures.append(
                f"{path}: substrate file must declare `expires-when: never`"
            )
    return failures


def collect_files(diff_only: bool) -> list[Path]:
    if diff_only:
        try:
            output = subprocess.check_output(
                ["git", "diff", "--name-only", "origin/dev...HEAD"],
                cwd=REPO_ROOT,
                text=True,
            )
        except subprocess.CalledProcessError:
            print("warning: could not compute diff; falling back to full scan",
                  file=sys.stderr)
            return collect_files(diff_only=False)
        candidates = [
            REPO_ROOT / line.strip()
            for line in output.splitlines() if line.strip()
        ]
    else:
        candidates = []
        for pattern in SCOPE_PATTERNS:
            candidates.extend(REPO_ROOT.glob(pattern))
    return [p for p in candidates if p.is_file() and in_scope(p)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diff", action="store_true",
                    help="Check only files modified vs origin/dev")
    args = ap.parse_args()

    files = collect_files(diff_only=args.diff)
    if not files:
        print("[check-musubi-tier] no in-scope files to check")
        return 0

    all_failures: list[str] = []
    for path in sorted(files):
        all_failures.extend(check_file(path))

    if all_failures:
        for msg in all_failures:
            print(msg, file=sys.stderr)
        print(f"\n[check-musubi-tier] {len(all_failures)} violation(s) "
              f"across {len(files)} file(s)", file=sys.stderr)
        return 1

    print(f"[check-musubi-tier] OK — {len(files)} files all carry tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
