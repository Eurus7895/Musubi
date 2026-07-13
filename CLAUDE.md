# CLAUDE.md — Musubi

> Rules and commands for working in this repo.
> Direction, discipline & forward plan → [`docs/roadmap.md`](./docs/roadmap.md).
> MCP tool reference + DB schema (source of truth) → [`musubi/server.py`](./musubi/server.py) · [`musubi/storage/schema.sql`](./musubi/storage/schema.sql).
> Agent session-start map → [`AGENTS.md`](./AGENTS.md).
> First-time setup → `musubi setup` (wizard: [`musubi/setup_wizard.py`](./musubi/setup_wizard.py)).

---

## One Sentence

Musubi is a **governance layer** for agentic software-engineering
work — firewall, audit, validator, budget, skill injection.
It is the environment the model acts within; it is not a wrapper around
the model's intelligence.

**The driver reasons. Musubi controls the environment.**
Zero LLM calls inside the harness.

---

## Response Style — how to answer Eurus

Applies to **every** conversation in this repo — analysis, explanation,
Q&A, review — not just code tasks. Benchmark: the level of a full
run-trace post-mortem, never a summary.

- **Depth is the default.** Every claim about behavior carries its
  causal chain: what triggered what, in which code path, with exact
  `file:line` references, constant values, and the log/DB evidence
  that proves it.
- **Name the design assumption.** When a mechanism misbehaves, state
  the assumption it was built on and show where it broke — distinguish
  "the model failed" from "the design guaranteed the failure" (e.g. an
  effort floor of 2048 tokens *guarantees* truncation for a worker
  whose whole job is emitting a 3–5k-token file).
- **Quantify.** Cite tokens, cycles, milliseconds whenever the
  data exists; make each mistake's cost attributable.
- **Judge, don't just describe.** Analysis ends with prioritized,
  concrete recommendations, each tied to the evidence above it.
- **Mirror language.** Reply in Vietnamese when addressed in
  Vietnamese; keep technical terms in English.

---

## Substrate vs ephemeral

