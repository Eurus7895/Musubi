# GUI Pipeline Studio Sessions and Direct Runs Design

> Superseded by
> [`2026-07-14-console-workspace-separation-design.md`](./2026-07-14-console-workspace-separation-design.md).
> Pipeline Studio is now builder-only; Orchestrator owns pipeline execution.

## Context

The desktop GUI currently presents Pipeline Studio as a separate surface, but
the separation is incomplete:

- `send_pipe_chat` launches the ordinary root agent with
  `--tool-surface agent`. The root prompt explicitly prohibits pipeline
  invocation, so asking Studio to run a pipeline produces a conversational
  refusal instead of a pipeline run.
- The selected Studio preset and composed steps are client-only state. They are
  never sent to the backend or the standalone runner.
- Run scoping checks only the `gui-pipeline-` chat ID prefix. It mixes old and
  current Studio sessions after New session.
- `subagent_audit` stage workers belong to the child pipeline session, while
  `agent_turns` maps only the outer parent session to a GUI chat ID. The current
  one-hop join therefore leaves actual pipeline stages unowned; they fall back
  to the Orchestrator surface.
- Clear chat, clear session view, and New session overlap. The three controls
  obscure the single action that actually creates an isolated context.

## Goals

1. Treat every submitted Pipeline Studio message as the brief for the selected
   registered pipeline and run it immediately.
2. Keep Pipeline Studio and Orchestrator conversations, current sessions, run
   lists, process ownership, and cancellation controls independent.
3. Make Studio runs derive from real pipeline envelopes and child stages rather
   than from flattened agent turns.
4. Make New session the only session-reset action on both surfaces and apply the
   selected accent-pill design consistently.
5. Preserve the shared single-process slot and the global append-only Audit.

## Non-goals

- Do not add model-driven pipeline routing. Pipeline selection is explicit and
  deterministic.
- Do not build a second pipeline engine in Rust or React.
- Do not make an edited client-only composition executable. It must first exist
  as a registered workspace pipeline.
- Do not delete durable `conversation_messages`, agent turns, pipeline runs, or
  audit events. The GUI-only `chat_log` remains a current-surface projection and
  is cleared when that surface starts a new session.
- Do not add a session-history browser. New session changes the current view;
  durable history remains available through Audit.

## Chosen Approach

Pipeline Studio will call the existing deterministic standalone entry point:

```text
agent "<brief>" --pipeline <pipeline-name> --chat-id <pipeline-chat-id>
```

The backend owns validation and launch construction. The frontend sends a
typed request containing the brief and selected registered pipeline name. The
root model does not decide whether to summon a pipeline, and
`musubi_spawn_pipeline` does not need to be exposed on the ordinary agent tool
surface.

The Orchestrator continues to launch the ordinary agent without `--pipeline`.
Both surfaces share the existing process slot.

## Pipeline Catalog and Composer State

The GUI must distinguish a registered pipeline from a client-side composition.

- The backend returns registered deterministic pipelines from the workspace
  pipeline catalog, including name, description, and ordered stages.
- The first supported catalog is `feature-dev` and `dev-lite`, matching the
  standalone linear pipeline runner. `code-review` remains unavailable for
  direct Studio execution until its per-file fan-out is supported by that
  runner.
- Loading a registered pipeline sets `pipeName`, replaces Studio steps with the
  backend-provided stage order, and marks the composition runnable.
- Adding, removing, or moving a stage marks the composition modified and not
  runnable. The input and Run action are disabled with: `Save this composition
  as a registered pipeline before running.`
- Stale frontend-only presets such as `bugfix` and `explore` are not presented
  as runnable workspace pipelines.

This prevents the UI from promising execution for a recipe the backend cannot
resolve.

## Session Model

