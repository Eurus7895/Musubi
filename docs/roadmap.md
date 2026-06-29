# Roadmap — Musubi

> What's next and what's labelled for dissolution. **Not** a history —
> that lives in git log + closed PRs + the audit DB.
> Repo rules → [`/CLAUDE.md`](../CLAUDE.md).
> Memory layer → [`docs/memory.md`](./memory.md).

---

## The discipline (cite this in PR review)

> Every PR moves Musubi either toward **thicker substrate** (queryable
> audit, more skill markdown, sharper invariants) OR toward **thinner
> ephemeral structure** (less pipeline scaffolding, fewer compensating
> preambles). PRs that add ephemeral structure without retiring an
> equivalent — or strengthening the substrate — get pushed back.

A piece is **substrate** if it still helps when the next frontier model
lands; **ephemeral** if it only compensates for a current model limit.
Substrate gets refactored; ephemera gets deleted when its limit
dissolves. Every ephemeral piece is also a **cost lever** — track both
the debt and the credits it saves, so you spot when the calculus flips.

---

## North star — the standalone single-agent pivot

The product is **deterministic, zero-LLM validation enforced at every
agent↔agent and agent↔tool boundary** — wherever the boundaries sit. The
staged pipeline was never the product; its 4-stage *shape* is one
arrangement of boundaries and is **ephemeral** (kept for now — see
Postponed). The boundary primitives are **substrate**, and would re-home
onto sub-agent + tool-call boundaries if/when the pipeline dissolves:

