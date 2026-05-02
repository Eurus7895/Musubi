# Memory in CopilotHarness

> How the harness remembers things — across stages within a session, across
> sessions, and across continuous orchestrator conversations.

This is the contract. Implementation lives in `copilot-harness/memory/`.

## References

- celesteanders/harness — `docs/best-practices.md`
  ("map, not encyclopedia"; "context as scarce"; "repository as system of record")
- mitchellh.com/writing/harness-engineering
  ("fresh context windows + structured handoff"; "subagents for offloading")

Our 3-tier memory + sub-agent firewall is our concrete implementation of those
patterns.

---

## Three tiers

```
Tier 1 — MEMORY.md             ~200 tokens, ALWAYS injected
                                Pointers index. What decisions were made,
                                where Tier 2 knowledge lives.

Tier 2 — architecture.md       Loaded on demand via
       — failure-patterns.md   harness_get_memory_entry(name).
                                Distilled decisions and recurring failures.

Tier 3 — sessions DB           Cross-session substring search via
                                harness_query_sessions(query).
                                Returns IDs + 160-char excerpts. Never
                                raw transcripts.
```

Tier 1 is "the map." Tier 2 is the chapters. Tier 3 is the historical archive.

---

## Per-agent injection rules

| Agent | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Pipeline `planner` / `designer` / `coder` | ✅ auto-injected | on demand | on demand |
| Pipeline `reviewer` | ❌ — evaluator firewall | ❌ | ❌ |
| `orchestrator` (new) | ✅ auto-injected | on demand | on demand |
| Any sub-agent (`explorer`, `investigator`, `reviewer-aux`, etc.) | ❌ — sub-agent firewall | ❌ | ❌ |

**Two firewalls, two reasons.**

- The **evaluator firewall** (reviewer): the reviewer judges artifacts against the
  code-review checklist. Past team preferences in memory would bias the verdict
  toward "what we usually do" instead of "what the checklist says." Skip memory.
- The **sub-agent firewall**: a sub-agent exists to do focused lookup work and
  return a compressed summary. Memory access would re-introduce the
  context-bloat problem the sub-agent exists to prevent. Sub-agents read briefs
  only.

---

## Lifecycle

### Read

1. Agent calls `harness_read_stage(stage)`.
2. Harness builds the per-agent context (firewall).
3. Harness appends `tier1_index` + `tier2_available` (skipped for reviewer).
4. Agent may then call `harness_get_memory_entry(name)` to pull a Tier-2 file,
   or `harness_query_sessions(query)` to search Tier 3.

### Write — distillation triggers

`failure-patterns.md` (Tier 2) gets new entries from four triggers. All four
funnel through `session_distiller.append_pattern(pattern, source)` which
deduplicates at append time.

| Trigger | When it fires | Captures |
|---|---|---|
| **Per-turn (gated)** | After every orchestrator reply, only if a noteworthy event happened (sub-agent failed, retry occurred, spawn cap hit) | Turn summary + flagged event |
| **Chat closed** | User runs `/clear` or chat panel is closed | Final sweep — anything not yet distilled |
| **Reviewer fail** | A `reviewer` sub-agent returns `passed: false` | Failure cause + offending code excerpt |
| **User frustration** | Deterministic regex match on negative-sentiment patterns in user message — no LLM | User's frustration phrase + the prior assistant turn that caused it + recent context |

**Why deterministic frustration detection.** Hard Invariant #1: zero LLM calls
inside the harness. Frustration patterns live in
`.github/memory/sentiment-patterns.json` as a configurable regex list.

### Compact

`harness_compact_memory` runs when `failure-patterns.md` exceeds 5 KB. Keeps the
union of (top-10 most-frequent, top-10 most-recent) and drops the rest. Same
function is invoked manually via the MCP tool.

### Dedup

`append_pattern` checks for existing entries with the same
`(error_signature, root_cause)` and increments their `frequency` counter
instead of writing a duplicate. `frequency` feeds the compaction ranking.

---

## Orchestrator integration

The orchestrator runs as a continuous conversation. "Session" is redefined for
this mode:

> **Session = one user turn.** Many sessions chained make one chat.

This keeps best-practice 8 ("one task per session") honest: each user turn is
one task. The orchestrator's persistent context across turns is held by the
extension's conversation transcript (`storage/conversations/<chat_id>.jsonl`),
not by extending the session abstraction.

**Tier 1 is auto-injected into the orchestrator on every turn** — same path as
pipeline agents.

**Tier 2 / Tier 3 are pulled by the orchestrator on demand** via the existing
MCP tools.

**Sub-agents the orchestrator spawns get nothing.** Same firewall as pipeline
sub-agents.

---

## Conversation transcript (orchestrator only)

Separate from memory; documented here because they are easily confused.

| | Memory (3 tiers) | Conversation transcript |
|---|---|---|
| Lives in | `.github/memory/` (Tier 1, 2) + DB sessions table (Tier 3) | `storage/conversations/<chat_id>.jsonl` |
| Persists across | All sessions, all chats | One chat (one `chat_id`) |
| Granularity | Distilled patterns, decisions | Full message history |
| Loaded into | Agent context as injected fields | LLM call as message array |
| Compacted by | `harness_compact_memory` (5 KB cap) | Reactive: 80%/90%/99% of model window (per Claude Code's pattern) |

The transcript is replayed verbatim to give the orchestrator continuity.
Memory is consulted for cross-conversation knowledge.

---

## What memory is NOT for

- **Caching answers to user questions** — that's the conversation transcript.
- **Storing user preferences for tone/style** — those go in `.github/instructions/` (always-loaded), not memory (failure-pattern-shaped).
- **Project documentation** — that's `docs/`. Memory is *operational learnings*, not reference material.
- **Per-session scratch space** — that's `.harness/sessions/<sid>/`. Memory persists across sessions.

---

## Adding a new Tier 2 file

1. Drop the `.md` file in `.github/memory/`.
2. Add a one-line pointer in `MEMORY.md` under `## Active Tier 2 Files`.
3. Agents discover it via the `tier2_available` list and pull on demand.

No code change needed.

---

## File map

```
.github/memory/
  MEMORY.md                    Tier 1 — always injected
  architecture.md              Tier 2 — past architectural decisions
  failure-patterns.md          Tier 2 — distilled failures (auto-compacted)
  sentiment-patterns.json      Frustration-detection regex list

copilot-harness/memory/
  memory_loader.py             Read API: get_tier1_index, get_tier2_entry,
                                query_sessions
  session_distiller.py         Write API: append_pattern, compact, four
                                trigger entry points
  pattern_detector.py          Frustration regex matcher

copilot-harness/server.py      MCP tools: harness_get_memory_context,
                                harness_get_memory_entry,
                                harness_query_sessions, harness_compact_memory
```
