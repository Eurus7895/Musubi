# Memory in Musubi

> How the harness remembers things — across stages within a session, across
> sessions, and across continuous agent conversations.

This is the contract. Implementation lives in `musubi/memory/`.

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
       — failure-patterns.md   musubi_get_memory_entry(name).
                                Distilled decisions and recurring failures.

Tier 3 — sessions DB           Cross-session substring search via
                                musubi_query_sessions(query).
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
| `agent` (new) | ✅ auto-injected | on demand | on demand |
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

1. Agent calls `musubi_read_stage(stage)`.
2. Harness builds the per-agent context (firewall).
3. Harness appends `tier1_index` + `tier2_available` (skipped for reviewer).
4. Agent may then call `musubi_get_memory_entry(name)` to pull a Tier-2 file,
   or `musubi_query_sessions(query)` to search Tier 3.

### Write — distillation triggers

`failure-patterns.md` (Tier 2) gets new entries from up to four triggers. All
shipped triggers funnel through
`session_distiller.append_pattern(pattern, source)` which deduplicates at
append time; drivers reach it through the `musubi_append_failure_pattern`
MCP tool.

| Trigger | When it fires | Status | Captures |
|---|---|---|---|
| **Reviewer fail** | A `reviewer` / `reviewer-aux` sub-agent returns `final_status='failed'` | ✅ shipped (Phase C.2) | Role + failure cause |
| **User frustration** | Deterministic regex match on negative-sentiment patterns in the user message — no LLM | ✅ shipped (Phase C.2) | `frustration:<label>` pattern keyed off the matched phrase |
| **Per-turn (gated)** | After every agent reply, only if a noteworthy event happened (sub-agent failed, retry occurred, spawn cap hit) | ⏳ deferred — overlaps with reviewer-fail | Turn summary + flagged event |
| **Chat closed** | Chat/session ends | ❌ retired — it targeted the VS Code chat lifecycle, and the extension host was removed | Final sweep — anything not yet distilled |

Persistent dedup runs through
`session_distiller._load_existing_patterns`.

**Why deterministic frustration detection.** Hard Invariant #1: zero LLM calls
inside the harness. Frustration patterns live in
`.github/memory/sentiment-patterns.json` as a configurable regex list, matched
by `musubi/memory/pattern_detector.py`.

### Compact

`musubi_compact_memory` runs when `failure-patterns.md` exceeds 5 KB. Keeps the
union of (top-10 most-frequent, top-10 most-recent) and drops the rest. Same
function is invoked manually via the MCP tool.

### Dedup

`append_pattern` checks for existing entries with the same
`(error_signature, root_cause)` and increments their `frequency` counter
instead of writing a duplicate. `frequency` feeds the compaction ranking.

---

## Agent integration

> This section originally described the embedded VS Code extension agent
> (feature-frozen May 2026, then removed — see
> [`docs/roadmap.md`](./roadmap.md) § Completed Tracks, "VS Code extension
> removal"). It now describes the standalone `agent` host, which reuses the
> same storage contract.

The agent runs as a continuous conversation. "Session" is redefined for
this mode:

> **Session = one user turn.** Many sessions chained make one chat.

This keeps best-practice 8 ("one task per session") honest: each user turn is
one task. The agent's persistent context across turns is held by the
`conversation_messages` SQLite table (Phase C.1), keyed by `chat_id`, not by
extending the session abstraction. The `chat_id` is user-supplied: pass
`--chat-id <id>` to the standalone CLI to persist turns and replay bounded
history on the next run.

**Tier 1 is auto-injected into the agent on every turn** — same path as
pipeline agents.

**Tier 2 / Tier 3 are pulled by the agent on demand** via the existing
MCP tools.

**Sub-agents the agent spawns get nothing.** Same firewall as pipeline
sub-agents.

---

## Conversation transcript (agent only)

Separate from memory; documented here because they are easily confused.

| | Memory (3 tiers) | Conversation transcript |
|---|---|---|
| Lives in | `.github/memory/` (Tier 1, 2) + DB sessions table (Tier 3) | `storage/audit.db::conversation_messages` (Phase C.1), keyed by `chat_id` |
| Persists across | All sessions, all chats | One chat (one `chat_id`) |
| Granularity | Distilled patterns, decisions | Full message history (`user` / `assistant` / `tool` / `system`) |
| Loaded into | Agent context as injected fields | LLM call as message array via `musubi_get_conversation` (token-budgeted, newest-first truncation) |
| Compacted by | `musubi_compact_memory` (5 KB cap) | `musubi_get_conversation` newest-first truncation (token-budgeted, default 50k — `session/conversations.py::DEFAULT_MAX_TOKENS`) plus driver-side `fit_context` under `MUSUBI_CONTEXT_BUDGET` |

The transcript is replayed verbatim (subject to compaction) to give the
agent continuity. Memory is consulted for cross-conversation knowledge.

---

## What memory is NOT for

- **Caching answers to user questions** — that's the conversation transcript.
- **Storing user preferences for tone/style** — those go in `.github/instructions/` (always-loaded), not memory (failure-pattern-shaped).
- **Project documentation** — that's `docs/`. Memory is *operational learnings*, not reference material.
- **Per-session scratch space** — that's the session's own DB rows (`stage_outputs` attempts). Memory persists across sessions.

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

musubi/memory/
  memory_loader.py             Read API: get_tier1_index, get_tier2_entry,
                                query_sessions
  session_distiller.py         Write API: append_pattern, compact, four
                                trigger entry points
  pattern_detector.py          Frustration regex matcher

musubi/server.py      MCP tools: musubi_get_memory_context,
                                musubi_get_memory_entry,
                                musubi_query_sessions, musubi_compact_memory,
                                musubi_append_failure_pattern (Phase C.2),
                                musubi_append_message,
                                musubi_get_conversation (Phase C.1)
```
