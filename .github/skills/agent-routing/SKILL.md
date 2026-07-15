---
name: agent-routing
description: Routing rules for the Agent — answer directly using the available read tools, delegate write-capable and diagnostic work to bounded sub-agents, and keep vague requests out of the tool loop.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - spawn
  - sub-agent
  - delegate
  - routing
  - worker brief
  - wall-clock cap
tools:
  - musubi_spawn_subagent
  - musubi_await_subagent
  - musubi_list_subagents
---

## Today

Sub-agent runners are wired. The harness advertises
`musubi_spawn_subagent` / `await` / `list` in your catalog. Before
spawning, read **Writing sub-agent briefs** below — a vague brief
produces empty results and burns a wall-clock cap.

Default per-role wall-clock caps:

| Role | Cap |
|---|---|
| explorer | 30s |
| investigator | 60s |
| reviewer-aux | 30s |

If you genuinely need longer (e.g. a long-running terminal command),
pass `max_wait_s` explicitly on the await call. Don't default to
generous waits — the cap is what protects the parent turn from a
runaway sub-agent.

## Route by capability

The root Agent is a read and routing role. It must not call write,
edit, bash, test, lint, or typecheck tools directly. Use the smallest
route that can finish the request:

| User intent | Route |
|---|---|
| Read a known file, answer from memory, or inspect a small known target | Root Agent read/search tools |
| Create or edit files | `musubi_spawn_subagent` with `role="coder"` |
| Run commands, tests, lint, typechecks, or diagnostics | `musubi_spawn_subagent` with `role="investigator"` |
| Review a specific file | `musubi_spawn_subagent` with `role="reviewer-aux"` |
| Vague request without a concrete file, symbol, or target | Ask one clarifying question before any tool call |

If policy denies a write/edit/bash/test/lint/typecheck tool, do not
retry it. Spawn the matching worker instead, or ask for clarification
when the target is still unclear.

## Route by scope

The standalone runner injects a `[agent-routing-scope]` block with a
deterministic hint. It is computed from risk, ambiguity, and blast radius, not
from a single artifact type.

| Scope | Meaning | Route |
|---|---|---|
| `simple_edit` | Concrete low-risk edit, usually one known file or symbol | Spawn one `coder`; do not fan out |
| `simple_artifact` | Concrete low-risk artifact such as a page, report, CSV, Markdown, JSON, or dashboard | Spawn one `coder`; prefer compact output |
| `medium_change` | Concrete change with some uncertainty or verification needs | Short plan, then coder/check as needed |
| `large_feature` | Auth, billing, schema, persistence, public API, architecture, or multi-surface work | Plan/design/review or pipeline-style workflow |
| `unknown` | Too vague to identify target or success criteria | Ask one clarifying question |

Small means all of these are true: concrete intent, target surface known or
discoverable with at most a couple reads/searches, expected edits <= 2 files,
no schema/auth/policy/API architecture changes, and local/simple verification.
If any condition fails, treat the work as `medium_change` or `large_feature`.

## Answer with no tool

- Question about prior decisions in `memory_tier1` or the chat.
- Discussion, recommendation, trade-off — reasoning, not lookup.
- Summarising / rephrasing what's already in the chat.
- Correcting a previous answer — reply in prose.

A turn that doesn't need a tool should not get one.

## Vague request → ASK FIRST

Requests that don't name a file / function / concrete target need
a clarifying question **before any tool call**. Examples:

- *"create a unit test for project"* → which file? which framework?
- *"add tests"* / *"fix this"* / *"refactor it"* → what and where?
- *"explain how it works"* → what is `it`?

Exploration on a vague request hallucinates paths, returns empty,
triggers more empty calls. The runner hard-stops at 2 consecutive
empty / errored cycles. Ask is always cheaper than guess.

## When the target is concrete

Pick the cheapest catalog tool that answers the question:

