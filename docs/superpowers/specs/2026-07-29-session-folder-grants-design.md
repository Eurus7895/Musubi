# Session Folder Grants Design

**Date:** 2026-07-29
**Status:** Proposed
**Scope:** Console Orchestrator, standalone agent host, filesystem substrate,
policy/audit, and completion verification

## Context

The workspace-picker branch currently treats an operator-selected application
folder as a replacement process root. `MUSUBI_WORKSPACE` takes precedence over
`MUSUBI_ROOT`, the standalone host changes its current working directory, and
all filesystem tools resolve against that one selected folder. The Console
stores the selection as a global preference under Settings and restarts to
apply it.

That model conflicts with the product boundary:

- Musubi is the harness and must keep one stable root for its driver,
  substrate, skills, agents, pipelines, policy, and audit.
- Filesystem authority belongs to a session, not to global Settings.
- A session may need to update more than one existing folder.
- Changing a session's folders must not mutate the authority of an in-flight
  request.
- External coding-agent launchers add no governance value and are not part of
  this feature.

## Decision

Keep the Musubi checkout/install as the fixed harness root. Model-visible file
and command tools use that root by default and may additionally target an
explicit, session-scoped folder grant.

A folder grant is authority to read and write one existing directory. It does
not replace `MUSUBI_ROOT`, change the host process working directory, or become
a second source for Musubi's own configuration and prompt catalog.

Each request captures the displayed session's current grants before the agent
process starts. The captured request manifest is immutable and is the only
authority used for that request and all workers it spawns.

## Terminology

- **Harness root:** The resolved `MUSUBI_ROOT`. Its reserved model-visible name
  is `musubi`. It is always present and cannot be removed or renamed.
- **Folder grant:** One external directory attached to an Orchestrator session.
- **Session manifest:** The editable current set of grants for one `chat_id`.
- **Request manifest:** An immutable copy of the harness root and session
  manifest keyed by `request_id`.
- **Root alias:** A short model-visible name such as `web` or `api`.
- **Grant ID:** An opaque stable identifier used by storage and audit. Aliases
  may change between requests; grant IDs do not.

## User Experience

Folder management moves from Settings to the Orchestrator session header.

The session surface shows:

- `musubi` as the fixed root, visibly locked;
- each attached folder's alias and canonical path;
- **Add folder**, **Rename**, and **Remove** actions.

Native directory selection adds one existing directory at a time. A default
alias is derived from the folder basename and receives a numeric suffix on
collision. Aliases are editable and must match
`[a-z][a-z0-9_-]{0,31}`. The name `musubi` is reserved.

Folder editing is rejected while any request owns the runtime. A new session
starts with no external grants; grants are never copied implicitly from
another session. Removing a grant affects only future requests. Historical
requests continue to display their captured manifest.

The prior global workspace preference, Settings picker, and restart prompt are
removed.

## Persistence

The shared Console database gains two tables.

```sql
CREATE TABLE session_folder_grants (
    chat_id        TEXT NOT NULL,
    grant_id       TEXT NOT NULL,
    alias          TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (chat_id, grant_id),
    UNIQUE (chat_id, alias),
    UNIQUE (chat_id, canonical_path)
);

CREATE TABLE request_folder_grants (
    request_id     TEXT NOT NULL,
    chat_id        TEXT NOT NULL,
    grant_id       TEXT NOT NULL,
    alias          TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    captured_at    TEXT NOT NULL,
    PRIMARY KEY (request_id, grant_id),
    UNIQUE (request_id, alias)
);
```

`request_folder_grants` includes a synthetic row for the reserved `musubi`
root. Request rows are append-only. Current session rows may be renamed,
reordered, or removed because prior request authority remains preserved by the
snapshot table and audit events.

Schema creation and migration remain idempotent and synchronized across the
Rust and Python storage owners already used by Console and the standalone
host.

## Request Launch

Console performs launch preparation in this order:

1. Resolve the displayed `chat_id` and verify that no request is running.
2. Load and validate its session manifest.
3. Resolve the fixed harness root.
4. Mint `request_id`.
5. While holding Console's established runtime/DB lock order, write the
   complete request manifest in one database transaction and then claim the
   in-memory runtime before releasing either lock. A database failure leaves
   the runtime unclaimed.
6. Start the standalone host with the existing request, chat, database, and
   runtime-log identifiers.

If any folder is missing, no longer a directory, duplicated, overlaps another
grant, or cannot be canonicalized, preparation fails before the agent process
starts.

The host stays in the Musubi root. It loads the request manifest once and
passes a bounded serialized copy to the MCP tool server and every worker.
There is no runtime fallback to the mutable session manifest. The registry is
immutable for the lifetime of the process.

The manifest is capped at 16 external grants. This bounds prompt size, launch
metadata, validation work, and the number of filesystem authorities an
operator must review.

## Tool Contract

Every direct filesystem tool gains an optional `root` argument:

```json
{
  "root": "web",
  "path": "src/App.tsx"
}
```

The default is `root="musubi"` for backward compatibility. `root` accepts only
an alias present in the immutable request registry. A path is always relative
to its selected root. Absolute paths are rejected even when they happen to
resolve inside an allowed directory; this prevents the path itself from
becoming a second root-selection mechanism.

The affected tools are:

