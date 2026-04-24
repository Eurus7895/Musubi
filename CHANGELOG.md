# Changelog

All notable changes to CopilotHarness. The Python harness version tracks
the repo as a whole; the VS Code extension version is tracked separately
in `copilot-harness-extension/package.json` and ships out of this repo
as a `.vsix`.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
