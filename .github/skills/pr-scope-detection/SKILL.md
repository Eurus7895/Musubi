---
name: pr-scope-detection
description: Classify the files in a PR/branch diff by kind (source, test, config, docs, generated, lockfile) and priority for review. Use when triaging which files in a change deserve careful review effort.
---

## Purpose

A typical PR mixes source code, test code, generated files, lockfiles,
and documentation. Treating all of them equally wastes review effort.
This skill is the deterministic classifier the `/code-review` scoper
agent uses to triage the diff before fan-out.

## Procedure

For each file path in the diff, walk these classifications in order
and take the first match.

### Kind

1. **lockfile** — `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`,
   `poetry.lock`, `Pipfile.lock`, `requirements.txt` lock-only changes,
   `Cargo.lock`, `composer.lock`, `Gemfile.lock`, `go.sum`.
2. **generated** — files inside `dist/`, `build/`, `node_modules/`,
   `.next/`, `out/`, `target/`, `__pycache__/`, `*.pb.go`, `*_pb2.py`,
   `*.min.js`, `*.min.css`, `*.map`, anything with a header line
   matching `# DO NOT EDIT` or `// Code generated`.
3. **test** — paths containing `test/`, `tests/`, `__tests__/`,
   `spec/`, or filenames matching `*_test.*`, `*test_*.py`, `*.test.*`,
   `*.spec.*`, `test_*.py`.
4. **config** — `*.yaml`, `*.yml`, `*.toml`, `*.ini`, `*.cfg`,
   `*.json` (excluding the lockfiles above), `Dockerfile*`,
   `Makefile`, `.eslintrc*`, `.prettierrc*`, `tsconfig.json`,
   `pyproject.toml`, `setup.py`, `setup.cfg`.
5. **docs** — `*.md`, `*.rst`, `*.txt`, anything under `docs/`,
   `README*`, `LICENSE*`, `CONTRIBUTING*`, `CHANGELOG*`.
6. **source** — everything else.

### Priority

Apply these rules in order; first match wins.

1. **skip** if `kind` is `lockfile` or `generated`.
2. **skip** if the diff for this file is whitespace-only (every
   changed line is the same after `strip()`).
3. **high** if any of:
   - Path matches `*/auth/*`, `*/security/*`, `*/crypto/*`,
     `*/permission*`, `*/policy*`, `*/firewall*`.
   - Path is a public API entrypoint: anything matching
     `*api/*`, `*/handlers/*`, `*/routes/*`, `*/endpoints/*`,
     `main.py`, `index.{js,ts}`, `server.{py,js,ts}`,
     `app.{py,js,ts}`.
   - File-level change ≥ 100 lines added or ≥ 50 lines removed.
   - The file is new (entire file is `+` lines).
4. **low** if any of:
   - `kind` is `docs` or `config` AND change is ≤ 20 lines.
   - File-level change is < 5 lines AND no `kind: source`.
5. **medium** otherwise. This is the typical-source-change bucket
   and should be the most common priority.

### Output rows

For each file, emit:

```json
{
  "path": "string",
  "kind": "source | test | config | docs | generated | lockfile",
  "priority": "high | medium | low | skip",
  "size_lines": 0,
  "reason": "short rule citation, e.g. 'auth path + 87 lines added'"
}
```

The reason field exists so the synthesizer and the user can audit
priority assignments. Cite the specific rule that triggered.

## Cross-cutting scope notes

After per-file classification, add scope-level notes for things one
file can't show:

- **Schema migration without backfill.** Look for migration files
  (`*/migrations/*`, `alembic/*`, `prisma/migrations/*`) adding a
  NOT NULL column or dropping a column.
- **New external dependency.** `package.json`, `pyproject.toml`,
  `Cargo.toml`, `go.mod` adding entries the lockfile newly pins.
- **Auth/permission surface change.** Multiple files in the auth
  paths above.
- **Test coverage gap.** Source files marked priority `high` or
  `medium` whose corresponding test path has no change in this diff.

Surface 1-5 notes maximum; the synthesizer weights them.

## Negative space

This skill is NOT for finding bugs. The scoper's only job is triage.
If the procedure tempts you to flag "this code is wrong," that's the
finder or reviewer-aux's job — note the file as `high` priority and
move on.