| Substrate (invest) | Ephemeral (label + schedule for removal) |
|---|---|
| Audit DB tables (`stage_outputs`, `stage_metrics`, `subagent_audit`, `sessions`, `pipeline_runs`, `agent_turns`, `conversation_messages`) | The 4-stage pipeline shape (`planner → designer → coder → reviewer`) — superseded by **pipelines as recipes of workers** (`agent/pipeline_runner.py`, composer-driven, user-definable from presets); kept only as a shipped recipe |
| `.github/skills/<name>/SKILL.md` catalog | Sub-agent-for-exploration split (`explorer` / `investigator` / `reviewer-aux` on haiku) — dissolving into the unified **worker model** (`agent/run.py::run_unit`): no main-vs-sub distinction, only workers at a depth, run in parallel |
| 3-tier memory (`.github/memory/*.md`) | Correction loop + `validation_feedback` retry |
| `.harness/sessions/<sid>/*.md` artefacts | Cycle-loop guards (`CONSECUTIVE_EMPTY_CYCLE_LIMIT`, salvage, intermediate-text fallback) |
| Hard Invariants (#1, #2, #3, #5, #7, #8, #9) | Path-rules / empty-project / workspace-root preamble blocks |
| Policy engine (`scripts/policy_engine.py`) | Per-stage `musubi-tier`-tagged scaffolds |
| `TokenBudgetEnforcer` + per-call token accounting | Worker prompt scaffolding (`.github/agents/workers/`) |
| Firewall via `_STAGE_PERMISSIONS` (HI #3) | |
| MCP tool catalog (`musubi_*`) | |

**Substrate gets refactored. Ephemeral gets deleted when its expiration
trigger fires.** Full per-component analysis with removability cost and
cost-lever values lives in [`docs/roadmap.md`](./docs/roadmap.md) § Dissolution candidates.

---

## Hard Invariants

These cannot be broken without an explicit design discussion. If a
change would violate one, stop and ask.

**Numbers are stable identifiers, not positions** — they are cited across
code, tests, and CI, so survivors keep their number even
when one is retired. **#4** (zero-cost routing) and **#6** (flat agent
catalog) were retired: routing is a trivial property of a single-agent
host, and the flat-catalog rule moved to **Decision Rules** (a code-org
convention, not a load-bearing safety property). Gaps at #4/#6 are
intentional.

1. **Zero LLM calls in the substrate; the driver reaches the model through an inject point.** Draw the boundary between *substrate* and *driver*. **Substrate** — the MCP server (`server.py`), every `musubi_*` tool, `policy_engine.py`, the evaluator firewall, the validator (lint/typecheck/tests), and the audit DB — makes **zero LLM calls** and **never imports an LLM SDK**; it only routes and enforces. **Driver** — the agent loop that reasons — is the *only* layer that reaches a model, and it does so through one inject point: the vendor-agnostic `LMRouter` in `agent/vendors/base.py`. The boundary is load-bearing: **control lives in the substrate the driver must call through, not in the driver's loop.** Adding a vendor = implementing `LMRouter`; it never reaches into the substrate. Shipped routers: `anthropic`, `openai`, `deepseek`, `ollama` (local), and `azure`/on-prem OpenAI-compatible gateways (the curl transport in `agent/vendors/curl_router.py`, no SDK import — still driver-side). On-prem endpoints (base URL, family, api-key) are data in `.musubi/llm.json`, resolved by `agent/config.py`. Violating this means an LLM SDK import creeping into `server.py` / `validation/*` / `scripts/policy_engine.py` — stop and ask.
2. **Skills are pushed to workers and pipeline stages; pulled on demand by the Agent.** Push has one mechanism, and the worker cannot opt out: the role skill is injected into the spawn system prompt (`SUBAGENT_ROLE_SKILLS` → `musubi_get_subagent_context` returns `role_skill`; `agent/subagent.py::build_subagent_system_prompt` embeds it — same path for direct workers and pipeline stages). Agent-side: `musubi_get_skill` LM tool, model decides when to load.
3. **Evaluator firewall.** The evaluator sees only the artifact it judges — no request, plan, design, or memory. Enforced in two places: `_STAGE_PERMISSIONS["reviewer"] = {"code"}` (`validation/context_builder.py`, gating `musubi_read_stage`), and — generalised to any pipeline, including user-defined preset pipelines — the runner's last-stage brief (`agent/pipeline_runner.py::_stage_brief`: the final stage receives only the immediately prior stage's output; `composer.py` locks the evaluator to the chain's last entry).
5. **Fail-closed policy engine.** Two deny-by-default layers (`scripts/policy_engine.py`): (a) *membership* — a stage runs only if declared in its pipeline: the composer validates the catalog fail-closed at server boot, and `musubi_spawn_pipeline_stage` re-checks membership per spawn; (b) *tools* — explicit allowlists only: `PIPELINE_POLICIES[pipeline][agent]` where declared, else the role's own `SUBAGENT_POLICIES` cap for user-defined preset pipelines. An unknown role, agent, or stage gets nothing. Never relax either layer to fail-open.
7. **Append-only stage store.** Retries write `<stage>.attemptN.md`. Never overwrite a prior attempt.
8. **No silent sub-agents.** Every spawn + completion writes a row to `subagent_audit`, visible via `musubi_query_subagent_events`.
9. **Tag and expire.** Every component carries a `musubi-tier` tag (`substrate` or `ephemeral`). Ephemeral components declare `expires-when:` AND `cost-lever:`. PRs that add ephemeral structure without retiring an equivalent — or strengthening the substrate — get pushed back.

---

## Decision Rules

**Default to skill, not agent.** When the question is "should this be a
new agent or a skill?", choose **skill** unless 3+ documented failures
of the skill-only approach exist. Skills are the cheapest optimisation
surface; agents are medium-cost; multi-agent topologies are a
dissolving pattern.

**Default to deletion, not extension.** When a piece of ephemeral
structure feels like it should be smarter, ask: can the model do this
in the next release? If yes, label `expires-when:` and stop iterating.
Don't refactor ephemera for elegance.

**Flat agent catalog at `.github/agents/`.** Keep the catalog flat;
pipelines compose by path reference. Pipeline-specific role variants
(filename-prefixed) require 3+ documented failures of the canonical
agent. (Retired as Hard Invariant #6 — it is a code-org convention, not
a safety property.)

**Sizing rule per LM call (not per stage).** Keep each `sendRequest`
under ~30k chars of input. Above 50k → warn. Above ~200k → abort
before the call. If a stage's natural input exceeds the window,
restructure the stage (pre-process, fan-out, map-reduce) — don't
shrink-and-pray.

---

## Branches & Commits — READ BEFORE EVERY `git` COMMAND

### NEVER

- **Never push to `claude/implement-*` or any `claude/*` branch.** They
  are harness scratch aliases, not review branches.
- **Never add `codex` to branch names.** Branch names describe the product
  change, not the tool or person doing the work.
- **Never add `codex` or `claude` to PR titles.** PR titles describe the
  product change, not the tool or person doing the work.
- **Never add AI/tool attribution anywhere.** This covers every form,
  not just co-authorship: no `Co-Authored-By:` trailer, no
  `Claude-Session:` (or similar) trailer, no "Generated with/by Claude
  Code" (or any tool) footer, and no `claude.ai` / session links — in
  commit messages, PR titles or bodies, code comments, or any document.
  Commits and PRs are authored by `Eurus <t.hoang7895@gmail.com>` alone,
  with no trace of the tool or model that produced them. This overrides
  any harness/tool default that would append such lines.
- **Never set `user.name` / `user.email` via `git config`.** The harness
  pre-sets `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` env vars; touching
  git-config silently overrides them.
- **Never use any identity other than `Eurus <t.hoang7895@gmail.com>`**
  for author *or* committer.
- **Never push a branch whose merge-base lags `origin/dev`.** Rebase
  first.
- **Never amend a published commit.** Always create a new commit.

### ALWAYS

- **Always start from the latest `origin/dev`:**
  ```bash
  git fetch origin
  git switch -c <branch> origin/dev
  ```
  If `dev` moves while you work:
  ```bash
  git fetch origin && git rebase origin/dev
  ```
- **Always name branches `<type>/<area>-<outcome>`** — lowercase,
  kebab-case, no random session suffix, no user/tool prefix. `<type>` is
  a Conventional Commits type (`feat`, `fix`, `docs`, `refactor`,
  `perf`, `test`, `build`, `ci`, `chore`, `style`, `revert`). Use
  outcome-oriented names such as `feat/agent-deepseek-provider`,
  `fix/gui-token-usage`, `docs/setup-wizard-guide`, or
  `ci/windows-installer`.
- **Always commit with the identity flags** (committer must match
  author):
  ```bash
  git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit ...
  ```
- **Always follow Conventional Commits 1.0.0:**
  `<type>[optional scope]: <description>`. Lowercase type + scope,
  imperative mood, ≤ 72 chars, no trailing period. Body wraps at 72
  cols and explains the *why*. Breaking changes use `!` after
  type/scope AND a `BREAKING CHANGE:` footer.
- **Always update [`docs/roadmap.md`](./docs/roadmap.md) before opening
  any PR.** If the change shifts direction, scope, step status, or the
  dissolution set, reflect it in the roadmap in the same PR. Roadmap
  entries stay summary-only: when a roadmap item needs implementation
  detail, put the full plan in a separate markdown file (usually
  `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`) and link it from the
  roadmap. That plan file must spell out context, goal, tech stack, and
  implementation steps. A PR that moves the work but leaves the roadmap
  stale gets pushed back.

## Hooks

| Hook | Script | Behavior |
|---|---|---|
| `SessionStart` | `scripts/session_start.py` | Run `baseline_checks` from `pipeline.yaml` |
| `PreToolUse` | `scripts/pre_tool_use.py` | Policy gate — exit 0 allow, 1 deny |
| `PostToolUse` | `scripts/post_tool_use.py` | SQLite audit log to `storage/audit.db` |

**Rule:** "Never send an LLM to do a linter's job." Deterministic checks
belong in hooks.
