# Musubi Console

> **New to the console? Start with the user guide:** [`docs/guide.md`](../docs/guide.md) § Console
> — install, the six views, and pointing it at a real `audit.db`. This file is
> the architecture + contributor reference.

A dark, technical **governance console** for Musubi (結び) — "tie agents to
policy." A React + Vite UI (recreated from the Claude Design prototype in
`../project/Musubi Console.dc.html`) packaged as a **standalone Tauri desktop
app** that reads Musubi's `audit.db` directly — no localhost server, no Copilot.

> Musubi is a governance layer for agentic software-engineering work — firewall,
> audit, validator, budget, skill injection. The agent reasons; Musubi controls
> the environment. Agents are threads; governance is the knot that binds them.

## Run

**In the browser (UI + simulation, no toolchain):**

```bash
npm install
npm run dev      # http://localhost:5173  (live simulation)
npm run build    # production build to dist/
npm run preview  # serve the production build
```

**As the desktop app (Tauri):**

```bash
npm run tauri:dev     # dev window with hot reload
npm run tauri:build   # bundled installable

# point it at a real Musubi database:
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

Requires the Rust toolchain and the platform webview libs (Linux:
`webkit2gtk-4.1` + `libgtk-3-dev`; macOS/Windows: built-in). If `tauri dev`
fails with `failed to run 'cargo metadata'` / `program not found`, `cargo` is
missing from `PATH`; on Windows run `winget install --id Rustlang.Rustup -e`,
open a new terminal, and confirm `cargo --version`. Without `MUSUBI_DB` the app
seeds an in-memory demo so it runs standalone. Icons are generated with
`npm run icons` (for `.ico`/`.icns`: `npm run tauri icon src-tauri/icons/icon.png`).

**Prebuilt installers (no local toolchain):** the
[`Desktop build`](../.github/workflows/desktop.yml) GitHub Actions workflow
compiles installers for macOS (Apple Silicon + Intel), Windows, and Linux in the
cloud — it installs Rust and the webview libs on the runners, so you never touch
them locally.
- **Manual:** Actions ▸ *Desktop build* ▸ *Run workflow* → download the
  `.dmg` / `.msi` / `.AppImage` / `.deb` from the run's artifacts.
- **Release:** push a tag (`git tag v0.1.0 && git push --tags`) → a draft GitHub
  Release with the installers attached.

> The UI loads IBM Plex Sans/Mono from Google Fonts. Offline, it falls back to
> system sans/mono — layout and colors are unaffected.

## Data sources

The UI is source-agnostic — a `DataSource` feeds the same view-model:

| source | when | data |
|---|---|---|
| `SimulationSource` | `npm run dev`, any browser | live in-browser simulation (default) |
| `TauriSource` | inside the desktop shell | native IPC → Rust core → `audit.db` |

Selection is automatic (`window.__TAURI__`), overridable with `?source=sim`
or `?source=tauri`. The backend contract (SQLite schema + JSON shape) is in
[`src-tauri/SCHEMA.md`](src-tauri/SCHEMA.md); the Rust reader and its tests are
in `src-tauri/musubi-data/` (`npm run test:data`).

## Views

Six sections, all driven by a live in-browser simulation (no backend):

- **Orchestrator** — the driver "knot" spawning governed sub-agents (explorer /
  investigator / reviewer-aux) over a woven net; each card shows its own model,
  spawn-order badge, turn cap and wall-clock budget. Click a card for its
  firewalled brief and restricted tool surface; the right panel is a driver chat.
- **Pipeline studio** — pick agents from the palette (or load a preset:
  `feature-dev`, `bugfix`, `explore`), reorder / remove them, then **Run** to
  walk the chain in order with a policy gate at each handoff. Click the driver to
  chat.
- **Policy** — fail-closed PreToolUse allow/deny stream + tool-surface-by-role,
  with the evaluator-firewall invariant (HI #3) called out.
- **Audit** — append-only ledger (spawned / completed), filterable.
- **Models** — LMRouter vendor profiles (anthropic / openai / ollama / azure)
  with a live `.musubi/llm.toml` snippet; selecting one updates the active model.
- **Skills** — pushed/pulled catalog with the "default to skill, not agent" rule.

A persistent trust strip surfaces the Hard Invariants (zero-LLM substrate,
fail-closed policy, append-only audit, evaluator firewall).

## Prototype props

The original prototype's editor props are accepted as URL query params:

- `?startView=orchestrator|pipeline|policy|audit|models|skills`
- `?simSpeed=Calm|Normal|Brisk`
- `?live=false` — start paused

## Layout

```
src/
  App.jsx                  shell: activity bar + trust strip + view switch
  components/               ActivityBar, TrustStrip, ChatBody
  views/                   Orchestrator, Pipeline, Policy, Audit, Models, Skills
  data/
    createSource.js        picks SimulationSource vs TauriSource
    TauriSource.js         native IPC source (invoke / listen / actions)
  sim/
    SimulationSource.js    in-browser simulation DataSource
    useMusubi.js           hook: owns a source, builds the view-model
    viewModel.js           pure presentation (state + actions → view-model)
    data.js                role/profile/skill/preset tables + colour lookups
    format.js              fmtClock / rhex / pick
    styleHelpers.js        roleChip / navStyle / auditBtn
    NetGraphic.jsx         woven-net SVG
  lib/
    Box.jsx                element wrapper with style + hover
    css.js                 CSS-string → React style object
src-tauri/
  src/lib.rs               Tauri commands + live audit.db poller
  musubi-data/             webkit-free Rust core: audit.db → State (+ tests)
  SCHEMA.md                backend contract (SQLite schema + JSON shape)
  tauri.conf.json          desktop window + bundle config
```

Both sources implement one contract (`state` / `actions` / `subscribe` /
`start` / `stop`) and feed the same pure `buildViewModel`. To wire a different
backend, add a source next to `TauriSource.js`.
