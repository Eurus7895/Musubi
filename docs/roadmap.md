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

1. ✓ **`tools/fs.py` MCP tools** (`musubi_read_file/write_file/edit_file/
   run_command`) — already wired; the file/command tool results flow
   through the substrate (the biggest token sink).
2. ✓ **Reversible compression core.** `musubi/compression/` (router +
   native compressors + content-hash store) and the
   `musubi_retrieve` tool. Zero-LLM, deterministic, pure Python.
3. ✓ **Wire compression into input returns.** `musubi_read_file` /
   `musubi_run_command` done and **on by default** (`MUSUBI_COMPRESS=0`
   opts out per session/workspace); reversible via `musubi_retrieve`, so
   default-on is safe. On-demand `musubi_compress` and efficiency
   measurement landed: the blob store records each payload's
   `original_chars`/`compressed_chars`, and `musubi_compression_stats`
   aggregates the overall ratio, bytes saved, and a per-kind breakdown.
   `musubi_read_stage` compresses permitted data after the evaluator
   firewall, and `musubi_get_conversation` compresses message content with
   per-message retrieve metadata. Native deterministic compressor upgrades
   have landed for JSON shape summaries, Python structure summaries, log
   pattern grouping, and heading-aware text outlines, learning from
   Headroom's token-economics architecture without importing Headroom or
   adding any substrate-side LLM call.
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
   only on truncation, `MUSUBI_EFFORT_TOKENS`), and IntelligentContext (`fit_context` elides
   the oldest/largest tool results when the convo exceeds
   `MUSUBI_CONTEXT_BUDGET`, preserving tool pairing + `musubi_retrieve`
   markers — the learned compaction stays out to honour HI #1).
   Remaining: port `BudgetEnforcer` into `agent/run.py`; multi-turn CLI +
   conversation persistence.
5. ◐ **Control at the boundary.** Sub-agent orchestrator landed
   (`agent/subagent.py`): the standalone agent runs spawned roles to
   completion on a firewalled brief + restricted tool surface, summary
   verified on `musubi_complete_subagent`, spawn audited. Remaining:
   `PreToolUse` (policy/firewall) + `PostToolUse` (audit) on every standalone
   tool call; surface cost/credits in the CLI.
6. **Fix the VS Code extension for the rename.** The extension is a
   **supported** Copilot surface (it brings the substrate — governance +
   compression — to Copilot Chat). Update its hardcoded `harness_*` tool
   calls to `musubi_*` so it works against the renamed server. The 4-stage
   pipeline lives here and stays.

### Token economics steps (Headroom-inspired, native Musubi)

Scope: learn the algorithms and architecture, not the runtime. Musubi
does **not** add Headroom as a dependency or proxy, and HI #1 still holds:
the substrate remains deterministic, pure Python, and zero-LLM.

1. **[done] Baseline CCR compression.** Reversible blob store,
   `musubi_retrieve`, deterministic JSON/code/log/text baseline
   compressors, default-on `musubi_read_file` / `musubi_run_command`
   wiring, on-demand `musubi_compress`, and `musubi_compression_stats`
   are landed.
2. **[done] Complete compression coverage.** Added compression to
   `musubi_read_stage` after the evaluator firewall and to
   `musubi_get_conversation`, preserving permission boundaries,
   tool-call pairing, and retrieve markers.
3. **[done] Smarter native compressors.** Replaced the minimal
   compressors with native, deterministic strategies: JSON smart-crush
   (schema/counts/samples/path stats), structural code compression
   (Python AST first; conservative fallback for other languages), log
   pattern grouping (normalized patterns + first/last examples), and
   heading-aware text outline compression. `musubi_retrieve` remains the
   source of truth, and the router skips any output that does not shrink
   after marker overhead.
4. **[in progress] LM-boundary context controls.** Terse prompting,
   effort-token routing, Anthropic prompt-cache controls,
   provider-native cached-token telemetry, and a first `fit_context`
   pass have landed. Next: extend `agent/context.py::fit_context` from
   oldest/largest elision to a budgeted packing pass that preserves
   system/tools/current task, keeps tool-call pairing intact, compresses
   old tool outputs before dropping them, and retains retrieve markers
   for any lossy view.
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
`audit.db` directly (Tauri IPC → Rust core → SQLite). Ships as a standalone
desktop app (macOS/Linux/Windows via the `desktop.yml` CI workflow).

| surface | tier | notes |
|---|---|---|
| `gui/` (React + Vite + Tauri) | **substrate** | operator view of the governance layer |
| `gui/src-tauri/musubi-data/` | substrate | webkit-free Rust core; reads `audit.db` |
| `.github/workflows/desktop.yml` | substrate | builds installers for all platforms |

**Views:** Orchestrator (sub-agent cohort), Pipeline studio, Policy
(PreToolUse stream), Audit (append-only ledger), Models (LMRouter profiles),
Skills (pushed/pulled catalog). The console is Tauri-only and uses
`TauriSource` (native IPC).

**To run:**
```bash
musubi setup                              # points to the prebuilt GUI installer
# Optional local GUI development:
npm install
MUSUBI_DB=/path/to/storage/audit.db npm run tauri:dev
```

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
