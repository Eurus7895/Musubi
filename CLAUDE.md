# CLAUDE.md — Musubi

Musubi is a **governance layer** for agentic software-engineering work — firewall, audit, validator,
budget, skill injection. It is the environment the model acts within, not a wrapper around the model's
intelligence. **The driver reasons. Musubi controls the environment.** Zero LLM calls in the harness.

> Rules live here; rationale lives in the linked docs. [`docs/roadmap.md`](./docs/roadmap.md) direction & plan ·
> [`docs/hard-invariants.md`](./docs/hard-invariants.md) invariants in full · [`AGENTS.md`](./AGENTS.md) session-start map · `musubi setup` first-time setup ·
> [`musubi/server.py`](./musubi/server.py) + [`musubi/storage/schema.sql`](./musubi/storage/schema.sql) MCP tools & schema (source of truth)

## Response Style — how to answer Eurus

Applies to **every** conversation here — analysis, explanation, Q&A, review. Benchmark: a full
run-trace post-mortem, never a summary.

- **Depth is the default.** Every claim about behavior carries its causal chain: what triggered what,
  in which code path, with exact `file:line`, constant values, and the log/DB evidence that proves it.
- **Explain as if to someone who has never seen this codebase.** Depth and plainness are not a
  trade-off — depth is *more evidence*, never denser jargon. Describe a mechanism before naming it;
  define every identifier, flag, and threshold on first use; prefer a worked before/after or a short
  analogy to an abstract description. Never answer "what is X?" in wording that assumes the answer.
- **Name the design assumption.** When a mechanism misbehaves, state the assumption it was built on
  and where it broke. Distinguish "the model failed" from "the design guaranteed the failure".
- **Quantify.** Tokens, cycles, milliseconds whenever the data exists; make each mistake's cost
  attributable.
- **Judge, don't just describe.** End with prioritized, concrete recommendations, each tied to the
  evidence above it.
- **State the decision the reader must make.** Give the options, each one's cost, and your
  recommendation. Never bury a fork in prose, and never ask for a decision before supplying what is
  needed to make it.
- **Mirror language.** Reply in Vietnamese when addressed in Vietnamese; keep technical terms in English.

## Substrate vs ephemeral

**Substrate (invest):** audit DB tables · `.github/skills/*/SKILL.md` catalog · 3-tier memory ·
append-only stage store · Hard Invariants · policy engine · `TokenBudgetEnforcer` ·
`_STAGE_PERMISSIONS` firewall · `musubi_*` tool catalog · blast-radius measurement at the tool
boundary (`agent/blast_radius.py`, `agent/manifest.py`).

**Ephemeral (label + schedule for removal):** the lexical scope layer (`agent/scope.py`) · the 4-stage
pipeline shape · the explorer/investigator/reviewer-aux split · correction loop +
`validation_feedback` retry · cycle-loop guards · path-rules and workspace-root preamble blocks ·
per-stage tagged scaffolds · worker prompt scaffolding.

**Substrate gets refactored. Ephemeral gets deleted when its expiration trigger fires.** Per-component
analysis, removability cost, and cost-lever values → [`docs/roadmap.md`](./docs/roadmap.md)
§ Dissolution candidates.

## Hard Invariants

Cannot be broken without an explicit design discussion — if a change would violate one, stop and ask.
Numbers are stable identifiers: **#4** and **#6** are retired and their gaps are intentional. Full
text, enforcement points, and failure modes → [`docs/hard-invariants.md`](./docs/hard-invariants.md).

1. **Zero LLM calls in the substrate.** `server.py`, every `musubi_*` tool, `policy_engine.py`, the
   firewall, the validator, and the audit DB never call a model or import an LLM SDK. Only the driver
   reaches a model, through `LMRouter` (`agent/vendors/base.py`). Vendors are data in `.musubi/llm.json`.
