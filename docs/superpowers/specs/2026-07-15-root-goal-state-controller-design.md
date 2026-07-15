# Root Goal-State Controller Design

**Date:** 2026-07-15

**Status:** Approved for implementation

## Context

The NYC dashboard trace used 66,791 tokens for a task whose artifact existed
after 20,887 tokens. Root alone consumed 10,598 raw tokens across two calls.
More than 93% of each root input was cache-read stable context: the full root
prompt, tool schemas, conversation history, and replayed worker feedback. The
false escalation at the worker turn cap is fixed, but the root loop still acts
as a high-volume message bus rather than a compact goal owner.

Musubi needs root to retain the user's intent and make adaptive decisions from
worker feedback. It must not achieve lower token use by removing root's semantic
responsibility or by moving intent interpretation into the deterministic
substrate.

## Goal

Make the standalone root a compact **Goal-State Controller** that:

- owns the original user intent and terminal success decision;
- receives bounded, structured feedback from every direct worker;
- chooses the cheapest next action that closes an unresolved goal gap;
- carries only goal state and the latest outcome delta between decision calls;
- exposes only tools relevant to the current decision phase; and
- makes no additional LLM call solely to transform already-known state.

For a successful low-risk simple artifact, root should use at most two model
decisions and target at most 3,000 total root tokens. The whole task targets
8,000-12,000 tokens and treats 20,000 as a performance-regression guard, not a
normal budget.

## Non-goals

- Do not add an LLM call to extract acceptance criteria.
- Do not add or change a database schema in this first increment.
- Do not make the regex scope classifier the authority for task completion.
- Do not let the substrate interpret semantic user intent.
- Do not change pipeline invocation policy or pipeline stage sequencing.
- Do not relax root's read-only policy, worker firewalls, or the cumulative
  three-direct-worker ceiling.
- Do not persist GoalState across separate CLI processes yet. Conversation
  persistence remains unchanged; this increment governs one root run.

## Architecture

### 1. GoalState

Create `musubi/agent/goal_state.py` with an in-memory `GoalState` owned by the
root `Orchestration` object. It contains only compact control state:

```python
@dataclass
class GoalState:
    intent: str
    scope: str
    route: str
    root_token_target: int
    root_calls: int = 0
    root_tokens_in: int = 0
    root_tokens_out: int = 0
    outcomes: list[OutcomePacket] = field(default_factory=list)
```

`intent` is the exact current user task, not a deterministic reinterpretation.
Scope and route remain advisory labels. The state records terminal worker
feedback and root economics; it does not contain source files, raw tool output,
or the full conversation transcript.

The root system prompt remains stable and cacheable. GoalState is rendered as a
short user decision block on calls after worker completion:

```text
[root-goal-state]
intent=...
scope=...
route=...
root_usage=calls:1,input:...,output:...,target:3000
latest_worker=...
decision=Compare the latest evidence with the original intent. Stop if the
goal is satisfied; otherwise summon only the cheapest worker needed for the
remaining gap.
[/root-goal-state]
```

### 2. OutcomePacket

`OutcomePacket` is the compact root-facing projection of the existing terminal
`WorkerOutcome`. The full verified summary remains in audit/storage. Root sees:

```python
@dataclass(frozen=True)
class OutcomePacket:
    role: str
    status: str
    summary: str
    touched_files: tuple[str, ...]
    verification: str | None = None
    remaining_gap: str | None = None
```

The projection is deterministic and zero-LLM. It parses the shipped worker
output contract fields (`status`, `summary`, `verification`) when present,
falls back to the verified terminal summary, collapses whitespace, and caps
each root-facing free-text field. Truncation is explicit. It never changes the
server-decided terminal status.

Worker prompts gain optional `remaining_gap:` output. Workers recommend; root
decides. Missing structured fields must remain backward compatible.

### 3. Delta-only root context

The existing loop appends every root assistant tool call and tool result to the
same conversation. After a direct worker reaches terminal status, the next root
decision instead receives a fresh compact message list:

```text
stable root system prompt
current GoalState block with the latest OutcomePacket
```

The original intent survives verbatim in GoalState. Prior worker summaries and
raw tool results remain available in audit and the append-only stores but are
not replayed automatically. Recovery retains the latest failed packet,
including touched files and the bounded failure summary.

