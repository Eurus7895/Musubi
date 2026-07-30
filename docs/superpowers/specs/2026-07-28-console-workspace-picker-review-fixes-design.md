# Console workspace picker review fixes

## Context

The workspace picker branch establishes `MUSUBI_WORKSPACE` as the application
boundary for the Console and standalone agent. Review found four gaps:

1. An unreadable or malformed existing Console preference file is treated as
   though no preference exists, so execution can fall back to the runtime
   checkout instead of failing closed.
2. `MUSUBI_WORKSPACE` supplied to the standalone CLI does not take the same
   validation and working-directory path as `--workspace`.
3. A Windows unit test compares a canonical `\\?\` path with its
   uncanonicalized input and fails deterministically.
4. The implementation plan has an extra blank line at end of file.

## Design

### Fail-closed Console preferences

`load_console_preferences` will return `Result<Option<ConsolePreferences>,
String>`. A missing file is the only `Ok(None)` case. Read and JSON parse
failures from an existing file become `Err` values that `open_configured_db`
stores in `workspace_error`, leaving agent launch blocked while Settings
remains available to replace the preference.

Preference writes will use a sibling temporary file followed by `rename`.
Serialization and temporary-write failures leave the last valid preference
untouched. The temporary file is removed after a failed replacement when
possible.

### One standalone workspace path

The standalone CLI will resolve one effective workspace: explicit
`--workspace` first, otherwise a non-empty `MUSUBI_WORKSPACE`. Either source
will pass through `_apply_workspace`, which canonicalizes and validates the
directory, updates the environment, and changes the process working directory.
An empty environment variable retains the existing current-directory fallback.

This keeps filesystem tools, command execution, mechanical linting, artifact
verification, and database paths on one boundary.

### Windows canonical paths

The Windows test will compare `canonical_workspace` with the canonicalized
fixture directory. Production will retain the canonical path because it is the
security boundary; the test must not assume that Windows canonicalization
preserves display syntax.

### Hygiene

The extra blank line at the end of the existing implementation plan will be
removed.

## Error handling

- Missing preference file: continue with existing discovery behavior.
- Existing unreadable or malformed preference file: open the Console with
  in-memory state, display the preference error, and refuse agent launch.
- Invalid CLI workspace from either source: print the existing
  `agent-agent: --workspace ...` diagnostic and exit with status 2.
- Failed atomic preference replacement: return the write error without
  restarting the Console or losing the prior preference.

## Verification

- Add Rust regression tests for missing, malformed, and valid preferences, plus
  atomic replacement behavior.
- Add Python regression tests proving environment-only workspace selection
  validates and changes the working directory, while CLI selection wins.
- Run the Console Rust suite, `musubi-data` Rust suite, targeted Python tests,
  all frontend tests, and `git diff --check`.