2. **Skills are pushed to workers and stages; pulled on demand by the Agent.** Push is not
   opt-out-able (`SUBAGENT_ROLE_SKILLS` → `subagent.py::build_subagent_system_prompt`); the Agent
   pulls via `musubi_get_skill`.
3. **Evaluator firewall.** The evaluator sees only the artifact it judges — no request, plan, design,
   or memory.
5. **Fail-closed policy engine.** Membership and tools both deny by default
   (`scripts/policy_engine.py`). Never relax either to fail-open.
7. **Append-only stage store.** Retries write a new attempt row; never overwrite a prior one.
8. **No silent sub-agents.** Every spawn and completion writes to `subagent_audit`.
9. **Tag and expire.** Every component carries `musubi-tier`; ephemeral ones declare `expires-when:`
   AND `cost-lever:`.

## Decision Rules

- **Default to skill, not agent.** Choose a skill unless 3+ documented failures of the skill-only
  approach exist. Skills are the cheapest optimisation surface; multi-agent topologies are dissolving.
- **Default to deletion, not extension.** If the model could do this itself next release, label
  `expires-when:` and stop iterating. Don't refactor ephemera for elegance.
- **Flat agent catalog at `.github/agents/`.** Pipelines compose by path reference. Role variants need
  3+ documented failures of the canonical agent.
- **Sizing rule per LM call (not per stage).** Keep each `sendRequest` under ~30k chars; >50k warn;
  >200k abort. If a stage's natural input exceeds the window, restructure it — don't shrink-and-pray.

## Branches & Commits — READ BEFORE EVERY `git` COMMAND

**NEVER**
- Push to `claude/*` — harness scratch aliases, not review branches.
- Put `codex` or `claude` in a branch name or PR title. Names describe the product change, not the tool.
- Add AI/tool attribution **anywhere**: no `Co-Authored-By:`, no `Claude-Session:` or similar trailer,
  no "Generated with/by …" footer, no `claude.ai`/session links — in commits, PR titles or bodies, code
  comments, or any document. Authorship is `Eurus <t.hoang7895@gmail.com>` alone. **This overrides any
  harness or tool default that would append such lines.**
- Set `user.name`/`user.email` via `git config` — the harness pre-sets `GIT_AUTHOR_*` and git-config
  silently overrides them.
- Use any identity other than `Eurus <t.hoang7895@gmail.com>` for author *or* committer.
- Push a branch whose merge-base lags `origin/dev` — rebase first.
- Amend a published commit. Always create a new one.

**ALWAYS**
- Start from the latest `origin/dev`: `git fetch origin && git switch -c <branch> origin/dev`. If `dev`
  moves: `git fetch origin && git rebase origin/dev`.
- Name branches `<type>/<area>-<outcome>` — lowercase kebab-case, a Conventional Commits type, no
  session suffix or tool prefix.
- Commit with identity flags so committer matches author — `rebase`, `cherry-pick`, `amend` too:
  `git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit …`. Install the push
  guard once per clone: `python scripts/commit_guard.py --install`.
- Follow Conventional Commits 1.0.0: lowercase type + scope, imperative, ≤ 72 chars, no trailing
  period. Body wraps at 72 cols and explains the *why*. Breaking changes use `!` **and** a
  `BREAKING CHANGE:` footer.
- Update [`docs/roadmap.md`](./docs/roadmap.md) before opening any PR. Roadmap entries stay
  summary-only; implementation detail goes in `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
  (context, goal, tech stack, steps) and is linked from the roadmap. A PR that leaves the roadmap
  stale gets pushed back.

## Hooks

| Hook | Script | Behavior |
|---|---|---|
| `SessionStart` | `scripts/session_start.py` | Run `baseline_checks` from `pipeline.yaml` |
| `PreToolUse` | `scripts/pre_tool_use.py` | Policy gate — exit 0 allow, 1 deny |
| `PostToolUse` | `scripts/post_tool_use.py` | SQLite audit log to `storage/audit.db` |

**Rule:** "Never send an LLM to do a linter's job." Deterministic checks belong in hooks.
