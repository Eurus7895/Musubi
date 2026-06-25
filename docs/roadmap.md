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
   json-minify + code/blank-strip + content-hash store) and the
   `musubi_retrieve` tool. Zero-LLM, deterministic, pure Python.
3. ◐ **Wire compression into input returns.** `musubi_read_file` /
   `musubi_run_command` done and **on by default** (`MUSUBI_COMPRESS=0`
   opts out per session/workspace); reversible via `musubi_retrieve`, so
   default-on is safe. `musubi_read_stage` (after firewall) +
   `musubi_get_conversation` + `stage_metrics` ratio recording remain.
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
   Remaining: port `BudgetEnforcer` + compaction into `agent/run.py`;
   multi-turn CLI + conversation persistence.
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

## Still-live substrate work (independent of the phases)

- **Skill catalog growth** — skills are the cheapest optimisation
  surface. Distil more from real sessions (`musubi_distill_session`);
  each new skill ships with an `applies-to` tag.
- **Per-cycle audit (`agent_cycles`)** — one row per `sendRequest`; the
  data that makes dissolution decisions empirical rather than guessed.
- **Lines-of-substrate vs lines-of-skill ratio** — track over time; the
  goal is the ratio improves even as features grow.
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