- `musubi_read_file`
- `musubi_glob`
- `musubi_grep`
- `musubi_write_file`
- `musubi_append_file`
- `musubi_edit_file`
- `musubi_run_command`

`musubi_run_command` also keeps its optional relative `cwd`, but resolves it
under the selected root. Selecting an external root changes only that child
command's working directory. It never changes the driver or MCP server process
working directory.

Tool results and audit details identify the selected alias, stable grant ID,
relative path, and canonical resolved path. Discovery results remain relative
to the selected root and never mix paths from multiple roots in one call.

## Path and Policy Enforcement

Root selection and path resolution have one substrate owner. Callers do not
concatenate grant paths themselves.

For every operation the resolver:

1. looks up the alias in the immutable request registry;
2. joins the supplied relative path to the registered canonical directory;
3. resolves symlinks/junctions through the nearest existing ancestor when a
   create target does not exist yet;
4. verifies the final target remains below the selected root;
5. returns a typed resolved target carrying both grant identity and path.

Unknown aliases, absolute paths, traversal, unavailable roots, and links that
escape a root are deterministic policy denials. They do not trigger a model
retry through a less restricted path. Duplicate and nested grants are rejected
when the session manifest is saved so one target has only one authority name.
Path and alias equality follows platform semantics, including
case-insensitive comparison on Windows; SQL text uniqueness is not the only
guard.

Workers inherit the exact request registry. A worker cannot add a folder,
rename an alias, or widen access. The root agent cannot expose a broader
registry than the parent request captured.

## Prompt and Discovery

The root and worker system context receives a bounded manifest:

```text
Available roots:
- musubi (fixed harness root): C:\Workspace\Projects\Musubi
- web: D:\Projects\web-app
- api: D:\Projects\api

Use the root argument for every operation outside musubi.
Paths must be relative to the selected root.
```

Musubi does not recursively merge instructions, skills, agents, pipelines, or
configuration from attached folders. Those catalogs continue to resolve from
the fixed harness root. The agent may read project-local documentation in an
attached folder as ordinary files when the task requires it.

Workspace detection may inspect each selected root independently for
mechanical validation, but it cannot replace the harness catalog or silently
grant another directory.

## Mechanical Gates and Artifacts

Artifact references become root-qualified:

```json
{"root": "web", "path": "src/App.tsx"}
```

A temporary compatibility parser treats a bare relative string as a path under
`musubi`; newly emitted manifests use the structured form.

Mechanical checks group changed artifacts by root and run from the applicable
root. Completion verification resolves every artifact through the same
immutable registry used by filesystem tools. A result claiming an unknown
root, an escaped path, or a file outside the request snapshot fails closed.

## Standalone CLI

Console sessions are the persistence owner. Headless runs use a repeatable
folder option to build an ephemeral request manifest while keeping the current
Musubi root:

```text
agent "<task>" --add-folder web=D:\Projects\web-app \
               --add-folder api=D:\Projects\api
```

The alias may be omitted and derived from the basename. Duplicate aliases or
paths fail before the model is called.

The branch-local `--workspace` and `MUSUBI_WORKSPACE` behavior is superseded:
neither may change the process directory or replace `MUSUBI_ROOT`. Because the
single-workspace picker has not shipped from this branch, it is removed rather
than retained as an ambiguous compatibility path.

## Failure and Concurrency Behavior

- Add, rename, reorder, and remove are rejected while the runtime is owned.
- Launch fails before spawning when manifest persistence or validation fails.
- A deleted or unavailable folder causes the next request to fail closed; it
  does not fall back to `musubi`.
- An in-flight request continues against its immutable snapshot. If an
  underlying directory disappears during the run, the next operation returns
  an explicit unavailable-root error.
- Cancellation does not delete request-manifest rows.
- Session deletion removes only its editable current grants; request snapshots
  remain available for audit according to the database's retention policy.

## Migration

This feature replaces unshipped behavior on the current feature branch:

- delete the global workspace preference and its restart flow;
- stop setting `MUSUBI_WORKSPACE`;
- stop changing the process current directory;
- remove the single-workspace Settings UI;
- replace tests that assert re-rooting with tests for fixed-root grants.

No implicit import of the old global preference occurs. Silent migration would
grant a folder to sessions that never explicitly selected it, violating the
session authority boundary.

## Verification

Required coverage includes:

- schema creation and upgrade from an existing database;
- session isolation, alias/path uniqueness, ordering, and a 16-grant cap;
- add/rename/remove rejection while a request runs;
- atomic request snapshot creation and historical retention;
- fixed `MUSUBI_ROOT` and unchanged process current directory;
- root-aware read, discovery, write, append, edit, and command execution;
- unknown root, absolute path, traversal, symlink/junction escape, duplicate,
  nested, missing, and deleted-folder failures;
- worker inheritance without authority widening;
- root-qualified artifact and mechanical-gate verification;
- Console rendering and command wiring;
- CLI repeated-folder parsing and fail-before-model behavior;
- regression suites for Rust, frontend Node tests, and Python.

## Non-goals

- Launching or configuring external coding agents.
- Importing tool permissions from another product.
- Per-file grants.
- Network or credential grants.
- Automatically discovering folders from task text.
- Changing the harness root per session.
- Merging attached folders into one virtual filesystem through links.
