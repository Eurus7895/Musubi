# GUI/CLI Orchestrator Token Economics

## Context

Musubi currently mixes two different accounting concepts:

- provider token usage, which is the measurable input, cached-input, and
  output volume reported for each LM call; and
- credits, a Musubi-local conversion based on a price table.

Credits are not a provider billing unit. Their price table can drift, the
existing `--max-credits` flag is already deprecated and ignored for positive
values, and presenting the result beside token usage makes an estimate look
like an invoice. Replay and seed labels have the same usability problem: they
attribute part of input usage to orchestration behavior but do not represent a
separately billed quantity.

The durable economics contract should therefore expose provider token usage
and tool activity only. It should not invent a currency or require operators
to understand internal replay terminology.

## Goals

1. Remove the credit concept from runtime behavior, public APIs, current
   schemas, current documentation, and the Console.
2. Persist one token-economics record per logical agent cycle for both the CLI
   and GUI, including root and child workers.
3. Show input, cached input, output, LM latency, and tool names in the Console
   using the same audit data written by the CLI driver.
4. Preserve append-only historical databases without continuing to read or
   write their legacy credit columns.
5. Keep observability best-effort: an audit failure must not abort an agent
   run.

## Non-goals

- Reconstructing a provider invoice or displaying money.
- Separating replay, system-prompt, tool-catalog, or accumulated tool-result
  tokens inside provider input usage.
- Changing the existing token budget, per-worker output ceiling, cycle limit,
  worker-count ceiling, or continuation policy.
- Rewriting historical audit rows solely to remove an unused column.

## Decisions

### Token usage is the only economics unit

Each logical cycle records:

- `tokens_in`
- `cached_input_tokens`
- `tokens_out`
- `token_source`: `provider` when usage came from the router response, or
  `estimated` when Musubi had to use its deterministic character heuristic
- `lm_ms`
- tool names requested by the model

`cached_input_tokens` is a subset of `tokens_in`, not an additional amount.
The Console must label it accordingly. It must not calculate currency or call
any token subset an actual charge.

When effort escalation makes more than one vendor call inside one logical
cycle, the cycle row stores the sum across those calls. This preserves the
existing cycle abstraction while keeping total token volume accurate. The
tool-name list describes the final response whose tools were eligible for
dispatch.

The forced no-tools final answer after exhaustion is also an LM call. It gets
its own cycle row with an empty tool list so totals include it.

### Credit removal is logical, not historical destruction

Remove all live credit behavior:

- the `--max-credits` CLI option and `run_agent(max_credits=...)` parameter;
- credit rates, estimators, enforcers, errors, stats, and log fields;
- credit parameters and aggregate tools in the MCP surface;
- credit fields from session status and current schema definitions;
- credit reads, writes, tests, and current user documentation.

New databases do not create credit columns. Existing databases may retain
legacy columns and values. Musubi neither migrates them forward nor destroys
them. This preserves append-only audit history and avoids a SQLite table
rebuild whose only effect would be cosmetic.

Historical plan documents may retain the word when it is part of past
evidence. They are not a live product contract.

## Architecture

### Driver collection

The shared `run_unit` / `_run_loop` path remains the single collection point
for root and child workers. After each model cycle it already has:

- aggregated usage from `_cycle_token_counts`;
- cached-input usage from the router response;
- elapsed LM milliseconds;
- the final response's tool-use blocks; and
- the audit DB path and orchestration session identity.

A small best-effort recorder writes these values to `agent_cycles`. Worker
identity is stored explicitly so the Console can distinguish the root from
children without parsing human-readable stderr labels. Pipeline stages use
the same shared worker loop and therefore follow the same path.

The recorder runs after usage is known. It never performs an LM call and does
not move model access outside `LMRouter`, preserving HI #1.

### Audit schema and compatibility

`agent_cycles` is extended as the durable per-cycle source with fields for:

- worker identity;
- input, cached-input, and output tokens;
- token source; and
- tool names as JSON.

The existing session, cycle index, timestamps, latency, text size, and status
fields remain. Old databases are migrated additively with safe defaults.
Readers tolerate missing new columns by selecting defaults, matching the
existing `agent_turns` compatibility pattern.

The previous generic `tool_calls_json` payload is normalized at the boundary:
new writers store a JSON array of names. Readers defensively treat malformed
or legacy JSON as an empty list instead of failing the entire Console state
load.

### Console projection

The Rust data layer loads recent `agent_cycles` rows and exposes them in the
application state. The JavaScript view model filters rows by the currently
viewed orchestrator or pipeline session and derives:

- total input tokens;
- cached input as a labeled subset of input;
- total output tokens;
- number of audited cycles;
- accumulated LM latency; and
- tool-name counts.

The run summary presents these values compactly. Cycle detail remains
inspectable without parsing stderr. Empty or legacy runs render zeros and an
empty tool list rather than an error.

No replay, seed, money, or credit label is displayed.

## Data Flow

1. The shared worker loop fits context and calls the configured `LMRouter`.
2. The router response supplies usage when the provider exposes it; otherwise
   Musubi uses its existing deterministic token estimate.
3. `_cycle_token_counts` aggregates effort attempts for the logical cycle.
4. The loop extracts tool names from the final response.
5. The best-effort recorder appends one `agent_cycles` row.
6. The Console's Rust reader loads compatible rows from the audit database.
7. The view model groups rows under the selected session and renders token and
   tool totals.

## Failure Handling

- Audit insert failures are logged and ignored; the model result still
  proceeds.
- Missing provider usage produces `token_source=estimated` rather than a
  misleading provider label.
- Cached input is clamped to the inclusive range `0..tokens_in`.
- Invalid tool JSON produces an empty tool list in the Console.
- Missing columns in a pre-migration database produce default values.
- A failed or empty forced-final call still records its measured usage when a
  response was received; preflight failures that make no LM call produce no
  cycle row.

## Testing

### Python

- Token accounting distinguishes provider usage from estimated fallback.
- Cached input is aggregated and clamped correctly.
- Root, child worker, pipeline stage, effort escalation, and forced-final
  paths append the expected cycle rows.
- Tool names are persisted without arguments or tool-result payloads.
- Audit failures do not fail the agent loop.
- Credit CLI options, runtime APIs, MCP tools, status fields, and schema
  definitions are absent.
- Existing databases with legacy credit columns remain readable.

### Rust and JavaScript

- The Rust reader loads new cycle fields and defaults safely for older DBs.
- Invalid or legacy tool JSON cannot abort state loading.
- The view model totals input, cached subset, output, cycle count, latency,
  and tool-name counts for the selected session only.
- Orchestrator and Pipeline Studio do not mix cycle rows.
- The UI contains no replay, seed, money, or credit economics labels.

## Documentation and Roadmap

Update `README.md`, `docs/guide.md`, `CLAUDE.md`, and `docs/roadmap.md` so token
counts are the only live economics contract. Move GUI/CLI orchestrator token
economics to completed only after the focused Python, Rust, JavaScript, and
build verification passes.

## Tier and Invariant Fit

Per-cycle token and tool audit is substrate: it remains useful across models
and providers. The Console is a projection of that audit, not a second source
of truth. Removing price-derived credits makes the substrate more stable by
eliminating a vendor-price policy disguised as measurement.

No substrate component makes an LM call, no tool policy is relaxed, existing
audit history is preserved, and no new ephemeral prompt structure is added.
