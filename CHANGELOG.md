# Changelog

All notable changes to CopilotHarness. The Python harness version tracks
the repo as a whole; the VS Code extension version is tracked separately
in `copilot-harness-extension/package.json` and ships out of this repo
as a `.vsix`.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

**Headline:** Phase A.1 + A.2 of the orchestrator pivot — sub-agent
foundation + firewall + result verification shipped at the harness
layer. The extension-side runner (Phase A.3) and orchestrator (Phase B)
now have everything they need to build on top without re-plumbing.

### Phase A.2 — Firewall + result verification

- **`copilot-harness/validation/subagent_context.py`** — frozen
  `SubagentContext(brief, role, role_skill, allowed_tools)` produced by
  `build_subagent_context(brief, role)`. Function signature deliberately
  excludes `session_id` / `db_path` so the firewall is enforced at the
  type level. `SUBAGENT_ROLE_SKILLS` table maps each role to a SKILL.md
  id (Phase A.3 ships the actual files). `assert_no_session_leakage`
  helper rejects payloads that look like main session state.
- **`copilot-harness/validation/verifier.py`** — new
  `verify_subagent_summary(summary, structured, max_tokens=2000,
  schema=None)` returning `SubagentVerifyResult(valid, summary,
  truncated, errors)`. Truncates over-cap text with the marker
  `[truncated by harness — exceeded max_tokens cap]`. Reuses the
  existing secrets + instruction-injection scanners as hard-fails.
  Optional schema check (required / types / enum) accepts string type
  names (`"int"`, `"list"`, …) so JSON-encoded schemas from the
  extension validate without a `jsonschema` dependency.
- **`copilot-harness/server.py`**:
  - `harness_complete_subagent` now passes `summary` + `structured`
    through `verify_subagent_summary` against the row's
    `output_schema`. Rejected summaries coerce status → `failed` with
    a structured error; the offending text is replaced before
    persisting so the parent never sees secrets / injection.
  - New `harness_get_subagent_context(handle_id)` MCP tool — returns
    the firewalled `{brief, role, role_skill, allowed_tools}` payload
    consumed by the Phase A.3 runner.
- **+46 tests:**
  - `tests/test_subagent_context.py` (15) — signature firewall, frozen
    dataclass, closed key set, role-skill mapping completeness,
    leakage detection, static no-session-import assertion.
  - `tests/test_subagent_summary_verify.py` (31) — token cap +
    truncation marker, secrets / injection rejection, schema type-name
    coercion, MCP-layer integration through harness_complete_subagent
    and harness_get_subagent_context.
- Total: **487 passing** (was 441 after A.1; +46 from A.2).

### Phase A.1 — Sub-agent foundation

- **`copilot-harness/storage/db.py`** — `sub_sessions` table + 6 CRUD
  helpers: `insert_sub_session`, `get_sub_session`,
  `update_sub_session_result`, `get_sub_sessions_by_parent`,
  `mark_sub_sessions_abandoned_for_parent`,
  `mark_orphan_running_sub_sessions_abandoned`. JSON-encoded fields
  (`allowed_tools`, `tools_used`, `result_structured`) decoded on read.
  Indexed on `parent_session_id` and `status` (commit `0606ed0`).
- **`copilot-harness/session/sub_sessions.py`** — lifecycle module:
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
- **`copilot-harness/server.py`** — four MCP tools:
  - `harness_spawn_subagent` — validates role / main / parent FK,
    intersects requested tools with role policy, returns handle +
    `effective_tools` + recorded timeout caps.
  - `harness_complete_subagent` — extension-side runner records
    summary / structured / tools_used / turns / status; harness
    auto-escalates on cap breach.
  - `harness_await_subagent` — polls in-process until terminal or
    `wall_clock_timeout_s` exceeded (wall-clock kill); returns
    `still_running` snapshot if `max_wait_s` exhausted first.
  - `harness_list_subagents` — spawn allow-list catalogue for a
    main agent; pipeline stages return `[]` until `pipeline.yaml`
    opts in.
  - Server import-time `sub_sessions.sweep_orphans()` — startup
    sweep marks any `running` row whose parent isn't `active` as
    `abandoned`, recovering from a crashed harness without leaving
    dangling state.
  - `policy_engine.py` import path: `_add_scripts_to_path` resolves
    against `HARNESS_ROOT` first (extension binary) then the dev tree.

### Changed

- **MCP tool count** in `CLAUDE.md` § MCP Tools: 18 → 23
  (`harness_spawn_subagent`, `harness_complete_subagent`,
  `harness_await_subagent`, `harness_list_subagents`,
  `harness_get_subagent_context`).
- **Hard Invariant #8** in `CLAUDE.md` reworded — sub-agent harness
  primitives are now shipped (Phase A.1); the chat-marker / audit
  surface is what's still pending in Phase A.3.

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
    coercion, MCP-layer integration through `harness_complete_subagent`
    and `harness_get_subagent_context`.
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
per-stage output) + dedicated CopilotHarness mark. All previous dashboard
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
  pipeline / step header, routing to a new `copilot-harness.showTasks`
  command that focuses the sidebar Tasks view in one click.
- **Per-stage output in chat** — after every `✓ <agent> — Xs — …` line
  a collapsible `<details><summary>output</summary>…</details>` block
  renders the agent's structured output as markdown (tasks for planner,
  modules for designer, files_modified + implementation_notes for coder,
  status + issues + fix_instructions for reviewer).
- **CopilotHarness mark** — `media/icons/harness.svg` (single-color,
  `currentColor`) replaces the `$(checklist)` / `$(robot)` codicons on
  the activity-bar container and the chat participant avatar. The
  design is a pure network-node abstraction: three inputs (planner,
  designer, coder) converge on one anchor node (the harness) and one
  output leaves below (reviewer / shipped change). `media/icons/harness-hero.svg`
  is the 256×256 marketplace source — rasterise to PNG via
  `rsvg-convert` to populate the `icon` field in `package.json` when
  publishing.
- **MCP commands exposed to the extension:** `copilot-harness.refreshTasks`,
  `copilot-harness.openSessionArtifact`, `copilot-harness.showTasks`.

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

Extension bootstrap: `McpClient` spawns the bundled `copilot-harness`
PyInstaller binary directly; `@harness` chat participant registered;
direct mode + slash commands + hooks.json + policy engine; `/help`
dynamic table; plugin manifest; direct-mode skill catalog; Tier 2
memory compaction + cross-session query; Level-1 probe infrastructure.
379 tests in the Python harness.

---

*Tag scheme:* `vMAJOR.MINOR.PATCH` matching
`copilot-harness-extension/package.json` `version`.
