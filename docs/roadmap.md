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
arrangement of boundaries and is **ephemeral**. The boundary primitives
are **substrate**, and re-home onto sub-agent + tool-call boundaries:

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
and free of `vscode.lm` quota. The VS Code extension host is abandoned.
Enabled by the HI #1 redraw (substrate stays zero-LLM; the driver may
connect to an LLM).

What dies with the pipeline: the **rigid sequential shape** and the
**between-stage human gate** — both ephemeral. Everything else re-homes.

---

## Steps (parity-gated — never dissolve speculatively)

Execution order. Token-compression (substrate, deterministic, reversible)
is folded in as Steps 1–3 + 6.

1. **Wire `tools/fs.py` as MCP tools + `cache_align`.** Retires
   `materializeCoderFiles`; the file/command tool results now flow
   through the substrate (the biggest token sink). Ship the zero-loss
   KV-cache prefix-alignment win.
2. **Deterministic reversible compression core.** Build
   `musubi/compression/` (router + json-dedup + code/AST-trim + store)
   and the `musubi_retrieve` tool (compress aggressively, model pulls the
   original on demand — CCR). Zero-LLM, pure Python.
3. **Wire compression into input returns.** `fs.py` tool results, then
   `musubi_read_stage` (after the firewall) and `musubi_get_conversation`.
   Behind a config flag, default OFF. Record compression-ratio into
   `stage_metrics`.
4. **Finish single-agent host parity.** Port `BudgetEnforcer` +
   compaction into `agent/run.py`; multi-turn CLI + conversation
   persistence.
5. **Control at the boundary.** `PreToolUse` (policy/firewall) +
   `PostToolUse` (audit) fire on every tool call; firewalled reviewer
   sub-agent; surface cost/credits in the CLI.
6. **Eval suite + default-on gate.** Build `.harness/evals/`; run the 5
   tasks through *both* the staged pipeline and the single-agent host;
   compare quality + credits. Only after no regression, flip compression
   (and other ephemeral guards) defaults on. **This is the gate.**
7. **Dissolve + cut VS Code.** Delete the 4-stage pipeline, `pipeline.ts`
   runners, manifest contract, the extension; rewrite HI #2/#3/#6/#7 for
   the boundary world. **Gated on Step 6.**

**Parity** = the single-agent host does everything the staged pipeline
does at equal-or-better quality/cost, proven by Step 6. Only then does
Step 7 run. Parity is the line "safe to delete the old path".

---

## Step 6 detail — the eval suite (the keystone)

Standing evaluation is what licenses every dissolution. Without it,
"the single agent is as good as the pipeline" is vibes.

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

## Dissolution candidates (all retire in Step 7)

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
prefix-agnostic). The VS Code extension was **deliberately not renamed** —
Step 7 deletes it, and the server-side prefix change already breaks its
hardcoded `harness_*` calls (acceptable; it is abandoned). GitHub repo
renamed in place (history / issues preserved).

---

## How we stay aligned as models evolve

- **Eval suite on every model release** (Step 6) — the keystone signal.
- **Watch the audit data** — falling cycle counts / spawn counts /
  preamble fire-rates flag a guard that stopped earning its keep.
- **Quarterly delete-pass** — walk the ephemeral set, apply the
  1-hour-vs-1-week question, write a short net-delta memo.
- **Thin user contract** — keep the entry command stable so internal
  dissolution never surfaces to the user.
