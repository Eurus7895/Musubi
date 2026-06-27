# Musubi Console — user guide

> A dark, operator-facing **governance console** for Musubi (結び). It shows
> the substrate at work — the sub-agent cohort, policy decisions, and the
> append-only audit ledger — by reading `audit.db` directly. **Zero LLM calls,
> no localhost server, no Copilot.** The agent reasons; the console only
> *observes and operates* the governance layer.
>
> Lives in [`app/`](../app). Architecture + backend contract:
> [`app/README.md`](../app/README.md) · [`app/src-tauri/SCHEMA.md`](../app/src-tauri/SCHEMA.md).

---

## What it is

| | |
|---|---|
| **Stack** | React + Vite (UI) · Tauri (desktop shell) · a webkit-free Rust core that reads SQLite |
| **Reads** | Musubi's `storage/audit.db` (append-only). Never writes to the audit tables. |
| **Tier** | **substrate** — an operator view of the governance layer (see `docs/roadmap.md`) |
| **Two ways to run** | a browser simulation (no toolchain) or a native desktop app (real DB) |

The only writes the console performs are GUI-side: the driver chat (`chat_log`)
and the active model profile (`meta`). Governed mutations — spawning agents,
running pipelines — still go through the MCP server, never a direct DB write.

---

## Quick start

### Option A — browser (UI + live simulation, no toolchain)

The fastest way to see the console. No Rust, no database — an in-browser
simulation feeds the same view-model the desktop app uses.

```bash
cd app
npm install
npm run dev        # → http://localhost:5173  (live simulation)
```

`npm run build` produces a static bundle in `dist/`; `npm run preview` serves it.

### Option B — desktop app (Tauri, reads a real `audit.db`)

```bash
cd app
npm install

# standalone, with seeded demo data:
npm run tauri:dev

# point it at a real Musubi database:
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

Without `MUSUBI_DB` the app seeds an in-memory demo so it runs standalone.
Requires the Rust toolchain plus the platform webview libs (Linux:
`webkit2gtk-4.1` + `libgtk-3-dev`; macOS/Windows: built-in).

### Option C — prebuilt installer (no local toolchain)

The [`Desktop build`](../.github/workflows/desktop.yml) GitHub Actions workflow
compiles installers for macOS (Apple Silicon + Intel), Windows, and Linux in the
cloud:

- **Manual:** Actions ▸ *Desktop build* ▸ *Run workflow* → download the
  `.dmg` / `.msi` / `.AppImage` / `.deb` from the run's artifacts.
- **Release:** push a tag (`git tag v0.1.0 && git push --tags`) → a draft GitHub
  Release with the installers attached.

---

## The six views

A persistent **trust strip** across the top surfaces the Hard Invariants
(zero-LLM substrate, fail-closed policy, append-only audit, evaluator firewall)
and the active model. Switch views from the activity bar on the left.

| View | What it shows | Backed by |
|---|---|---|
| **Orchestrator** | The driver "knot" spawning governed sub-agents (explorer / investigator / reviewer-aux) over a woven net. Each card shows its model, spawn-order badge, turn cap, and wall-clock budget. Click a card for its firewalled brief and restricted tool surface; the right panel is a driver chat. | `subagent_audit` folded per handle |
| **Pipeline studio** | Author a chain: pick agents from the palette (or load a preset — `feature-dev`, `bugfix`, `explore`), reorder / remove, then **Run** to walk the chain with a policy gate at each handoff. | authoring surface (not the DB) |
| **Policy** | Fail-closed PreToolUse allow/deny stream + tool-surface-by-role, with the evaluator-firewall invariant (HI #3) called out. | `policy_audit` |
| **Audit** | The append-only ledger (spawned / completed), filterable. | `subagent_audit` (newest first) |
| **Models** | LMRouter vendor profiles (anthropic / openai / ollama / azure) with a live `.musubi/llm.toml` snippet; selecting one updates the active model. | `meta.active_profile` + static defs |
| **Skills** | The pushed / pulled skill catalog, with the "default to skill, not agent" rule. | static catalog |

---

## Pointing at your real governance data

The console renders whatever `audit.db` you hand it. To watch a live Musubi
session:

```bash
MUSUBI_DB=/abs/path/to/storage/audit.db npm run tauri:dev
```

A background poller refreshes the UI ~1×/second as the database grows — spawn a
sub-agent or run a pipeline through the MCP server and the cohort, policy
stream, and audit ledger update in place. A fresh DB with empty tables yields
empty surfaces (the reader is tolerant); missing optional columns fall back to
defaults.

The exact schema the reader expects (column names matter; extra columns are
ignored) is the backend contract in
[`app/src-tauri/SCHEMA.md`](../app/src-tauri/SCHEMA.md).

---

## Data sources & URL options

The UI is source-agnostic — one `DataSource` contract feeds the same
view-model. Selection is automatic, overridable by query param:

| source | when | override |
|---|---|---|
| `SimulationSource` | `npm run dev`, any browser | `?source=sim` |
| `TauriSource` | inside the desktop shell | `?source=tauri` |

The original prototype's editor props are accepted as URL query params (handy in
the browser):

- `?startView=orchestrator|pipeline|policy|audit|models|skills`
- `?simSpeed=Calm|Normal|Brisk`
- `?live=false` — start paused

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Fonts look like system sans/mono | The UI loads IBM Plex from Google Fonts; offline it falls back to system fonts. Layout and colours are unaffected. |
| `npm run tauri:dev` fails on Linux with a webkit error | Install the webview libs: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`. |
| App opens but shows demo data | `MUSUBI_DB` is unset or empty — it seeded the in-memory demo. Set it to your `audit.db` absolute path. |
| Want installers without a local toolchain | Use **Option C** above — the cloud workflow builds them for every platform. |

---

## How it's tested

The Rust core (`app/src-tauri/musubi-data`) is webkit-free, so its
`audit.db` → state reader is unit-tested headlessly on every PR by the `rust`
job in [`.github/workflows/ci.yaml`](../.github/workflows/ci.yaml)
(`cargo fmt --check` + `clippy -D warnings` + `cargo test`). Run it locally with
`npm run test:data` from `app/`.
