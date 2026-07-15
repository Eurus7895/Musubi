# Musubi Console

> New to the console? Start with the user guide: [`docs/guide.md`](../docs/guide.md)
> section "Console". This file is the architecture and contributor reference.

A dark, technical governance console for Musubi. The React UI is packaged as a
Windows Musubi installer bootstrap: desktop GUI plus runtime checks for the
Python `musubi` and `agent` CLIs. The GUI reads Musubi's `audit.db` directly
through the Rust backend: no localhost-only preview, no Copilot.

Musubi is a governance layer for agentic software-engineering work: firewall,
audit, validator, budget, and skill injection. The agent reasons; Musubi
controls the environment.

## Run

Primary Windows path: install a prebuilt artifact from the **Desktop build**
GitHub Actions workflow. That path needs no local Rust or MSVC build toolchain
to install the desktop surface. The bootstrap expects the Python core CLIs
(`musubi` and `agent`) to be installed or repaired through `musubi setup`.
macOS and Linux GUI installers are intentionally not built.

Local Windows developer path, from the repository root:

```bash
npm install
npm run tauri:dev
npm run tauri:build

# Optional explicit database override:
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

`npm run tauri:dev` is for local Windows GUI development and requires the Rust
toolchain and MSVC linker. If Tauri fails with `failed to run 'cargo metadata'`
or `program not found`, `cargo` is missing from `PATH`. If Rust fails with
`link.exe not found`, the MSVC linker is missing. Run:

```powershell
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
cargo --version
where.exe link
```

Open a new terminal after installing these tools. The app reads `MUSUBI_DB`
when set, then `MUSUBI_ROOT/data/audit.db`, then the nearest workspace
`musubi/storage/audit.db`; if none is available it opens an empty in-memory
state for first-run setup.
The Settings view shows Python, `musubi`, `agent`, `.musubi/llm.json`, and the
selected audit DB path.

Icons are generated with `npm run icons`. For `.ico`/`.icns` generation, run
`npm run tauri icon src-tauri/icons/icon.png`.

The Windows Musubi installer bootstrap is produced by the
[`Desktop build`](../.github/workflows/desktop.yml) GitHub Actions workflow.
The setup-aware first-run artifact lives at
[`artifacts/gui/setup_first_run_report.html`](../artifacts/gui/setup_first_run_report.html).

## Data Source

The UI is desktop-only. `TauriSource` connects through native IPC to the Rust
core, which reads `audit.db` and emits domain snapshots. The backend contract
(SQLite schema and JSON shape) is in [`src-tauri/SCHEMA.md`](src-tauri/SCHEMA.md).

The Rust shell owns one shared driver process per project. Only an explicit
Orchestrator submission launches the standalone Python driver; the React/Tauri
shell does not call a model directly. Pipeline Studio only edits registered
recipes and never owns runtime execution.

The Rust reader and its tests are in `src-tauri/musubi-data/`:

```bash
npm run test:data
```

## Views

The sections backed by the Tauri backend:

- **Orchestrator**: the only Console execution surface. Direct mode launches a
  worker run; Pipeline mode launches an explicitly selected runnable recipe
  under the same durable conversation. The center workspace separates minimal
  runtime topology from evidence: selecting a node opens its filtered logs,
  while Conversation owns narrative summaries and artifacts. Skill badges are
  derived only from successful audited skill calls. Graph edges mean
  "summoned" and do not assert sequential or parallel execution. The active
  chat ID is the durable resume target, while a different viewed chat ID may
  expose historical chat and runtime evidence read-only. Once the shared
  driver is idle, the first follow-up to that viewed session atomically
  validates and promotes its exact ID, persists the message, and launches the
  driver. `driverStatus.chatId` is the exact process owner; only the active
  owning session may cancel a run. The serialized
  `orchestratorChatId`, `viewedOrchestratorChatId`,
  `orchestratorSessions[]`, and `driverStatus.chatId` fields are documented in
  [`src-tauri/SCHEMA.md`](src-tauri/SCHEMA.md#state-shape-rust--json--buildviewmodel).
- **Pipeline studio**: a builder-only workspace with Basics, Stages, Handoffs,
  and Validate steps. Create a pipeline, drag presets or agents into its ordered
  primary stage chain, reorder and inspect stages, configure each stage's
  `spawns` allowlist, validate, then save atomically to
  `.github/pipelines/<name>/pipeline.yaml`. Primary stages run sequentially;
  nested workers summoned in the same worker turn may run in parallel. Studio
  has no chat, Run button, process ownership, or runtime history. Execute a
  saved recipe from Orchestrator Pipeline mode or with
  `agent "<brief>" --pipeline <name>`. Exact serialized catalog and builder
  fields are documented in
  [`src-tauri/SCHEMA.md`](src-tauri/SCHEMA.md#state-shape-rust--json--buildviewmodel).
- **Policy**: fail-closed PreToolUse allow/deny stream and role tool surfaces.
- **Audit**: append-only ledger, filterable by event type.
- **Models**: LMRouter vendor profiles and active profile selection.
- **Skills**: pushed/pulled catalog with the "default to skill, not agent" rule.
- **Settings**: first-run checks for Python, CLIs, profile config, and audit DB.

A persistent trust strip surfaces the Hard Invariants: zero-LLM substrate,
fail-closed policy, append-only audit, and evaluator firewall.

## URL Options

The desktop window accepts a view selector:

- `?startView=orchestrator|pipeline|policy|audit|models|skills|settings`

## Layout

```text
src/
  App.jsx                  shell: activity bar + trust strip + view switch
  components/              ActivityBar, TrustStrip, ChatBody
  views/                   Orchestrator, Pipeline, Policy, Audit, Models, Skills, Settings
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
