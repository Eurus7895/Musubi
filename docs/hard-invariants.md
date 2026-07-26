# Hard Invariants — full text

`CLAUDE.md` carries the one-line rule for each invariant, because it is
loaded into every conversation and pays for its length in every context
window. This file carries what the rule alone cannot: the boundary each one
draws, where it is enforced, and how it fails.

These cannot be broken without an explicit design discussion. If a change
would violate one, stop and ask.

**Numbers are stable identifiers, not positions.** They are cited across code,
tests, and CI, so survivors keep their number even when one is retired. **#4**
(zero-cost routing) and **#6** (flat agent catalog) were retired: routing is a
trivial property of a single-agent host, and the flat-catalog rule moved to
*Decision Rules* in `CLAUDE.md` — a code-org convention, not a load-bearing
safety property. The gaps at #4 and #6 are intentional.

---

## #1 — Zero LLM calls in the substrate; the driver reaches the model through one inject point

Draw the boundary between *substrate* and *driver*.

**Substrate** — the MCP server (`server.py`), every `musubi_*` tool,
`policy_engine.py`, the evaluator firewall, the validator (lint/typecheck/
tests), and the audit DB — makes **zero LLM calls** and **never imports an LLM
SDK**. It only routes and enforces.

**Driver** — the agent loop that reasons — is the *only* layer that reaches a
model, and it does so through one inject point: the vendor-agnostic `LMRouter`
in `agent/vendors/base.py`.

The boundary is load-bearing: **control lives in the substrate the driver must
call through, not in the driver's loop.** Adding a vendor means implementing
`LMRouter`; it never reaches into the substrate.

Shipped routers: `anthropic`, `openai`, `deepseek`, `ollama` (local), and
`azure` / on-prem OpenAI-compatible gateways (the curl transport in
`agent/vendors/curl_router.py` — no SDK import, still driver-side). On-prem
endpoints (base URL, family, api-key) are **data** in `.musubi/llm.json`,
resolved by `agent/config.py`. This is why an agent prompt file must not
hardcode a model id: the vendor is configuration, not a constant.

**How it breaks:** an LLM SDK import creeping into `server.py`,
`validation/*`, or `scripts/policy_engine.py`. Stop and ask.

## #2 — Skills are pushed to workers and pipeline stages; pulled on demand by the Agent

Push has one mechanism and the worker cannot opt out: the role skill is
injected into the spawn system prompt (`SUBAGENT_ROLE_SKILLS` →
`musubi_get_subagent_context` returns `role_skill`;
`agent/subagent.py::build_subagent_system_prompt` embeds it). Same path for
direct workers and pipeline stages.

Agent-side: the `musubi_get_skill` LM tool — the model decides when to load.

## #3 — Evaluator firewall

The evaluator sees only the artifact it judges: no request, plan, design, or
memory. Enforced in two places:

- `_STAGE_PERMISSIONS["reviewer"] = {"code"}` in
  `validation/context_builder.py`, gating `musubi_read_stage`.
- The runner's last-stage brief —
  `agent/pipeline_runner.py::_stage_brief` gives the final stage only the
  immediately prior stage's output, and `composer.py` locks the evaluator to
  the chain's last entry. This generalises the rule to any pipeline,
  including user-defined preset pipelines.

## #5 — Fail-closed policy engine

Two deny-by-default layers in `scripts/policy_engine.py`:

- **Membership** — a stage runs only if declared in its pipeline. The composer
  validates the catalog fail-closed at server boot, and
  `musubi_spawn_pipeline_stage` re-checks membership per spawn.
- **Tools** — explicit allowlists only: `PIPELINE_POLICIES[pipeline][agent]`
  where declared, else the role's own `SUBAGENT_POLICIES` cap for
  user-defined preset pipelines.

An unknown role, agent, or stage gets nothing. Never relax either layer to
fail-open.

## #7 — Append-only stage store

Retries write a **new** `stage_outputs` attempt row — write-once per
`(session_id, stage, chunk_id, attempt)`
(`musubi/session/state.py::write_stage`). Never overwrite a prior attempt.

## #8 — No silent sub-agents

Every spawn and every completion writes a row to `subagent_audit`, visible via
`musubi_query_subagent_events`.

## #9 — Tag and expire

Every component carries a `musubi-tier` tag (`substrate` or `ephemeral`).
Ephemeral components declare `expires-when:` **and** `cost-lever:`. PRs that
add ephemeral structure without retiring an equivalent — or strengthening the
substrate — get pushed back.
