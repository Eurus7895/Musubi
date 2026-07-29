# Session Folder Grants Implementation Plan

> Execute inline with strict TDD. Keep `vietnam-weather.html` untouched.

## Context

The current feature branch replaces Musubi's process root with one globally
selected `MUSUBI_WORKSPACE`, persists it in Settings, and restarts Console.
The approved replacement keeps `MUSUBI_ROOT` fixed and grants each
Orchestrator session access to up to 16 additional folders. Every request gets
an immutable root manifest; all filesystem operations, commands, mechanical
gates, artifacts, policy events, and workers resolve through that manifest.

Design:
[`2026-07-29-session-folder-grants-design.md`](../specs/2026-07-29-session-folder-grants-design.md)

## Goal

Allow an idle Orchestrator session to add, rename, order, and remove multiple
existing folders without changing the Musubi root or restarting Console.
Launches snapshot those grants and give the standalone agent root-aware,
fail-closed access for the complete worker tree.

## Tech Stack

- Rust/Tauri 2 Console host
- `musubi-data` Rust crate and SQLite
- React frontend with Node source-contract tests
- Python standalone host, MCP server, filesystem substrate, and pytest

## Task 1: Shared Storage Model

**Files**

- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `musubi/storage/schema.sql`
- Modify: `musubi/storage/db.py`
- Test: Rust tests inside `gui/src-tauri/musubi-data/src/lib.rs`
- Test: `musubi/tests/test_g2_schema_migration.py`

**TDD steps**

1. Add failing Rust tests for idempotent creation of
   `session_folder_grants` and append-only `request_folder_grants`.
2. Add failing tests for:
   - a session's ordered current grants;
   - alias and canonical-path uniqueness;
   - 16 external-grant cap;
   - rename/remove isolation between chats;
   - request snapshot retaining removed grants.
3. Add both tables and indexes to the canonical Rust schema/migration path.
4. Mirror the schema in Python's `schema.sql` and embedded `_SCHEMA_SQL`.
5. Add the smallest Rust storage API:
   - list session grants;
   - add/rename/remove a grant;
   - snapshot fixed Musubi root plus current grants for a request;
   - list a request manifest.
6. Normalize alias/path equality according to platform semantics before SQL
   writes; reject duplicates and nested roots.
7. Run focused Rust and Python migration tests.

## Task 2: Console Commands and Runtime Snapshot

**Files**

- Modify: `gui/src-tauri/src/lib.rs`
- Test: Rust tests inside `gui/src-tauri/src/lib.rs`

**TDD steps**

1. Replace picker tests with failing tests for one-folder selection without
   restart.
2. Add failing tests that add/rename/remove use the displayed `chat_id`, reject
   mutation while a run owns the runtime, and leave other sessions unchanged.
3. Add failing launch tests proving:
   - `project_root` remains the Musubi root;
   - no `MUSUBI_WORKSPACE` is exported;
   - current process directory is not changed;
   - the request manifest is captured before spawn;
   - invalid/missing grants stop launch before `Command::spawn`.
4. Reuse the native directory chooser for `choose_folder`; delete preference
   persistence, workspace data-directory probing, restart, and
   `workspace_error`.
5. Add Tauri actions/commands for list, add, rename, and remove.
6. Serialize the captured request registry into bounded launch metadata while
   retaining the database snapshot as the audit source.
7. Ensure spawn failure leaves the request snapshot but releases runtime
   ownership with an explicit failure log.
8. Run focused Console Rust tests.

## Task 3: Immutable Python Root Registry

**Files**

- Add: `musubi/workspace/grants.py`
- Modify: `musubi/agent/run.py`
- Modify: `musubi/agent/subagent.py`
- Test: `musubi/tests/test_agent_loop.py`
- Add/Test: `musubi/tests/test_workspace_grants.py`

**TDD steps**

1. Add failing parser tests for the reserved `musubi` root, multiple external
   aliases, invalid JSON, duplicate aliases/paths, nesting, missing folders,
   cap overflow, and Windows case folding.
2. Implement immutable `FolderGrant` and `RootRegistry` values with one
   canonical resolver.
3. Add failing CLI tests for repeated
   `--add-folder [ALIAS=]PATH`, derived aliases, collision suffixes, and
   fail-before-model behavior.
4. Remove `--workspace`, `_apply_workspace`, environment fallback, and every
   `chdir` assertion introduced by the superseded picker.
5. Keep `musubi_dir`/`MUSUBI_ROOT` authoritative and construct one registry at
   launch.
6. Pass the exact serialized registry through `_server_env`, direct workers,
   and pipeline workers; workers may not widen it.
7. Inject the bounded available-roots block into root and worker context.
8. Run the new registry and focused agent-loop tests.