Compaction occurs only after a terminal `musubi_spawn_subagent` result at root
depth zero. Ordinary read/retrieve analysis cycles and nested workers retain
their existing message behavior.

### 4. Phase-specific root tool surface

The MCP server continues exposing the normal `agent` catalog. The driver narrows
what each root decision call sees:

- **Simple initial decision:** `musubi_spawn_subagent` only.
- **Medium/large initial decision:** spawn plus skill discovery/loading tools.
- **Post-worker decision:** spawn plus skill discovery/loading tools.
- **Unrecovered failure analysis:** preserve the existing recovery behavior;
  the two-cycle window may use the normal read/retrieve surface before it
  becomes spawn-only.
- **Nested workers and pipeline stages:** unchanged.

This changes model-visible schemas, not policy. A tool hidden in one phase is
not newly authorized elsewhere. The root may still answer directly without a
tool call.

### 5. Root optimizer contract

The stable root prompt states the decision order concisely:

1. Compare terminal evidence with the exact user intent.
2. Stop when the goal is satisfied; do not spawn optional enhancement work.
3. Identify one unresolved gap that blocks completion.
4. Prefer deterministic evidence already supplied by the harness.
5. If model work is required, summon the cheapest role with authority to close
   that gap; parallelize only independent gaps.
6. Replan when worker feedback contradicts the expected strategy or the root
   token target is exceeded.
7. Ask the user when an unresolved choice would change intent or acceptance.

The prompt never tells root to trust a worker's `done` claim blindly. Mechanical
status comes from the substrate; semantic acceptance stays with root.

## Data flow

```text
user task
  -> deterministic scope hint
  -> GoalState(intent=user task)
  -> compact root decision surface
  -> direct worker
  -> server-verified terminal status
  -> OutcomePacket projection
  -> GoalState reducer
  -> compact root delta decision
  -> final answer or one bounded next worker
```

Medium and large direct runs use the same loop. Their extra cost comes from
meaningful planner/designer/coder/reviewer decisions, not from replaying every
previous message. User-invoked pipelines keep their deterministic runner.

## Failure handling

- A failed context fetch records a failed OutcomePacket exactly as today.
- Failed/escalated outcomes preserve touched files and enter the existing
  bounded replacement path.
- Goal-state compaction must not mark a failure recovered; only a later
  same-role `done` outcome does that.
- Malformed worker output falls back to a bounded plain summary.
- If root exceeds its token target, the run is not killed solely for that
  reason. The goal block marks the overage so root must avoid optional work;
  the existing hard `TokenBudgetEnforcer` remains authoritative.
- Audit writes remain best-effort and must not prevent a result.

## Observability

Use existing `agent_cycles` rows for provider usage. Add no schema. Tests and
logs derive root economics from root cycles and GoalState. Log one concise line
when a terminal worker causes context reduction:

```text
[agent] root goal-state compacted outcomes=1 chars=... tools=...
```

No worker summary content is added to logs beyond existing behavior.

## Testing

Add pure tests for OutcomePacket parsing, bounding, GoalState reduction, and
prompt rendering. Add loop regressions proving:

- a simple initial root call sees only `musubi_spawn_subagent`;
- medium/large decisions retain skill tools without read/write execution tools;
- after a worker outcome, the next root call contains exact user intent and the
  compact latest feedback but not an earlier large raw tool result;
- a successful worker can be semantically accepted by root without another
  worker;
- a failed worker still follows the current bounded recovery path;
- root economics count root cycles only, not worker cycles; and
- the estimated simple root input stays below the 3,000-token target in the
  synthetic regression fixture.

Run the focused agent tests, the full Python suite, `git diff --check`, and any
existing static checks used by the touched modules.

## Acceptance criteria

- Root owns exact user intent for the whole direct run.
- Every direct terminal worker produces one compact OutcomePacket.
- The next root decision uses delta-only GoalState context after worker
  completion.
- Simple initial root decisions expose only the spawn tool.
- Non-simple root decisions retain only spawn and skill tools unless recovery
  explicitly opens read/retrieve analysis.
- Root token accounting excludes worker cycles.
- Existing recovery, firewalls, append-only audit, and worker ceilings remain
  unchanged.
- No new LLM calls, database tables, or semantic decisions in the substrate.
- Focused tests and the full Python suite pass.
