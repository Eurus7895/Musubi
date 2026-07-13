# Per-Worker Effort Ceiling & Output-Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the effort-routing floor from *guaranteeing* truncation for
workers whose job is emitting files, and give every worker an explicit,
per-role output-token budget clamped to the model's real limit — so a
one-shot artifact no longer dies at a hardcoded 4096-token ceiling.

**Motivation (observed run):** `agent "create a dashboard about vietnam"`
(deepseek-v4-flash, `single_coder` route) burned ~30.36 credits and 124k of a
200k token budget to produce a *partial* 4.8 KB file plus a "want me to try
again?" message. The causal chain, from the run log:

1. `_call_with_effort` (`agent/run.py:1286`) opens every cycle at
   `DEFAULT_EFFORT_FLOOR = 2048` (`agent/context.py:74`). The coder's first
   `musubi_write_file` naturally needs 5-6k output tokens, so it truncated
   (`stop_reason=max_tokens`) with probability 1.0 — logged as `attempts=2` on
   coder cycle 0.
2. The single retry escalates to `EFFORT_CEILING = 4096` (`agent/run.py:83`) but
   re-sends the *entire input* (double-billed) and, being still < 5-6k, likely
   truncated again — the retry dispatched `write_file(content="")`, producing an
   empty file.
3. Cleanup of that empty file consumed coder cycles 1-6 (read → retrieve →
   edit → read → `rm` (exit 1, Windows) → `del`), ~7.2 credits of no-op.
4. The floor has no cross-cycle memory, so it re-bet and re-lost at cycles 2
   and 3 (both `attempts=2`).
5. The coder exhausted `max_turns=10` mid-file; the root's continuation spawn
   was refused by `run worker cap (1) reached` (`agent/run.py:1589`); the run
   ended incomplete.

This plan removes the root cause (step 1-2, 4) and the recovery dead-end
(step 5). It is the effort-economics follow-up to
[`2026-07-09-gui-cli-orchestrator-tokens.md`](./2026-07-09-gui-cli-orchestrator-tokens.md),
which covered empty-write-guarding and same-worker chunked retry at the
tool-result layer but did not touch the `max_tokens` floor/ceiling sizing that
*causes* the truncation in the first place.

**Design assumption being corrected:** the floor's docstring states *"Most
cycles emit a small tool_use block, so the floor cap costs nothing they
needed"* (`agent/run.py:1293-1295`). True by distribution, false by role: a
`coder` on `simple_artifact` emits a whole file on its first mutate cycle. The
fix keys off the worker's *actual* tool surface, not a distributional guess.

