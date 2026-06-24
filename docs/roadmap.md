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
   `musubi_run_command` done (behind `MUSUBI_COMPRESS`, default OFF);
   `musubi_read_stage` (after firewall) + `musubi_get_conversation` +
   `stage_metrics` ratio recording remain.
4. **Finish single-agent host parity.** Port `BudgetEnforcer` +
   compaction into `agent/run.py`; multi-turn CLI + conversation
   persistence.
5. **Control at the boundary.** `PreToolUse` (policy/firewall) +
   `PostToolUse` (audit) on every tool call; firewalled reviewer
   sub-agent; surface cost/credits in the CLI.
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
  only earns its keep when we're ready to dissolve the pipeline. Until
  then compression stays **opt-in** (`MUSUBI_COMPRESS`, default OFF);
  enable it per workspace when you want the savings.
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

---

## Dissolution candidates (retire when the pipeline is dissolved — postponed)

Each is `musubi-tier: ephemeral` with an `expires-when:` source tag. The
single-agent host + eval suite is the trigger for the whole set.

| Component | Cost-lever today |
|---|---|
| 4-stage pipeline shape | ~30 credits/run avoided vs unconstrained one-shot |
| Sub-agent-for-exploration split | ~75% exploration cost cut (~15 credits/session) |
| Correction loop (`runAgentWithValidationRetry`) | 1× stage cost per retry avoided |
| Cycle-loop guards (bail-out / salvage) | ~0.3 credits/session |
| Preamble blocks (path-rules / empty-project / workspace-root) | ~8 credits/avoided-stuck-stage |
| `materializeCoderFiles` + JSON manifest | deterministic disk writes (retired in Step 1) |
| `preSpawnAndSplice` fanout | ~3-10 credits/stage |
| `runStageReviewGate` 4-button UX | human-in-the-loop value (not a cost lever) |

**Reading rule:** when cost-lever falls below maintenance cost, delete —
even if the expiration trigger hasn't fully fired.

---

## Rename status (done — substrate only)

CopilotHarness → **Musubi**. Substrate, driver, docs, scripts, CI, and
`.github/` were renamed to `Musubi` / `musubi` / `musubi_*` /
`musubi-tier`, including the breaking `harness_* → musubi_*` MCP-prefix
change (the standalone CLI takes tools dynamically, so it is
prefix-agnostic). The VS Code extension was **not renamed in this pass**,
so the server-side prefix change currently breaks its hardcoded
`harness_*` calls — Step 6 fixes that (the extension is kept, not
abandoned). GitHub repo renamed in place (history / issues preserved).

---

## How we stay aligned as models evolve

- **Eval suite on every model release** (postponed) — the keystone signal once pipeline dissolution is back on the table.
- **Watch the audit data** — falling cycle counts / spawn counts /
  preamble fire-rates flag a guard that stopped earning its keep.
- **Quarterly delete-pass** — walk the ephemeral set, apply the
  1-hour-vs-1-week question, write a short net-delta memo.
- **Thin user contract** — keep the entry command stable so internal
  dissolution never surfaces to the user.
