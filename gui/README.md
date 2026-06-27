# Musubi Console

> New to the console? Start with the user guide: [`docs/guide.md`](../docs/guide.md)
> section "Console". This file is the architecture and contributor reference.

A dark, technical governance console for Musubi. The React UI is packaged as a
standalone Tauri desktop app that reads Musubi's `audit.db` directly through the
Rust backend: no localhost-only preview, no demo source, no Copilot.

Musubi is a governance layer for agentic software-engineering work: firewall,
audit, validator, budget, and skill injection. The agent reasons; Musubi
controls the environment.

## Run

Run npm commands from the repository root:

```bash
npm install
npm run tauri:dev
npm run tauri:build

# Point the console at a real Musubi database:
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

`npm run tauri:dev` requires the Rust toolchain and platform webview libraries
(Linux: `webkit2gtk-4.1` and `libgtk-3-dev`; macOS/Windows: built in). If Tauri
fails with `failed to run 'cargo metadata'` or `program not found`, `cargo` is
missing from `PATH`. If Rust fails with `link.exe not found`, the MSVC linker is
missing. On Windows run:

```powershell
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
cargo --version
where.exe link
```

Open a new terminal after installing these tools. Without `MUSUBI_DB` the app
seeds an in-memory demo so it runs standalone.

Icons are generated with `npm run icons`. For `.ico`/`.icns` generation, run
`npm run tauri icon src-tauri/icons/icon.png`.

Prebuilt installers are produced by the
[`Desktop build`](../.github/workflows/desktop.yml) GitHub Actions workflow for
macOS, Windows, and Linux.

## Data Source

The UI is desktop-only. `TauriSource` connects through native IPC to the Rust
core, which reads `audit.db` and emits domain snapshots. The backend contract
(SQLite schema and JSON shape) is in [`src-tauri/SCHEMA.md`](src-tauri/SCHEMA.md).
The Rust reader and its tests are in `src-tauri/musubi-data/`:

```bash
npm run test:data
```

## Views

Six sections are backed by the Tauri backend:

- **Orchestrator**: the driver knot spawning governed sub-agents over a woven
  net; each card shows model, spawn-order badge, turn cap, and wall-clock
  budget.
- **Pipeline studio**: build or run chains such as `feature-dev`, `bugfix`, and
  `explore`.
- **Policy**: fail-closed PreToolUse allow/deny stream and role tool surfaces.
- **Audit**: append-only ledger, filterable by event type.
- **Models**: LMRouter vendor profiles and active profile selection.
- **Skills**: pushed/pulled catalog with the "default to skill, not agent" rule.

A persistent trust strip surfaces the Hard Invariants: zero-LLM substrate,
fail-closed policy, append-only audit, and evaluator firewall.

## URL Options

The desktop window accepts a view selector:

- `?startView=orchestrator|pipeline|policy|audit|models|skills`

## Layout

```text
src/
  App.jsx                  shell: activity bar + trust strip + view switch
  components/              ActivityBar, TrustStrip, ChatBody
  views/                   Orchestrator, Pipeline, Policy, Audit, Models, Skills
  data/
    createSource.js        requires the Tauri desktop shell
    TauriSource.js         native IPC source: invoke, listen, actions
  model/
    useMusubi.js           hook: owns the source and builds the view-model
    viewModel.js           pure presentation: state + actions to view-model
    data.js                role/profile/skill/preset tables and color lookups
    format.js              fmtClock / rhex / pick
    styleHelpers.js        roleChip / navStyle / auditBtn
    NetGraphic.jsx         woven-net SVG
  lib/
    Box.jsx                element wrapper with style + hover
    css.js                 CSS-string to React style object
src-tauri/
  src/lib.rs               Tauri commands and audit.db poller
  musubi-data/             webkit-free Rust core: audit.db to State
  SCHEMA.md                backend contract
  tauri.conf.json          desktop window and bundle config
```

`TauriSource` implements the source contract (`state`, `actions`, `subscribe`,
`start`, `stop`) and feeds the pure `buildViewModel`.