**Architecture:** Three layers, all deterministic, zero LLM calls (HI #1):

- **Floor keyed to tool surface.** A worker whose runtime `tools` intersect
  `ORDER_SENSITIVE_FILE_TOOLS` (`agent/run.py:99-103` — the concrete
  `musubi_write_file` / `musubi_append_file` / `musubi_edit_file` set) starts at
  its ceiling, not the floor. Read-only workers and the root keep 2048.
- **Per-worker output budget.** Each worker `.agent.md` declares
  `maxOutputTokens:` in frontmatter (sibling to the existing `maxTurns:`),
  resolved at spawn in `run_subagent` where `agent_md` is already parsed
  (`agent/subagent.py:108`). This is the *intent* ceiling.
- **Optional per-model override (operator intent, NOT a discovered physical
  limit).** `.musubi/llm.json` MAY carry an optional `max_output_tokens` per
  model (resolved via `agent/config.py`) for the two cases that earn it:
  (a) deliberately capping an expensive model *lower* (Opus-tier output is real
  money per token), (b) raising for a model the operator *knows* supports large
  artifacts. This is operator intent — absent by default, never a required
  lookup, so it does not drift. When set, it clamps the resolved ceiling down.

**Why NOT a required per-model physical-limit table.** The model's true output
ceiling is only cleanly discoverable for one of Musubi's vendors — Anthropic
(`client.models.retrieve(id).max_tokens`, a real Models-API field, not an LLM
call). For OpenAI/DeepSeek it is doc-only (transcription drift); for
`ollama`/on-prem it *does not exist* as a model property at all — output is
bounded by the operator's local `num_predict`/context config. And where it IS
discoverable (Anthropic), the values are uniform and far above any brake need:
128K for the current frontier (Fable 5, Opus 4.8/4.7/4.6, Sonnet 5/4.6), 64K
for Haiku 4.5 — per-model precision buys nothing for a runaway brake. So the
ceiling is sized as a safety brake from universal tier defaults, not from a
per-model max table; the vendor enforces the real hard limit at call time (a
request above the model's cap errors/clamps — authoritative, surfaced when it
matters, nothing to pre-tabulate). This keeps model knowledge out of the
substrate (HI #1) — the router/vendor owns it.

**Resolution order (highest priority first):**
1. Worker `.agent.md` `maxOutputTokens:` (explicit per-role intent).
2. Tool-surface tier default — mutate `8192`, read-only/root `4096`.
3. Optional per-model `max_output_tokens` from `.musubi/llm.json`, IF the
   operator set one, clamps the result down. Absent → no clamp; the vendor
   rejects an over-cap request at call time.

**Tech Stack:** Python 3, existing Musubi agent loop
(`agent/run.py`, `agent/context.py`, `agent/subagent.py`), `agent/config.py`,
worker frontmatter under `.github/agents/workers/`, pytest.

## Global Constraints

- Zero LLM calls in the substrate (HI #1). Floor/ceiling selection is pure
  function of `tools`, frontmatter, and config — never a model call.
- Do not change token-budget accounting (`TokenBudgetEnforcer`), provider
  pricing, or the `_check_budget_preflight` semantics.
- `EFFORT_CEILING` stays a real per-call brake — the only enforcement that
  operates *inside* a single LM call (see Task 2 rationale). Never remove it;
  only make it resolvable per model / per worker.
- Preserve `_call_with_effort`'s single-retry escalation contract for the
  read-only path (floor → ceiling on `max_tokens`).
- Fail closed: an unknown worker, a missing frontmatter field, or a missing
  per-model config entry resolves to the conservative tier default, never to
  "unlimited".
- Keep `.vscode/mcp.json` unstaged.

---

## File Structure

- Modify `musubi/agent/run.py`: thread an explicit `floor`/`ceiling` into
  `_call_with_effort`; compute per-worker effort bounds once at `_run_loop`
  entry from `tools` + resolved config; add sticky escalation.
- Modify `musubi/agent/context.py`: replace the hardcoded `EFFORT_CEILING`
  usage path with a resolver that accepts per-worker / per-model inputs; keep
  `DEFAULT_EFFORT_FLOOR` for the read-only path.
- Modify `musubi/agent/subagent.py`: parse `maxOutputTokens` from `agent_md`
  frontmatter and pass it into `run_unit` → `_run_loop`.
- Modify `musubi/agent/config.py`: resolve the OPTIONAL per-model
  `max_output_tokens` from `.musubi/llm.json` — returns `None` when the operator
  did not set one (no clamp), not a guessed physical limit.
- Modify `musubi/agent/run.py::_file_tool_argument_error` (`~1831`): reject an
  empty/whitespace-only `content` on `musubi_write_file` / `musubi_append_file`
  with a regenerate hint (NOT on `edit_file`, where empty `new_string` is a
  valid deletion).
- Modify `musubi/agent/context.py::_elided_tool_arg_stub` (`~199`): add an
  imperative "regenerate; do not copy this marker" clause to the stub text.
- Modify `.github/agents/workers/coder.agent.md` (and other mutate workers):
  add `maxOutputTokens:` frontmatter; the read-only workers may set a small
  value or omit it.
- Modify `musubi/tests/test_agent_loop.py`, `musubi/tests/test_context.py`:
  unit + integration coverage.
- Modify `docs/roadmap.md`: link this plan from the Backlog.

---

### Task 1: Per-Worker + Per-Model Effort Resolver

**Files:**
- Modify: `musubi/agent/config.py`
- Modify: `musubi/agent/context.py`

**Interfaces:**
- Produces: `resolve_model_output_override(model_family: str, cfg) -> int | None`
  in `config.py` — the OPTIONAL operator-set `max_output_tokens`, or `None` when
  absent (no clamp).
- Produces: `resolve_effort_bounds(*, can_mutate: bool, worker_max_output: int | None, model_output_override: int | None) -> tuple[int, int]` in `context.py`,
  returning `(floor, ceiling)` per the resolution order above.

- [ ] **Step 1: Read the optional per-model override from config**

In `.musubi/llm.json`'s per-model shape, read an OPTIONAL `max_output_tokens`.
In `agent/config.py`, add `resolve_model_output_override` returning that value
or `None` when the key is absent. Do NOT hardcode any model's true limit and do
NOT invent a physical default — absent means "no clamp", and the vendor rejects
an over-cap request at call time (see the "Why NOT a required per-model table"
note in the Architecture section).

- [ ] **Step 2: Add the effort-bounds resolver**

In `agent/context.py`, add tier defaults and the resolver:

```python
MUTATE_TIER_CEILING = 8192
READONLY_TIER_CEILING = 4096

def resolve_effort_bounds(
    *,
    can_mutate: bool,
    worker_max_output: int | None,
    model_output_override: int | None,
) -> tuple[int, int]:
    tier_default = MUTATE_TIER_CEILING if can_mutate else READONLY_TIER_CEILING
    ceiling = worker_max_output if worker_max_output else tier_default
    if model_output_override:            # operator opt-in only; None = no clamp
        ceiling = min(ceiling, model_output_override)
    floor = ceiling if can_mutate else min(effort_floor(), ceiling)
    return floor, ceiling
```

- [ ] **Step 3: Unit tests**

Cover: mutate worker → floor == ceiling; read-only → floor stays 2048; a
missing worker value falls back to the tier default; a `None` override leaves
the tier default untouched (no clamp); an operator override below the default
clamps the ceiling down.

---

### Task 2: Thread Bounds Through `_call_with_effort` and `_run_loop`

**Files:**
- Modify: `musubi/agent/run.py`

**Interfaces:**
- Changes: `_call_with_effort(vendor, messages, tools, *, floor, ceiling)` —
  accepts explicit bounds instead of calling `effort_floor()` / the constant.

**Rationale — why the ceiling stays:** `EFFORT_CEILING` is the only per-call
brake in the system. `TokenBudgetEnforcer` checks only *between* calls
(`_check_budget_preflight`, `run.py:539`); once a request is in flight the
substrate is blind until it returns. A degenerate-repetition cycle (a known
flash-tier failure) could otherwise generate up to the model's hard limit
(tens of thousands of tokens ≈ 100+ credits at the log-fitted
~0.0018 credits/output-token) in one call, discovered only at the next
preflight. Keep the brake; just size it per model / per worker.

- [ ] **Step 1: Add explicit bounds params to `_call_with_effort`**

Replace the internal `floor = min(effort_floor(), EFFORT_CEILING)` with the
caller-supplied `floor`; use the caller-supplied `ceiling` for the escalation
call. Preserve the single-retry contract: retry only when
`stop_reason == "max_tokens" and floor < ceiling`.

- [ ] **Step 2: Compute bounds once at `_run_loop` entry**

Near the top of `_run_loop` (`run.py:522`), before the cycle loop:

```python
worker_can_mutate = any(
    t.get("name") in ORDER_SENSITIVE_FILE_TOOLS for t in tools
)
model_override = resolve_model_output_override(model_family, cfg)  # None unless operator set one
base_floor, ceiling = resolve_effort_bounds(
    can_mutate=worker_can_mutate,
    worker_max_output=worker_max_output,   # threaded in via Task 3
    model_output_override=model_override,
)
escalated = False
```

Pass `floor=(ceiling if escalated else base_floor)` and `ceiling=ceiling` into
each `_call_with_effort` call (`run.py:566` and the forced-final at
`run.py:686`).

- [ ] **Step 3: Sticky escalation**

After each cycle's call, if the response hit `max_tokens` (or
`len(effort.attempts) > 1`), set `escalated = True` so the remaining cycles in
this loop skip the floor bet. This stops the repeated double-billing seen at
coder cycles 2 and 3.

- [ ] **Step 4: Integration test**

Extend `test_agent_loop.py`: a mutate worker's first call goes out at the
ceiling (assert `router.calls[0]["max_tokens"] == ceiling`, no `attempts=2`);
a read-only worker still opens at 2048; once a cycle truncates, the next cycle
opens at the ceiling.

---

### Task 3: Per-Worker `maxOutputTokens` Frontmatter

**Files:**
- Modify: `musubi/agent/subagent.py`
- Modify: `.github/agents/workers/coder.agent.md` (+ other mutate workers)

**Interfaces:**
- Produces: `worker_max_output: int | None` parsed from `agent_md` frontmatter,
  threaded into `run_unit` → `_run_loop`.

**Grounding:** `maxTurns: 8` already lives in `coder.agent.md` frontmatter but
is currently NOT read at spawn — `max_turns` comes from the spawn envelope
(`subagent.py:91`), and the observed run used `max_turns=10`, not the file's 8.
So the per-worker frontmatter→runtime wire does not yet exist; this task opens
it for the new field (and could later carry `maxTurns` too, out of scope here).

- [ ] **Step 1: Parse the field**

In `run_subagent`, after `agent_md = _read_agent_md(role, agents_dir)`
(`subagent.py:108`), parse the frontmatter for `maxOutputTokens` (reuse the
existing frontmatter parser; fall back to `None` when absent or malformed —
fail closed to the tier default).

- [ ] **Step 2: Thread it into the loop**

Pass `worker_max_output` through `run_unit` into `_run_loop` as a new
keyword-only param (default `None`, so the root and existing callers are
unaffected and resolve via tier default).

- [ ] **Step 3: Declare it on mutate workers**

In `coder.agent.md` frontmatter add e.g. `maxOutputTokens: 8192`. Leave
read-only workers (`explorer`, `reviewer-aux`, `finder`) omitting it or setting
a small value. Keep the `musubi-tier`/`expires-when`/`cost-lever` tags intact.

- [ ] **Step 4: Test**

Assert a coder spawn resolves its ceiling from frontmatter (mock a worker
declaring `maxOutputTokens: 8192`, assert the first call uses 8192 clamped to
the model cap); a worker with no field falls back to the mutate tier default.

---

### Task 4: Empty-Content Guard + Self-Explaining Elision Stub

**Files:**
- Modify: `musubi/agent/run.py` (`_file_tool_argument_error`, `~1831`)
- Modify: `musubi/agent/context.py` (`_elided_tool_arg_stub`, `~199`)

**Rationale:** Truncation can still occur at any ceiling, so a deterministic
guard turns "empty file + 3 cleanup cycles" into "1 tool error + immediate
regenerate". The elision-copyback (coder cycle 8) is the failure the
2026-07-02 elision plan predicted; putting the instruction *in the stub* lands
it where a confused flash-tier model is actually looking.

- [ ] **Step 1: Reject empty create/append content**

In `_file_tool_argument_error`, after the type checks pass, for
`musubi_write_file` / `musubi_append_file` only: if `content` is empty or
whitespace-only, append an error like `"content is empty; regenerate the full
file content (an empty write is almost always a truncation artifact)"`. Do NOT
apply this to `musubi_edit_file` — an empty `new_string` is a valid deletion.

- [ ] **Step 2: Make the stub imperative**

In `_elided_tool_arg_stub`, extend the message from "argument was already sent
to the MCP tool" to also say "DO NOT copy this marker as content; regenerate
the original text from scratch." Keep the deterministic `chars=`/`bytes=`/
`sha256=` fields so existing tests and the `_should_elide_tool_arg` guard
(`context.py:209`) still match on the `[musubi:elided-tool-arg` prefix.

- [ ] **Step 3: Tests**

`test_agent_loop.py`: `write_file(content="")` returns the guard error, not a
dispatch. `test_context.py`: the stub still starts with the elided prefix and
now contains the regenerate clause; round-trip elision tests still pass.

---

### Task 5: Salvage Respawn On Incomplete Artifact (design-gated)

**Files:**
- Modify: `musubi/agent/run.py` (spawn cap path, `~1589`)
- Modify: `docs/roadmap.md`

**Rationale:** `max_workers=1` used as a *cumulative* run cap
(`run.py:1558` comment) turned a recoverable mid-file exhaustion into a dead
end — the root could only read/glob/surrender because its `agent` surface has
no mutate tools. This touches the cumulative-worker-ceiling invariant, so it
needs a roadmap design note before code.

- [ ] **Step 1: Roadmap design note**

Record the choice: (a) count the cap against *concurrent* workers, or
(b) preferred — keep the cap and add exactly one explicit continuation-spawn
exception when a worker exhausts cycles with an incomplete artifact, the spawn
carrying a firewalled brief of the current file state (path, bytes, sha256
from the mutate audit). Option (b) preserves fail-closed spawning and HI #8
(every spawn still writes `subagent_audit`).

- [ ] **Step 2: Implement the chosen option**

Only after the design note lands. Gate the exception so it fires at most once
per root run and only for the incomplete-artifact condition — never a general
relaxation of the cumulative cap.

- [ ] **Step 3: Test**

A worker that exhausts `max_turns` with a non-empty-but-incomplete artifact
triggers exactly one continuation spawn; a normal completion does not; the cap
is never exceeded beyond the single sanctioned exception.

---

### Task 6: Platform-Aware Worker Prompt

**Files:**
- Modify: worker system-prompt construction (`agent/context.py` /
  `agent/subagent.py::build_subagent_system_prompt`)

**Rationale:** coder cycles 5-6 wasted a cycle on `rm` (exit 1, Windows) then
`del` (exit 0). The host shell is deterministic and belongs in the prompt.

- [ ] **Step 1: Inject one host line**

Add a single line derived from the detected platform (e.g. `"Host: Windows cmd
— use del not rm; \\ path separators"` or the POSIX equivalent) into the
worker system prompt. Source the platform from the existing workspace
detection rather than a new probe.

- [ ] **Step 2: Test**

Assert the rendered worker system prompt contains the host line for each
platform branch.

---

### Task 7: Regression, Roadmap, Commit, Push

**Files:**
- Modify: `docs/roadmap.md`

- [ ] **Step 1: Link this plan from the roadmap Backlog**

Add a Backlog entry summarizing the effort-ceiling / per-worker output budget
and linking `2026-07-13-agent-effort-ceiling-per-worker.md`. Note it as the
effort-economics follow-up to the 2026-07-09 orchestrator-tokens work.

- [ ] **Step 2: Focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_context.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_context.py -q
```

- [ ] **Step 3: Vendor / salvage regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_vendors.py musubi/tests/test_salvage_on_exhaust.py -q
```

- [ ] **Step 4: Stage intended files only, commit, push**

Commit with the identity flags per CLAUDE.md; Conventional Commit
`feat(agent): size effort ceiling per worker and model`. Push with
`git push -u origin <branch>`.

---

## Self-Review

- **Spec coverage:** addresses the *cause* of the Vietnam-run truncation (the
  2048 floor guaranteeing a truncated first mutate call, plus a 4096 ceiling
  below a one-shot dashboard's natural size), not just the symptom. Distinct
  from the 2026-07-09 plan, which guarded empty writes and chunked retries at
  the tool-result layer without resizing the `max_tokens` bounds.
- **HI #1 held:** every floor/ceiling decision is a pure function of `tools`,
  frontmatter, and `.musubi/llm.json` — no LLM call enters the substrate.
- **Fail-closed:** missing frontmatter, missing config, or unknown worker all
  resolve to conservative tier defaults (mutate 8192 / read-only 4096);
  nothing resolves to "unlimited". The model's true hard limit is enforced by
  the vendor at call time, not guessed from a per-model table (see the
  Architecture "Why NOT a required per-model physical-limit table" note — the
  ceiling is undefined for ollama/on-prem and uniform-and-high where it IS
  discoverable, so a per-model table would be drift-prone dead weight).
- **Ceiling preserved:** the per-call runaway brake stays — Task 2's rationale
  documents why removing it would open the one enforcement gap
  `TokenBudgetEnforcer` cannot cover.
- **Cost framing:** raising the mutate ceiling 4096 → 8192 adds ~7.5 credits of
  worst-case per-cycle exposure (only on a rare pathological cycle) versus the
  ~15-20 credits of deterministic truncation waste plus a failed run observed
  here.
- **Invariant touch isolated:** the only change that brushes a load-bearing
  rule (the cumulative worker cap, Task 5) is gated behind a roadmap design
  note and a single sanctioned exception, per the repo's "design discussion
  first" discipline.
