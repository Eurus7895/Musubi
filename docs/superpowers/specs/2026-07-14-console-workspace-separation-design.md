# Console Workspace Separation Design

## Decision

The Console has two distinct workspaces with one owner each:

- **Orchestrator owns execution.** It starts direct workers or an explicitly
  selected registered pipeline, retains the durable conversation, displays
  runtime topology and evidence, and owns cancellation.
- **Pipeline Studio owns recipes.** It creates, edits, validates, and saves
  deterministic pipeline YAML. It never starts a model process and has no chat
  or run-history surface.

This separation keeps Musubi's knot metaphor literal: Studio ties a recipe;
Orchestrator pulls that knot through the governed runtime.

## Orchestrator Information Architecture

- The left rail owns durable sessions and may collapse.
- The center owns Direct/Pipeline launch controls, status, minimal graph, and
  node-filtered evidence logs.
- The right rail owns Conversation, narrative summaries, artifacts, and the
  skills proven by successful audit events; it may collapse.
- Graph nodes contain identity, status, parent, timing, and counts only. Edges
  mean “summoned” and do not imply sequential or parallel execution.
- Selecting a node opens its tools, skills, policy, model, and lifecycle events
  without repeating its narrative summary.
- Raw tool arguments and results never cross the Rust-to-React boundary.
  Ambiguous evidence remains unassigned rather than being guessed.

## Pipeline Studio Information Architecture

Studio follows four guided steps: Basics, Stages, Handoffs, and Validate.

- Basics owns recipe name and metadata.
- Stages is the single ordered primary lane. Presets and agents may be dragged
  into it, reordered, selected, and removed.
- Handoffs shows the sequential backbone plus each stage's `spawns` allowlist.
  Primary stages are sequential; nested siblings run in parallel only when the
  same worker turn actually summons them.
- Validate owns findings and the complete recipe preview. Save is enabled only
  for a safe, fully resolved, valid draft and writes atomically beneath
  `.github/pipelines/<safe-name>/pipeline.yaml`.

Agent prompts, tools, skills, turn caps, and output budgets remain catalog-owned
read-only contracts. A recipe may own stage order, stage aliases, preset/agent
references, and nested spawn allowlists.

## Compatibility and Safety

- Opening either workspace is passive; only an explicit Orchestrator submit
  can launch the standalone driver.
- Direct is the default mode. Pipeline mode fails closed until a runnable saved
  recipe is selected.
- Direct and pipeline runs use the same durable Orchestrator chat identity and
  exact shared-process ownership rules.
- Historical `surface = 'pipeline'` chat rows remain readable, but no active
  Studio mutation creates more of them.
- Recipe paths reject traversal and symlink escape, validation precedes write,
  and replacement is same-directory and atomic.
- Policy, evaluator, spawn, audit, and append-only stage invariants do not
  change.
