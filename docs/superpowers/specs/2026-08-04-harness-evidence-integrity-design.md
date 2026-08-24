# Harness Evidence Integrity Repair Design

**Status:** Approved for implementation on 2026-08-04

## Decision

Repair three review findings without adding task-meaning heuristics:

1. Root instructions cite only tools that still exist and route every worker
   flow through the current planning contract.
2. Policy decisions receive `request_id` and `parent_session_id` at the append
   boundary. The Console may join a verdict only through that durable identity
   or an existing worker handle. Legacy rows remain visible in the policy
   ledger but stay unattributed in request-scoped evidence.
3. When durable stage state is configured, every checkpoint requires its
   append-only attempt row. A missing row raises a typed runtime failure before
   the pipeline can continue; explicitly storeless runs remain supported.

## Invariants

- The model still selects scope, worker chain, skill, and acceptance predicates.
- The harness validates and records; it does not infer provenance or task size.
- Existing audit rows are never rewritten.
- A missing checkpoint target cannot degrade into an unaudited successful run.

## Verification

- A repository cross-reference test rejects prompt references to absent MCP
  tools.
- Python, Rust, and JavaScript regressions cover policy identity from write to
  request projection, including legacy-schema compatibility.
- Checkpoint helper regressions prove missing state raises instead of returning.