Isolation here is logical, not filesystem-level. All sessions belonging to the
same canonical project root share one workspace, dependency environment,
`musubi.db`, and `audit.db`. Musubi never creates a per-session directory,
worktree, repository clone, virtualenv, or container. The project retains one
shared child-process/writer slot; exact session IDs isolate conversation,
runtime ownership, budget, logs, and pipeline ancestry.

The backend state includes the exact current session identifiers:

```text
orchestratorChatId: gui-orchestrator-<workspace-hash>-<nonce>
pipelineChatId:     gui-pipeline-<workspace-hash>-<nonce>
```

Frontend run scoping compares complete chat IDs. Prefix checks are retained
only as a backward-compatible classification fallback for rows that predate
the exact-session fields.

New session performs the same operation independently for either surface:

1. Refuse while the shared process slot is running.
2. Mint and persist a new nonce.
3. Replace only that surface's current chat ID.
4. Clear only that surface's visible `chat_log` rows and local draft/selection.
5. Reset its current run list because no historical run has the new exact chat
   ID.

Historical database records remain untouched.

## Run Ownership and Grouping

Studio runs use the existing audit graph instead of treating every agent turn
as a pipeline run:

```text
agent_turns.parent_session_id
  -> subagent_audit pipeline envelope
       handle_id = pipeline_runs.session_id
  -> subagent_audit stage workers
       parent_session_id = pipeline_runs.session_id
```

The Rust data reader builds pipeline run view rows by:

1. Loading `pipeline_runs` as the authoritative run envelopes.
2. Locating the envelope spawn row whose `handle_id` equals the pipeline
   session ID.
3. Resolving the envelope's outer `parent_session_id` through `agent_turns` to
   the exact GUI chat ID.
4. Attaching workers whose `parent_session_id` equals the pipeline session ID.
5. Returning the pipeline name, brief, start/end time, final status, and ordered
   workers as one run.

The deterministic runner must finalize every pipeline envelope. On success it
calls `musubi_finalize_pipeline_run(..., final_status="success")`; on a stage,
policy, cancellation, or budget failure it finalizes as `escalated` or
`aborted` before propagating the error. Finalization also appends the matching
terminal completion event for the `pipeline:<name>` envelope in
`subagent_audit`, so neither Studio nor Audit leaves a completed pipeline
looking permanently active.

Pipeline envelopes and their descendant workers do not appear in the
Orchestrator run list. Orchestrator runs remain grouped from non-pipeline root
agent turns and their direct worker descendants. Audit remains global and
continues to show every spawn and completion event.

During the short interval before the database contains a pipeline envelope,
the frontend creates one live Studio run from `driverStatus` only when
`driverStatus.surface === "pipeline"`. The live run is replaced by the real
pipeline run after the next backend snapshot.

## Backend Launch Flow

`send_pipeline_task` accepts exactly two user-controlled strings:

```text
brief: non-empty task text
pipelineName: registered deterministic pipeline name
```

Before inserting the user chat message or spawning a child process, the backend
validates:

- no process is already running;
- the brief is non-empty;
- the pipeline name contains only lowercase letters, digits, and hyphens;
- the pipeline exists in the backend catalog;
- the pipeline resolves to at least two ordered stages; and
- the pipeline is supported by the deterministic runner.

On success it launches the existing agent CLI with the Pipeline Studio chat ID
and `--pipeline`. The runtime records `surface = "pipeline"`, pipeline name,
brief, and start time so state polling can render an immediate live card.

The Orchestrator launch path uses the same lower-level process function without
a pipeline argument. This keeps process pumping, cancellation, bounded logs,
artifact links, profile selection, and error formatting shared.

## UI Design

### Session controls

Both chat headers use the approved accent pill:

```text
[ +  New session ]
```

- Height: 32 px.
- Rounded 9 px corners.
- Amber translucent background and amber border.
- Plus icon followed by the visible `New session` label.
- Disabled styling while any surface owns the process slot.

