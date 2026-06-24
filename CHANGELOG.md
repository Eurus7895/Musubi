# Changelog

All notable changes to Musubi. The Python harness version tracks
the repo as a whole; the VS Code extension version is tracked separately
in `copilot-harness-extension/package.json` and ships out of this repo
as a `.vsix`.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Renamed to Musubi + standalone single-agent pivot

- **Breaking: `CopilotHarness` → `Musubi`.** The MCP tool prefix is now
  `musubi_*` (was `harness_*`); the package dir is `musubi/`; the
  `musubi-tier` tag and `MUSUBI_ROOT` env replace the old equivalents.
  The standalone CLI takes tools dynamically, so it is prefix-agnostic;
  the VS Code extension was deliberately left on the old prefix and is
  now broken-by-design, pending deletion (roadmap Step 7).
- **Hard Invariant #1 redrawn** as a substrate/driver boundary: the
  substrate makes zero LLM calls; the driver (agent loop) reaches a model
  through one inject point (`vscode.lm` or the vendor-agnostic
  `LMRouter`). This unblocks the standalone host.
- **Docs consolidated.** `docs/design.md` and `docs/musubi-direction.md`
  removed; their durable framing folds into `docs/roadmap.md`, whose plan
  is now a numbered Step 1..7 sequence toward a standalone, model-agnostic
  single-agent host with the staged pipeline scheduled for dissolution.

### Reversible input compression (substrate)

- New `musubi/compression/` — deterministic, zero-LLM compressors
  (JSON-minify, code comment/blank-strip, whitespace collapse) routed by
  content type, with a content-hash blob store for reversibility.
- New `musubi_retrieve(ref_id)` MCP tool returns the verbatim original of
  any compressed payload (CCR-style: the model reads compressed; audit and
  on-demand retrieval read the original).
- Wired into `musubi_read_file` / `musubi_run_command` behind the
  `MUSUBI_COMPRESS` flag (default OFF until the eval suite clears it).
  Measured ~67% reduction on indented JSON with an exact round-trip.
