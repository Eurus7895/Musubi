---
name: git-workflow
description: Git hygiene for review-ready history — Conventional Commits, outcome-named branches, rebase-before-push, and safe conflict resolution. Use when the user asks to commit, branch, rebase, resolve a merge conflict, or prepare a change for review.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - commit
  - branch
  - rebase
  - merge conflict
  - conventional commits
  - cherry-pick
  - git history
---

## Purpose

Produce a git history a reviewer can read commit-by-commit: each commit
one logical change, named for its outcome, based on the current
integration branch. The project's own contribution rules (CLAUDE.md /
CONTRIBUTING) always override the defaults below — read them first.

## Procedure

### 1. Branch from the integration branch, freshly fetched

```bash
git fetch origin
git switch -c <type>/<area>-<outcome> origin/<integration-branch>
```

- Name = `<type>/<area>-<outcome>`, lowercase kebab-case, where
  `<type>` is a Conventional Commits type (`feat`, `fix`, `docs`,
  `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `style`,
  `revert`). The name states the product outcome, not the tool or
  person doing the work.
- Never develop on the integration branch itself.

### 2. Commit one logical change at a time

- Stage deliberately: `git add -p` over `git add .` — inspect what is
  going in. `git status` + `git diff --staged` before every commit.
- Message follows Conventional Commits 1.0.0:
  `<type>[optional scope]: <description>` — imperative mood, ≤ 72
  chars, lowercase type/scope, no trailing period. Body wraps at 72
  columns and explains *why*, not *what* (the diff shows what).
- Breaking change → `!` after type/scope **and** a `BREAKING CHANGE:`
  footer.
- A commit that needs "and" in its subject is two commits.

### 3. Keep the base current

```bash
git fetch origin && git rebase origin/<integration-branch>
```

- Rebase *your unpublished work* onto the moved base; never merge the
  base into a feature branch (merge bubbles hide the real diff).
- Never rebase or amend commits that are already published on a shared
  branch — fix forward with a new commit.

### 4. Resolve conflicts by intent, not by side

- For each conflicted hunk, read both parents' intent (`git log` the
  file on both sides if unclear). The resolution implements *both*
  intents or consciously drops one — it is never "pick ours/theirs"
  by default.
- After resolving: run the tests **before** `git rebase --continue`.
  A syntactically clean merge can still be semantically broken (both
  sides pass alone, combined they fail).
- Lost? `git rebase --abort` restores the pre-rebase state; no
  half-resolved rebase is ever pushed.

### 5. Pre-push audit

```bash
git log --oneline origin/<integration-branch>..HEAD
git diff origin/<integration-branch>...HEAD --stat
```

- Every listed commit belongs to this change; nothing unrelated rode
  along, no stray artifacts/lockfiles/secrets in the stat.
- Push with an upstream: `git push -u origin <branch>`.
- `--force-with-lease` (never bare `--force`), and only on branches no
  one else builds on.

## Anti-patterns

- "WIP" / "fix" / "more fixes" commit chains on a review branch —
  squash locally into logical commits before pushing.
- Committing generated files, editor state, or vendored deps because
  `git add .` swept them in.
- Amending or force-pushing over a commit a reviewer already commented
  on — their comments now point at nothing.