| Boundary primitive (substrate — keep) | Was bound to | Re-homes to |
|---|---|---|
| Evaluator firewall (HI #3) | reviewer *stage* | reviewer *sub-agent* (restricted context) |
| Validator (lint/typecheck/tests) | between stages | every sub-agent handoff + tool call (hook) |
| Schema / contract check | `musubi_write_stage` | sub-agent return + tool-call gate |
| Policy engine (HI #5) | `(pipeline, agent)` | `(agent, sub-agent role)` + tool allow-list |
| Append-only audit (HI #7/#8) | stage store | turn / conversation / sub-agent store |

**Target architecture.** A standalone, single-agent host (Claude-Code
shaped) reaching the model through one inject point — the vendor-agnostic
`LMRouter` (`agent/vendors/base.py`) — so the product is model-agnostic
and free of `vscode.lm` quota. Enabled by the HI #1 redraw (substrate
stays zero-LLM; the driver may connect to an LLM). The standalone host
and the VS Code extension **both stay supported** — the extension brings
the substrate (governance + compression) to Copilot Chat, the way tools
like headroom wrap Copilot. Two surfaces, one substrate.

What *would* die with the pipeline (postponed): the **rigid sequential
shape** and the **between-stage human gate** — both ephemeral. Everything
else re-homes.

---

## Steps

The pipeline and the single-agent host **coexist** — we are not removing
the pipeline now. Near-term work grows the standalone host and the
substrate; pipeline dissolution is postponed (see below).

1. ✓ **`tools/fs.py` MCP tools.** File/command tools are wired through the substrate.
2. ✓ **Reversible compression core.** Deterministic blob-store compression plus `musubi_retrieve` landed.
3. ✓ **Compression on input returns.** Tool/stage/conversation outputs compress by default, remain reversible, and expose stats.
4. ◐ **Finish single-agent host parity.** Model-agnostic vendors landed:
   `anthropic`/`openai`/`ollama`/`azure`-on-prem (curl transport) and the
   `genai_farm` on-prem gateway (SDK by default, curl fallback for an
   authenticated proxy / custom CA / mTLS) selected by `.musubi/llm.json`
   family profiles (`agent/config.py` + `agent/vendors/`). External-MCP
   federation landed (`agent/mcp_gateway.py`): the standalone host reads an
   `mcp.json` (the standard `mcpServers` schema — Claude Desktop / Cursor /
   VS Code configs paste in unchanged), connects any number of other MCP
   servers (stdio or streamable-HTTP), and splices their tools into the
   catalog under a `<server>__<tool>` namespace, routing each call back to
   its owner.
   Fail-open per server (a bad entry is logged and skipped); top-level
   agent only (sub-agents stay Musubi-tool-scoped); the tools are **not**
   firewalled/audited by Musubi — a driver-side convenience, not a
   substrate control.
   Driver-side context controls landed (`agent/context.py` +
   `agent/vendors/anthropic_router.py`): deterministic, zero-LLM
   counterparts of Headroom's verbosity steering (terse system prompt),
   CacheAligner (Anthropic `cache_control` on the static system+tools
   prefix, `MUSUBI_PROMPT_CACHE=0` opts out; OpenAI-compatible vendors
   surface provider-native cached-token telemetry through the same cycle-log
   keys), effort routing (low per-cycle `max_tokens` floor that escalates
   only on truncation, `MUSUBI_EFFORT_TOKENS`), and IntelligentContext
   (`fit_context` runs a budgeted packing pass when the convo exceeds
   `MUSUBI_CONTEXT_BUDGET`: protect system + first task + recent turns,
   compress old tool outputs first, then trim only if needed while
   preserving tool pairing + `musubi_retrieve` markers — the learned
   compaction stays out to honour HI #1).
   Remaining: port `BudgetEnforcer` into `agent/run.py`; multi-turn CLI +
   conversation persistence.
5. ◐ **Control at the boundary.** Sub-agent orchestrator landed
   (`agent/subagent.py`) and has since unified into the **worker model**
   (see "Worker model — landed" below): one `run_unit` path, parallel +
   depth-2 workers, agent-summoned and user-defined pipelines, all on
   firewalled briefs + restricted tool surfaces with verified summaries and
   audited spawns. Remaining: `PreToolUse` (policy/firewall) + `PostToolUse`
   (audit) on every standalone tool call; surface cost/credits in the CLI.
6. **Fix the VS Code extension for the rename.** The extension is a
   **supported** Copilot surface (it brings the substrate — governance +
   compression — to Copilot Chat). Update its hardcoded `harness_*` tool
   calls to `musubi_*` so it works against the renamed server. The 4-stage
   pipeline lives here and stays.

### Worker model — landed (standalone host)

The standalone host no longer distinguishes "main agent" from "sub-agent":
there are only **workers**, the root task being the depth-0 worker. The
core purpose is **context-window offloading** — a worker does bounded work
in its own firewalled context and returns only a compact summary, keeping
the orchestrator lean.

- ✓ **One code path.** Root and spawned workers both run through `agent/run.py::run_unit`.
- ✓ **Frontmatter spawn firewall.** Role `spawn_allowlist:` metadata is authoritative, with fail-closed fallback.
- ✓ **Parallel + background workers.** Turn spawns run concurrently with width guards and depth-2 nesting.
- ✓ **Agent summons a pipeline.** Pipelines compose ordered workers while preserving evaluator firewall boundaries.
- ✓ **User-defined pipelines from presets.** Preset YAML blocks compose user pipelines such as `dev-lite`.

This advances the north star: the sub-agent split and the 4-stage shape
were the ephemera; the worker model is where they re-home. The TS
extension's 4-stage pipeline is untouched (still feature-frozen, step 6).

### Token economics steps (Headroom-inspired, native Musubi)

Scope: learn the algorithms and architecture, not the runtime. Musubi
does **not** add Headroom as a dependency or proxy, and HI #1 still holds:
the substrate remains deterministic, pure Python, and zero-LLM.

1. **[done] Baseline CCR compression.** Reversible blob store, default-on file/command compression, `musubi_compress`, and stats landed.
2. **[done] Complete compression coverage.** Stage and conversation reads now compress within existing permission boundaries.
3. **[done] Smarter native compressors.** JSON, Python, log, and text compressors now use deterministic structural summaries.
4. **[done] LM-boundary context controls.** Terse prompting, cache controls, effort routing, telemetry, and `fit_context` packing landed.
5. **[planned] Cache hardening, output steering, and compression eval.**
   Build on the landed prompt-cache controls by hardening stable prompt
   prefixes and tool ordering, tighten tool-result formats, keep low
   default `max_tokens` with truncation-based escalation, and add a
   compression/context eval gate that measures savings, retrieve
   correctness, retrieve-call frequency, and task-quality regression. This
   is separate from the postponed pipeline-parity eval suite below.

Default profile stays conservative. Aggressive compression must be
opt-in until the compression/context eval gate shows no meaningful
quality regression.

### Postponed (the pipeline stays for now)

The staged pipeline is **not** being removed yet. Two items are deferred
until we choose to revisit dissolution:

- **Eval suite (the parity gate).** `.harness/evals/` running tasks
  through *both* the pipeline and the single-agent host. Deferred — it
  only earns its keep when we're ready to dissolve the pipeline.
  Compression is now **on by default** (reversible via `musubi_retrieve`);
  set `MUSUBI_COMPRESS=0` to opt out per workspace. The eval suite would
  still quantify its token savings and confirm no quality regression.
- **Dissolve the 4-stage pipeline shape.** Collapse `pipeline.ts`
  runners, manifest contract, the staged fanout; re-home the boundary
  primitives onto sub-agent + tool-call boundaries; rewrite HI #2/#3/#7.
  Deferred — gated on the eval suite above. **The extension itself is
  kept** (it stays a supported Copilot surface); only the staged-pipeline
  *shape* inside it would dissolve.

**Parity** (the single-agent host doing everything the pipeline does at
equal-or-better cost) remains the line that *would* license dissolution —
when we choose to revisit it.

---

## Eval suite (postponed) — detail

The keystone *when* we revisit pipeline dissolution. Standing evaluation
is what would license it. Postponed while the pipeline stays.

- `.harness/evals/` — 5 representative tasks (bootstrap-mcp-tool,
  cross-cutting-rename, test-existing-helper, new-skill,
  docs-from-reference).
- Dual-mode runner: mocked default + `--real-lm`. The mocked fixtures
  are **captured from real LM sessions**, never hand-authored — hand
  authoring silently encodes the wrong behaviour.
- Captures per task: pass/fail, cycles, lm_ms, credits, sub-agent spawns.
- Run on every model release — a model that dissolves an ephemeral guard
  shows up as a metric drop here.

---

## Operator console (`gui/`) — substrate

A dark, governance-focused UI for Musubi — **zero LLM calls**, reads
`audit.db` directly (Tauri IPC → Rust core → SQLite). Ships as a Windows
Musubi installer bootstrap via the `desktop.yml` CI workflow: desktop GUI plus
runtime checks for the Python `musubi` / `agent` CLIs.

The console is **not** the backend. The portable backend remains the Python
`musubi/` package so a user can carry it into any project, run `musubi setup`,
and use `agent` without the desktop app. The GUI is an operator shell: it may
discover, configure, and launch backend commands, but all governed work still
flows through the Musubi core.

| surface | tier | notes |
|---|---|---|
| `gui/` (React + Vite + Tauri) | **substrate** | operator view of the governance layer |
| `gui/src-tauri/musubi-data/` | substrate | webkit-free Rust core; reads `audit.db` |
| `.github/workflows/desktop.yml` | substrate | builds the Windows installer bootstrap |

**Views:** Orchestrator (sub-agent cohort), Pipeline studio, Policy
(PreToolUse stream), Audit (append-only ledger), Models (LMRouter profiles),
Skills (pushed/pulled catalog). The console is Tauri-only and uses
`TauriSource` (native IPC).

**To run:**
```bash
musubi setup                              # points to the Windows installer bootstrap
# Optional local Windows GUI development:
npm install
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

### GUI implementation steps

1. **[done] Windows installer bootstrap.** CI builds the Windows desktop installer artifact and labels real vs demo data.
2. **[done] Setup-aware first run.** Settings shows Python/CLI/profile/audit-DB discovery and links the static artifact.
3. **[next] On-demand task launcher.** Add a Tauri command that launches
   one governed `agent "<task>"` process only when the user presses Run. The
   GUI passes the selected project root, profile, and audit DB path through the
   child process environment, streams stdout/stderr into the operator view, and
   supports cancellation. There is no always-on background daemon in this
   slice; idle GUI means no running agent process.
4. **[planned] Installer runtime reduction.** Prefer a bundled or locally
   repairable Python core payload so first run does not depend on a global
   `pip install` or manual `PATH` edits. Keep network install as a fallback
   for development builds.
5. **[planned] Signing and release hardening.** Sign the Windows installer and
   document the expected Defender / SmartScreen path so the primary install
   route is suitable for non-developer machines.

User guide → [`docs/guide.md`](./guide.md) § Console.

---

## Still-live substrate work (independent of the phases)

- **Skill catalog growth** — skills are the cheapest optimisation
  surface. Distil more from real sessions (`musubi_distill_session`);
  each new skill ships with an `applies-to` tag.
- **Per-cycle audit (`agent_cycles`)** — one row per `sendRequest`; the
  data that makes dissolution decisions empirical rather than guessed.
- **Lines-of-substrate vs lines-of-skill ratio** — track over time; the
  goal is the ratio improves even as features grow.
- **Tool-catalog surface for the standalone agent (deferred cost-lever).**
  The standalone host hands the model **all** `musubi_*` tools every call
  (`agent/run.py` `register_local`), but ~20 are pipeline/audit machinery the
  harness code calls, not tools the model should invoke. Sub-agents are
  already filtered (`agent/subagent.py`); the top-level agent is not. A
  driver-side allowlist would cut per-call tool-schema tokens with no HI #1
  impact (the substrate keeps all tools registered). Observed, not yet acted
  on — tracked so the lever isn't forgotten.
- **Relocate substrate out of `.github/` (coordinated, later).** The
  skill catalog, 3-tier memory, agent catalog, and pipeline defs live
  under `.github/` only as a Copilot-extension artifact — substrate is
  meant to be platform-neutral, yet it sits in a GitHub/Copilot-specific
  dir whose paths are hardcoded across `server.py`, `composer.py`,
  `session_distiller.py`, `pattern_detector.py`, and tests. Only
  `.github/workflows/` (CI, incl. the HI #9 tier gate) and the Copilot
  surface (`commands/`, `instructions/`) genuinely belong there. When the
  standalone host is split from the extension, move
  `skills/memory/agents/pipelines` to a neutral root (e.g. `.musubi/`) and
  leave `.github/` for GitHub + Copilot. A ~20-path cross-cutting rename —
  the extension still reads `.github/`, so it must be coordinated, not a
  drive-by. Not blocking; tracked so it isn't forgotten.

---