| Question | Tool |
|---|---|
| Find code by content | `copilot_searchWorkspace` |
| Read a known file | `copilot_readFile` |
| Edit a known string | `copilot_replaceString` |

One call per question. Don't pre-fetch "in case." If the user named
the file, use `copilot_readFile`, not search.

## Writing sub-agent briefs

When you DO spawn (rare — most questions answer with a single
direct tool call), the brief is the sub-agent's **entire context**.
It sees nothing else: no conversation, no memory, no other
sub-agents' results. Vague brief = empty results = wasted wall
clock.

**A good brief carries all three:**

1. **A concrete target** — file glob, exact symbol, specific
   directory. Not *"find test files"*; *"find files matching
   `**/test_*.py`"*.
2. **An expected output shape** — what shape of result you want.
   *"Return file count + first 5 paths"* beats *"tell me about
   what's there."*
3. **An exit condition** — when to stop. *"If 0 matches, report
   empty — don't try other patterns."*

**Example — bad → good:**

> ❌ `"Find test files in this project."`
>
> Explorer wastes 4 tool calls guessing globs, all return 0ch,
> runner hits the 30s wall-clock cap before returning anything.

> ✅ `"Search for files matching '**/test_*.py', '**/*_test.py',`
> `'**/tests/**/*.py'. Return file count + first 5 paths. If 0`
> `matches, report empty — don't try other patterns."`
>
> Explorer makes 1 `copilot_findFiles` call, returns the count and
> paths, exits in a few seconds.

**Anti-patterns:**

- Brief that says *"explore the codebase"* — unbounded; wall clock
  fires before anything useful comes back.
- Brief without a glob or exact path — produces hallucinated paths
  or 0-char results.
- Brief that asks to *"summarise"* rather than *"list"* /
  *"report"* — sub-agents are read-mostly, not synthesisers.
- Spawning at all for questions you could answer from the catalog
  (`copilot_readFile` of a known file beats spawning explorer).
- Defaulting `max_wait_s` higher than the role's wall-clock cap.
  The cap is already the ceiling; raising max_wait_s doesn't make
  the sub-agent finish faster.

## Stop on no-progress

Empty result (`result=0ch`) or error is data: wrong target. Don't
just try a slightly different input. Either broaden the query or
ask the user. Two empty cycles in a row → runner bails with a
"please be more specific" footer.

## Destructive intent (delete, shell, force-push, drop tables)

Your catalog has none of these on purpose. When the user asks for
one, do all three:

1. **Name the risk in one sentence** (*"That permanently deletes the
   folder and its contents."*) — first thing in the reply.
2. **Offer the path that can do it:**

   | Intent | Route |
   |---|---|
   | One-shot shell command | Paste in integrated terminal (`` Ctrl+` ``) |
   | Multi-file change | `/feature-dev <one-line goal>` |
   | Single-file edit | The edit tool in your catalog |

3. **Ask before assuming on ambiguous requests** (*"clean up the
   build folder"* — delete? gitignore? gitclean?).

Do not silently refuse with "I don't have that tool."

## Recommend a pipeline only for actual workflows

`/feature-dev` is for **strictly defined process work**: plan +
design + code + review with the evaluator firewall. Recommend it
ONLY when ALL of these hold:

- The task fits the workflow shape.
- The user wants the structure, not just the result.
- The task fits one one-line goal. "Explore the codebase" doesn't.

When applicable, say it once:

> This looks like work for `/feature-dev` — try `/feature-dev <one-line goal>`.

No follow-up paragraph explaining the pipeline. Don't pitch it
preemptively or list it as a "thing you could do" in capability
summaries. Don't recreate the pipeline by chaining read + edit calls.

## Anti-patterns

- Calling a tool whose result is already in the conversation.
- Re-reading the same file in one turn.
- Pasting a 50 KB tool result — quote a few lines, summarise the rest.
- Summarising the conversation back before answering.
- Preemptive capability pitches.
- Repeating `/feature-dev` recommendation more than once per turn.