- Idea credit: headroom. The learned text compressor is deliberately not
  adopted — it would be a model call in the substrate (violates HI #1).

### Agent CLI — vendor-agnostic substrate driver (Python)

- New `agent` console script (lives in `musubi/agent/`).
  Drives the Musubi MCP substrate via a direct LLM API — Anthropic or
  OpenAI today, extensible to other vendors by implementing one
  `LMRouter` subclass. Restores end-to-end usage when Copilot
  Chat isn't available (e.g. quota exhausted), and is the basis for the
  standalone single-agent host (roadmap Steps 4–5).
- Aligns with the substrate discipline: the agent IS the model's native
  mode (the driver reasons; the substrate controls the environment). The
  4-stage pipeline is not ported — it remains `musubi-tier: ephemeral`,
  scheduled for dissolution in roadmap Step 7.
- Vendor SDKs are optional extras: `pip install -e .[anthropic]` or
  `.[openai]` or `.[all]`. Defaults: `claude-haiku-4-5` (Anthropic),
  `gpt-4o-mini` (OpenAI). Override via `--model`.
- Also fixes two latent wheel-packaging bugs surfaced by adding the
  new package: `composer` (top-level module imported by `server.py`)
  was missing from `py-modules`, and `workspace` (item-4 package) was
  missing from `packages`. Both now ship in the wheel.
- 17 new tests cover the vendor abstraction, OpenAI ↔ Anthropic wire
  conversion, and the end-to-end loop against a real MCP server
  (FakeRouter replays canned responses). Total: 880 passing.

### Filesystem + command MCP tools (harness as complete substrate)

Adds four new `musubi_*` MCP tools so any MCP client — butler, Claude
Code, Cursor, a custom driver — can actually edit files and run
commands through the harness without depending on a client-side tool
set (which historically was Copilot Chat's). Closes the gap that
became obvious the moment the butler shipped: 51 governance tools, 0
file tools.

- `tools/fs.py` — workspace-scoped implementation. Every path is
  resolved against `_workspace_root()` (MUSUBI_ROOT env var or cwd)
  and rejected if the resolved target escapes the workspace. No
  "dangerous command" heuristic — the user picked the model + the
  catalog; the substrate's job is path-safety + audit, not paternalism.
  Audit on stderr (`[harness.tools.fs]`); a SQL `fs_audit` table is a
  follow-up if patterns show it's needed.
- `server.py` — four new MCP tools delegate to `tools/fs.py`:
  - `musubi_read_file(path)` — up to 5 MB UTF-8.
  - `musubi_write_file(path, content, create_parents=True)` —
    creates or replaces, mkdirs parents by default.
  - `musubi_edit_file(path, old_string, new_string, replace_all=False)`
    — defaults to "match must be unique" semantics; explicit
    replace_all path returns the count.
  - `musubi_run_command(command, timeout_seconds=60, cwd=None)` —
    shell command via `sh -c`. Output capped at 1M chars
    (head + tail preserved on overflow). Timeout returns partial
    stdout/stderr.
- 36 new tests in `tests/test_fs_tools.py` covering: traversal
  rejection (dotdot + absolute outside), unicode handling, missing
  files, directory-as-path rejection, edit uniqueness contract,
  replace_all count, command timeout + truncation, MCP-layer JSON
  round-trip for each tool.
- `pyproject.toml` — `tools` added to packages.
- Verified end-to-end: a real MCP client (smoke-tested with
  `stdio_client`) writes → reads → edits → runs and gets escape
  blocked on `/etc/passwd`. Audit lines fire on every call.

This is what makes the butler (and any MCP client) self-sufficient:
the harness no longer assumes the client brings file tools. Total:
916 passing on top of the butler PR (880 + 36).

**Headline:** Phase A of the orchestrator pivot is complete on the
Python side — sub-agent foundation, firewall, result verification,
role .agent.md + SKILL.md files, and a durable audit log shipped at the
harness layer. The remaining Phase A.3 work (mcpClient EventEmitter +
subagentRendering.ts chat markers) is TypeScript and lands separately;
until it does, the extension polls `musubi_query_subagent_events` for
spawn / completion events.

### Phase A.3 — Role files + spawn-event audit (Python side)

- **`.github/agents/explorer.agent.md`** — read-only sub-agent for
  codebase scans (`Read + View + Grep + Glob`).
- **`.github/agents/investigator.agent.md`** — read-only diagnostics
  (`+ Bash`) for narrow `pytest` / `ruff` / `mypy` / `git diff` runs;
  forbidden-command list in the role file rules out mutation /
  network operations.
- **`.github/agents/reviewer-aux.agent.md`** — single-file checklist
  review (`Read + View`); deliberately omits Grep / Glob so the role
  cannot wander into the wider codebase.
- **`.github/skills/{explorer,investigator,reviewer-aux}/SKILL.md`** —
  procedure docs the harness pushes via
  `validation/subagent_context.SUBAGENT_ROLE_SKILLS`. Each documents
  reduce-the-brief, tool-selection, summary format, structured-payload
  shape, and anti-patterns specific to the role.
- **`musubi/storage/subagent_audit.py`** — new
  `subagent_audit` table on `audit.db` with `record_spawn`,
  `record_complete`, `query_events`. JSON-encoded fields
  (`allowed_tools`, `tools_used`, `verification_errors`) decoded on
  read. Indexed on `ts`, `parent_session_id`, and `handle_id`.
- **`musubi/server.py`**:
  - `musubi_spawn_subagent` — writes a `'spawned'` audit row after a
    successful spawn (audit failures swallow rather than block the
    spawn — durable evidence is best-effort, not blocking).
  - `musubi_complete_subagent` — writes a `'completed'` audit row
    capturing `final_status`, `escalated`, `turns`, `tools_used`,
    `summary_truncated`, and `verification_errors`. Mirror of the
    spawn row keyed on the same `handle_id`.
  - New **`musubi_query_subagent_events(parent_session_id?,
    handle_id?, since_ts?, limit=200)`** MCP tool exposes the audit
    log so the extension can poll for spawn / completion events and
    render chat markers without losing visibility on a window reload.
- **+20 tests:** `tests/test_subagent_audit.py` covers writer field
  coverage, query filters (parent / handle / since_ts), limit /
  ordering, server-wired audit on spawn / complete / escalation /
  verification-failure / truncation, MCP-tool query semantics,
  end-to-end no-silent-sub-agents invariant across all three roles,
  and presence of the role .agent.md + SKILL.md files.
- Total: **507 passing** (was 487; +20 from A.3).

### Phase A.2 — Firewall + result verification

- **`musubi/validation/subagent_context.py`** — frozen
  `SubagentContext(brief, role, role_skill, allowed_tools)` produced by
  `build_subagent_context(brief, role)`. Function signature deliberately
  excludes `session_id` / `db_path` so the firewall is enforced at the
  type level. `SUBAGENT_ROLE_SKILLS` table maps each role to a SKILL.md
  id (Phase A.3 ships the actual files). `assert_no_session_leakage`
  helper rejects payloads that look like main session state.
- **`musubi/validation/verifier.py`** — new
  `verify_subagent_summary(summary, structured, max_tokens=2000,
  schema=None)` returning `SubagentVerifyResult(valid, summary,
  truncated, errors)`. Truncates over-cap text with the marker
  `[truncated by harness — exceeded max_tokens cap]`. Reuses the
  existing secrets + instruction-injection scanners as hard-fails.
  Optional schema check (required / types / enum) accepts string type
  names (`"int"`, `"list"`, …) so JSON-encoded schemas from the
  extension validate without a `jsonschema` dependency.
- **`musubi/server.py`**:
  - `musubi_complete_subagent` now passes `summary` + `structured`
    through `verify_subagent_summary` against the row's
    `output_schema`. Rejected summaries coerce status → `failed` with
    a structured error; the offending text is replaced before
    persisting so the parent never sees secrets / injection.
  - New `musubi_get_subagent_context(handle_id)` MCP tool — returns
    the firewalled `{brief, role, role_skill, allowed_tools}` payload
    consumed by the Phase A.3 runner.
- **+46 tests:**
  - `tests/test_subagent_context.py` (15) — signature firewall, frozen
    dataclass, closed key set, role-skill mapping completeness,
    leakage detection, static no-session-import assertion.
  - `tests/test_subagent_summary_verify.py` (31) — token cap +
    truncation marker, secrets / injection rejection, schema type-name
    coercion, MCP-layer integration through musubi_complete_subagent
    and musubi_get_subagent_context.
- Total: **487 passing** (was 441 after A.1; +46 from A.2).

### Phase A.1 — Sub-agent foundation

- **`musubi/storage/db.py`** — `sub_sessions` table + 6 CRUD
  helpers: `insert_sub_session`, `get_sub_session`,
  `update_sub_session_result`, `get_sub_sessions_by_parent`,
  `mark_sub_sessions_abandoned_for_parent`,
  `mark_orphan_running_sub_sessions_abandoned`. JSON-encoded fields
  (`allowed_tools`, `tools_used`, `result_structured`) decoded on read.
  Indexed on `parent_session_id` and `status` (commit `0606ed0`).
- **`musubi/session/sub_sessions.py`** — lifecycle module:
  `spawn` (uuid hex[:12] handle, validates row-level invariants),
  `complete` (terminal recording + auto-escalation when
  `turns >= max_turns` or `elapsed > wall_clock_timeout_s`, with reason
  appended to summary), `abandon`, `cascade_abandon_for_parent`,
  `sweep_orphans`, `list_for_parent`. Status set is closed:
  `running → done | failed | escalated | abandoned`.
- **`scripts/policy_engine.py`** — sub-agent slice:
  - `SUBAGENT_POLICIES` — per-role tool allow-list
    (`explorer = Read+View+Grep+Glob`,
    `investigator = + Bash`,
    `reviewer-aux = Read+View`).
  - `MAIN_SUBAGENT_ALLOWLIST` — per-main set of roles. `orchestrator`
    gets all three; pipeline stages (`planner` / `designer` / `coder` /
    `reviewer`) start empty (Phase B opts them in via
    `pipeline.yaml subagents:`).
  - Helpers: `check_subagent_allowed`, `list_subagent_roles`,
    `get_subagent_tools`, `effective_subagent_tools`
    (`role ∩ main ∩ requested`), `subagent_deny_reason`.
- **`musubi/server.py`** — four MCP tools:
  - `musubi_spawn_subagent` — validates role / main / parent FK,
    intersects requested tools with role policy, returns handle +
    `effective_tools` + recorded timeout caps.
  - `musubi_complete_subagent` — extension-side runner records
    summary / structured / tools_used / turns / status; harness
    auto-escalates on cap breach.
  - `musubi_await_subagent` — polls in-process until terminal or
    `wall_clock_timeout_s` exceeded (wall-clock kill); returns
    `still_running` snapshot if `max_wait_s` exhausted first.
  - `musubi_list_subagents` — spawn allow-list catalogue for a
    main agent; pipeline stages return `[]` until `pipeline.yaml`
    opts in.
  - Server import-time `sub_sessions.sweep_orphans()` — startup
    sweep marks any `running` row whose parent isn't `active` as
    `abandoned`, recovering from a crashed harness without leaving
    dangling state.
  - `policy_engine.py` import path: `_add_scripts_to_path` resolves
    against `MUSUBI_ROOT` first (extension binary) then the dev tree.

### Changed

- **MCP tool count** in `CLAUDE.md` § MCP Tools: 18 → 24
  (`musubi_spawn_subagent`, `musubi_complete_subagent`,
  `musubi_await_subagent`, `musubi_list_subagents`,
  `musubi_get_subagent_context`, `musubi_query_subagent_events`).
- **Hard Invariant #8** in `CLAUDE.md` rewritten — every spawn writes a
  durable `subagent_audit` row and surfaces via
  `musubi_query_subagent_events`; the chat-marker UX layer
  (`subagentRendering.ts`) consumes the same audit log.

### Tests (combined A.1 + A.2)

- **+117 tests** across four new files:
  - A.1: `tests/test_sub_sessions.py` (43) — handle uniqueness, status
    transitions, auto-escalation on max_turns + wall-clock breach,
    cascade-on-parent-end, startup orphan sweep, abandon, list-for-parent
    ordering, MCP-tool integration covering spawn → complete → await
    + wall-clock kill via await.
  - A.1: `tests/test_subagent_policy.py` (28) — policy table shape,
    intersection rules, per-main filtering, fail-closed on unknown
    main / role / disjoint tools, `deny_reason` ergonomics.
  - A.2: `tests/test_subagent_context.py` (15) — signature firewall,
    frozen dataclass, closed key set, role-skill mapping completeness,
    leakage detection, static no-session-import assertion.
  - A.2: `tests/test_subagent_summary_verify.py` (31) — token cap +
    truncation marker, secrets / injection rejection, schema type-name
    coercion, MCP-layer integration through `musubi_complete_subagent`
    and `musubi_get_subagent_context`.
- Total: **487 passing** (was 370).

### Roadmap impact

- `docs/design.md` § Phase A — Day A.1 + Day A.2 flipped from `[ ]`
  to `[x]`. Phase A.3 (role files + spawn-event chat markers + audit
  rows) is the only remaining task before End-of-A checkpoint flips
  fully ✅.
- BP 13 / BP 15 in § Best Practices Compliance: status note bumped
  to "Phase A.1 + A.2 ✅ shipped — A.3 pending".

---

## [v0.4.0] — 2026-04-24

**Headline:** Sidebar Tasks view + in-chat ergonomics (Show Tasks button,
per-stage output) + dedicated Musubi mark. All previous dashboard
attempts reverted — the chat participant and a native tree view are now
the only two surfaces.

### Added

- **Tasks sidebar (`src/tasksView.ts`)** — native `vscode.TreeDataProvider`
  contributed to a new `copilotHarness` activity-bar container. Two
  sections:
  - *Active session* — stages (pending / in_progress / complete / failed)
    with codicon status markers, attempt counter, live refresh as
    pipeline stages transition.
  - *History* — past sessions read from `.harness/sessions/<sid>/` on
    disk; outcome inferred from the latest `review.md`. Expanding a
    row lists the stage artifacts; clicking any opens its `.md`
    in an editor tab.
- **Show Tasks chat button** — `stream.button` emitted after every
  pipeline / step header, routing to a new `musubi.showTasks`
  command that focuses the sidebar Tasks view in one click.
- **Per-stage output in chat** — after every `✓ <agent> — Xs — …` line
  a collapsible `<details><summary>output</summary>…</details>` block
  renders the agent's structured output as markdown (tasks for planner,
  modules for designer, files_modified + implementation_notes for coder,
  status + issues + fix_instructions for reviewer).
- **Musubi mark** — `media/icons/harness.svg` (single-color,
  `currentColor`) replaces the `$(checklist)` / `$(robot)` codicons on
  the activity-bar container and the chat participant avatar. The
  design is a pure network-node abstraction: three inputs (planner,
  designer, coder) converge on one anchor node (the harness) and one
  output leaves below (reviewer / shipped change). `media/icons/harness-hero.svg`
  is the 256×256 marketplace source — rasterise to PNG via
  `rsvg-convert` to populate the `icon` field in `package.json` when
  publishing.
- **MCP commands exposed to the extension:** `musubi.refreshTasks`,
  `musubi.openSessionArtifact`, `musubi.showTasks`.

### Changed

- **Pipeline emission points** (`src/pipeline.ts`) — `runPipeline`,
  `runStep`, and `runCorrectionLoop` now accept an optional `onChange`
  callback fired at session-start and every stage transition, so the
  Tasks view can refresh itself without polling. Debounced 150 ms in
  `extension.ts` to collapse bursts during rapid retries.
- **Build script** — `scripts/build-parallel.sh` wraps `tsc` in
  `bash -c "cd '$EXT_DIR' && exec npx tsc -p ."` so Windows PowerShell
  users whose `npm run package` shells out to WSL bash no longer hit
  TS5058 (Windows tsc.exe can't read `/mnt/c/...` path arguments).

### Removed

- **Dashboard webview** (introduced in v0.3.0, expanded in v0.3.2) —
  deleted. Copilot Chat already provides the rendering surface
  (streaming markdown, syntax highlighting, history, model picker);
  re-implementing it in a webview was ~1,400 LoC of duplication at
  lower quality. The Tasks TreeView is the persistent sidebar
  instead. See 45432ce for the full revert.

### Fixed

- **Activity-bar icon renders correctly** — the first cut of
  `harness.svg` set `stroke="currentColor"` at the root and relied on
  inheritance, which VS Code's activity-bar renderer strips before
  re-colouring. Now every element sets `fill`/`stroke` directly and
  the four `<line>` children are collapsed into one `<path>` of
  M/L segments (107e75d).
- **MCP tool count in docs** — README Diagnostics said "17" tools;
  `server.py` actually exposes 18. Synced both README and CLAUDE.md
  MCP Tools section (f3ba28b).

### Docs

- README `Tasks Sidebar` section (v0.4.0) documents the new view.
- CLAUDE.md gains `Tasks Sidebar TreeView (v0.4.0)` with the full data
  sources + refresh contract + invariants.
- AGENTS.md Session Protocol notes the two native surfaces.

### Extension version

`0.3.1 → 0.4.0`.

---

## [v0.3.1] — 2026-04-24

Reverted the v0.3.0 Dashboard webview and replaced it with rich in-chat
rendering. Each stage streams a `### ⏳ <agent>` header, a tag line with
the injected skill / memory / firewall / schema / policy, per-stage
timing, and — on reviewer fail — a blockquote with the reviewer verdict
and `fix_instructions`. Pipeline end emits a `[View plan.md →]` anchor.
Single surface, no separate panel. Net diff from v0.3.0: -1,375 / +294.

## [v0.3.0] — 2026-04-24 (superseded)

Dashboard webview (editor panel) driven by `postMessage` events from
`pipeline.ts`. Full HTML/CSS card with colored status dots, pulse
animation, tags, retry block, live elapsed timer, footer action
buttons. Reverted in v0.3.1. The design is preserved in the git
history at commit 170c350 for future reference.

## [v0.2.0] — earlier

Extension bootstrap: `McpClient` spawns the bundled `musubi`
PyInstaller binary directly; `@harness` chat participant registered;
direct mode + slash commands + hooks.json + policy engine; `/help`
dynamic table; plugin manifest; direct-mode skill catalog; Tier 2
memory compaction + cross-session query; Level-1 probe infrastructure.
379 tests in the Python harness.

---

*Tag scheme:* `vMAJOR.MINOR.PATCH` matching
`copilot-harness-extension/package.json` `version`.