## Task 4: Root-aware Filesystem and Command Tools

**Files**

- Modify: `musubi/tools/fs.py`
- Modify: `musubi/server.py`
- Modify: `musubi/agent/boundary.py` only if audit normalization requires it
- Test: `musubi/tests/test_fs_tools.py`

**TDD steps**

1. Replace single-root fixture assumptions with an immutable registry fixture.
2. Add failing tests for all seven tools against `musubi` and one external
   root.
3. Add failing denial tests for:
   - unknown root;
   - absolute paths;
   - `..` traversal;
   - symlink/junction escape;
   - unavailable root;
   - an external `cwd` paired with the wrong root.
4. Replace `_workspace_root()` with registry lookup returning a typed resolved
   target.
5. Add optional `root="musubi"` to every MCP tool schema.
6. Keep discovery results relative to one selected root and include root
   identity in structured responses.
7. Make `run_command` choose a child-only working directory without changing
   the server process.
8. Ensure audit payloads include alias, grant ID, relative path, and canonical
   target.
9. Run the filesystem test module.

## Task 5: Mechanical Gates and Artifact Verification

**Files**

- Modify: `musubi/agent/subagent.py`
- Modify: artifact/outcome helpers found from
  `delivered_artifact`, `artifact_manifest`, and completion verification
- Modify affected schemas/prompts under `.github/agents/` only where the
  structured artifact contract is declared
- Test: `musubi/tests/test_mechanical_gate.py`
- Test: `musubi/tests/test_subagent_orchestrator.py`

**TDD steps**

1. Add failing tests for `{root, path}` artifact references and bare-string
   compatibility under `musubi`.
2. Add failing tests that group mechanical checks by root and reject unknown or
   escaped artifact roots.
3. Replace `_mechanical_workspace_root()` with registry-based resolution.
4. Update completion artifact verification to use the request registry rather
   than process environment precedence.
5. Preserve append-only and evaluator-firewall behavior; do not expose the
   request manifest to evaluators unless the artifact resolver itself needs the
   root identity.
6. Run focused mechanical and orchestrator tests.

## Task 6: Session Folder UI

**Files**

- Modify: `gui/src/data/TauriSource.js`
- Modify: `gui/src/data/TauriSource.test.mjs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/model/viewModel.test.mjs`
- Modify: `gui/src/views/Orchestrator.jsx`
- Modify: `gui/src/views/Orchestrator.test.mjs`
- Modify: `gui/src/views/Settings.jsx`
- Modify: `gui/src/views/Settings.test.mjs`
- Modify: `gui/src/index.css`

**TDD steps**

1. Replace Settings picker assertions with failing assertions that Settings has
   no workspace control.
2. Add failing source-action tests for choose/add, rename, and remove with the
   displayed session ID.
3. Add failing view-model tests for:
   - fixed locked `musubi` root;
   - ordered external grants;
   - disabled controls while running;
   - inline error display.
4. Add failing Orchestrator surface tests for the session-folder control.
5. Wire backend state and actions through `TauriSource` and `viewModel`.
6. Render a compact folder-grants control in the session header, with native
   add, editable aliases, remove, paths, and busy state.
7. Remove all workspace-switching/restart client state.
8. Run all Node tests.

## Task 7: Documentation and Compatibility Cleanup

**Files**

- Modify: `AGENTS.md` only if CLI examples require correction
- Modify: `docs/guide.md` or `guide.md`, whichever documents Console operation
- Modify: `docs/roadmap.md`
- Modify/delete superseded branch-only tests and comments referencing global
  Settings workspace selection

**Steps**

1. Document session folder grants and repeated headless `--add-folder`.
2. Mark the roadmap track implemented only after verification passes.
3. Keep the old workspace-picker plan as historical evidence; do not rewrite
   it to describe the replacement.
4. Search for stale `MUSUBI_WORKSPACE`, Settings picker, restart, and
   single-workspace wording; every remaining occurrence must be historical or
   an explicit migration assertion.

## Task 8: Full Verification and Commit

**Commands**

1. `cargo fmt --check --manifest-path gui/src-tauri/Cargo.toml`
2. `cargo test --manifest-path gui/src-tauri/Cargo.toml`
3. `cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml`
4. `npm test --prefix gui`
5. Run the focused Python modules from Tasks 1, 3, 4, and 5.
6. Run the complete Python suite if its baseline is tractable; otherwise record
   the exact pre-existing failures and prove all touched modules.
7. `git diff --check`
8. Confirm only approved files are staged and `vietnam-weather.html` remains
   untracked.
9. Commit with Conventional Commits and repository identity:
   `feat(console): add session folder grants`.