Remove Clear chat and the secondary clear/session-view controls from Pipeline
Studio and Orchestrator. Pipeline Studio chat remains part of the Studio layout;
it does not need a separate close button.

### Pipeline submission

- Header subtitle: `<pipeline-name> · isolated session`.
- Input placeholder: `Describe the task for <pipeline-name>…`.
- Idle submit button semantics: `Run pipeline`.
- Running owner semantics: `Cancel pipeline`.
- When Orchestrator owns the slot, Studio input is disabled with
  `Orchestrator run is active…`; the reciprocal behavior remains on
  Orchestrator.
- When the composition is modified/unregistered, input and submit are disabled
  with the save-as-pipeline message.

### Studio runs

Each card shows:

- chronological run number;
- pipeline name;
- truncated user brief;
- `N stages`;
- current/final status; and
- current stage when running.

Selecting a card shows its ordered stage timeline. Studio never labels a run
as `driver-only turn`; a pipeline launch that fails before an envelope exists is
shown as a failed system message rather than a fabricated pipeline run.

## Error Handling

- Validation failures are inserted as Pipeline Studio system messages with a
  deny tone. They do not add a user message, create a run, or launch a model.
- Process-spawn failures are written to Pipeline Studio chat and clear the live
  runtime state.
- Non-zero pipeline exits use the existing concise failure summary and process
  log link. A real envelope that was created remains visible with its audit
  status.
- Policy or stage rejection returns a non-zero exit through the existing
  `strict=True` pipeline runner path.
- Once a pipeline envelope exists, every exit path finalizes both
  `pipeline_runs` and the envelope's append-only completion audit event.
- New session remains unavailable until the process ends or is cancelled.
- A malformed or missing optional historical table produces an empty Studio run
  list, not a fallback into Orchestrator.

## Test Strategy

### Frontend Node tests

- Studio submit sends brief plus selected registered pipeline name.
- Modified/unregistered compositions disable submit.
- Exact chat ID scoping excludes older Studio and Orchestrator sessions.
- Live runs appear only on the owning surface.
- Studio cards contain pipeline name/stage status and never `driver-only turn`.
- Both headers expose only the approved New session action.

### Rust data tests

- Registered catalog loading returns ordered `feature-dev` and `dev-lite`
  stages and excludes unsupported entries.
- Pipeline launch specs add `--pipeline <name>` only for Studio.
- Invalid names, missing pipelines, empty briefs, and single-stage recipes fail
  before process launch.
- Pipeline envelope ancestry resolves the exact GUI chat ID and attaches stage
  descendants.
- New session updates only the chosen exact chat ID.

### Python regression tests

- Existing direct `run_agent(..., pipeline=...)` stage-order and evaluator
  firewall tests remain green.
- Invalid deterministic pipeline invocation exits non-zero without being
  misreported as a successful answer.
- Success, stage rejection, budget exhaustion, and cancellation finalize the
  pipeline envelope exactly once with the appropriate terminal status.

### Verification

- Run focused Node, Rust, and Python suites.
- Run the complete GUI data tests and Rust crate tests.
- Build the production GUI.
- Run `git diff --check`.
- Visually verify Pipeline Studio and Orchestrator headers, disabled states,
  fresh-session empty lists, direct pipeline launch, run timeline, and failure
  rendering.

## Invariants

- The substrate makes zero LLM calls; the existing standalone driver remains
  the only model inject point.
- Pipeline selection is explicit and user-invoked.
- The evaluator sees only the prior stage output.
- Policy stays fail-closed.
- Stage and worker audit remains append-only.
- Every pipeline and stage spawn remains visible in Audit.

## 2026-07-12 Runtime Follow-up

The approved follow-up design is
[`2026-07-12-project-session-and-pipeline-runtime-design.md`](./2026-07-12-project-session-and-pipeline-runtime-design.md).
It makes exact process ownership part of the session contract and bounds each
standalone pipeline stage without introducing per-session filesystem state.
