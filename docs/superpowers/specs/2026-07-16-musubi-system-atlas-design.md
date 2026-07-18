# Musubi System Atlas Design

## Context

Musubi spans a standalone model driver, a zero-LLM governance substrate,
deterministic pipelines, worker nesting, policy and evaluator firewalls,
skills, memory, compression, durable audit, and a native Console. The current
documentation accurately describes these pieces in isolation, but a maintainer
must still reconstruct the system-level mental model across `AGENTS.md`,
`CLAUDE.md`, the roadmap, Python source, Rust projection code, React views, and
database schemas.

That reconstruction is particularly difficult because Musubi deliberately
contains both durable substrate and temporary structures that compensate for
current model limits. Historical designs also remain useful for explaining why
the current boundaries exist, even when they no longer describe active runtime
behavior.

This feature produces one self-contained interactive HTML atlas that teaches
the current system, its causal design rationale, its evolution, and its likely
dissolution path. It is a learning artifact, not another source of runtime
configuration.

## Audience

The primary reader is a Musubi maintainer who can read Python, Rust,
JavaScript, YAML, and SQLite schemas. The atlas assumes general software
architecture knowledge but does not assume prior knowledge of Musubi.

The reader should finish with enough understanding to:

- trace a request from CLI or Console submission through the driver, workers,
  governance boundaries, model router, storage, and UI projection;
- explain why every major component exists and what would break if it were
  removed;
- distinguish model behavior from driver behavior and substrate enforcement;
- identify which costs are tokens, cycles, wall-clock time, storage, or
  maintenance complexity;
- distinguish the driver/substrate trust boundary from the independent
  durable/ephemeral lifecycle axis, then name the trigger that permits each
  temporary structure to disappear;
- predict the audit evidence and failure behavior of common runtime scenarios;
- verify those claims against the repository rather than trusting prose alone.

## Goals

1. Present a coherent system map before exposing file-level detail.
2. Decompose Musubi into components with consistent responsibilities and
   interfaces.
3. Explain the causal reason for every component, not only what it does.
4. Show direct, parallel, nested, pipeline, policy-denied, budget-exhausted,
   and Console-observation flows step by step.
5. Explain all active Hard Invariants and identify their enforcement points.
6. Make token and runtime economics attributable to the component and model
   call that caused them.
7. Tell the full architectural evolution without presenting superseded
   behavior as current fact.
8. Include an interactive scored quiz with answer explanations and chapter
   references.
9. Keep the result portable as one HTML file with no network dependency.

## Non-goals

- Replace `AGENTS.md`, `CLAUDE.md`, `docs/roadmap.md`, source comments, or
  schemas as canonical sources.
- Execute Musubi, mutate repository state, query live databases, or call a
  model from the atlas.
- Teach basic Python, Rust, React, SQLite, or LLM concepts.
- Promise that line numbers remain valid after the source snapshot used to
  generate the atlas.
- Turn unresolved backlog proposals into implied commitments.
- Reproduce every function, test, database column, or historical commit.

## Deliverable

Create `artifacts/musubi-system-atlas.html` as a single UTF-8 HTML document.
All CSS, JavaScript, SVG, diagrams, quiz data, and explanatory content are
embedded. The document must open directly from the filesystem without a local
server, CDN, font download, package install, or build step.

The atlas records the source commit hash and generation date in its header.
Every source link displays a repository-relative path and line number from that
snapshot. It must state that later source changes can invalidate those line
numbers.

“Full architectural evolution” means every decision that changed component
ownership, execution topology, enforcement, persistence, model-call economics,
or operator surfaces. It does not mean a commit-by-commit changelog. Minor
fixes appear only when they expose a design assumption or materially change a
boundary.

## Information Architecture

### 1. Orientation

Open with the core statement:

> The driver reasons. Musubi controls the environment.

Define the three load-bearing boundaries before introducing component names:

- **Model:** produces reasoning, text, and tool requests.
- **Driver:** owns model calls, goal state, worker loops, and orchestration.
- **Substrate:** deterministically controls permitted actions, context,
  validation, persistence, and evidence while making zero model calls.

The orientation chapter also defines `surface`, `worker`, `pipeline`, `skill`,
`audit evidence`, `fail-closed`, `substrate`, and `ephemeral`.

### Two independent classification axes

The atlas must not use `substrate` as a single catch-all category. It teaches
two independent axes:

1. **Trust boundary:** `model-calling driver` versus `zero-LLM governance
   substrate` versus `read-only operator projection` versus `external system`.
2. **Lifecycle:** `durable investment` versus `ephemeral compensation`, with
   the repository's `musubi-tier` tag shown separately as implementation
   metadata.

For example, `agent/run.py` is a durable execution primitive but remains on the
model-calling driver side of HI #1. Conversely, a deterministic validator is
both durable and inside the zero-LLM substrate. The map uses shape for trust
zone and line/pattern treatment for lifecycle so color is not overloaded.

“Unified worker model” is defined narrowly: root and child workers reuse the
same `run_unit` execution primitive. They do not have equal authority. Root
alone owns goal state, phase-reduced routing, replacement recovery, and broader
read access; children receive frozen briefs, pushed skills, and restricted
tool/spawn capabilities.

### 2. Interactive System Map

The initial map groups components into six layers:

1. **Operator surfaces:** standalone CLI and native Console.
2. **Driver and orchestration:** root goal-state controller, worker loop,
   nested worker dispatch, pipeline runner, context fitting, and `LMRouter`.
3. **Governance substrate:** MCP server, policy engine, evaluator firewall,
   validator, budget enforcement, and tool dispatch boundary.
4. **Capabilities:** skills, memory, compression, workspace discovery, and
   prompt catalog.
5. **Durable state and evidence:** `audit.db`, `musubi.db`, session artifacts,
   stage attempts, conversations, and lifecycle rows.
6. **Read models:** Rust safe evidence projection and React Console views.

The map supports two overlays:

- **Runtime flow:** highlights request, context, tool, model, result, and audit
  movement.
- **Control boundary:** highlights which component may allow, deny, validate,
  persist, or call a model.

Selecting a component highlights its immediate upstream callers and downstream
consumers. Edges use explicit verbs such as `launches`, `calls`, `gates`,
`injects`, `records`, `reads`, and `projects`; a generic unlabeled arrow is not
allowed.

### 3. Component Atlas

Every component uses the same record shape:

| Field | Meaning |
|---|---|
| Responsibility | The smallest stable job owned by the component |
| Why it exists | The failure or design pressure that justified it |
| Inputs | Data, events, or calls it accepts |
| Outputs | Data, mutations, events, or decisions it produces |
| Called by | Immediate upstream owners |
| Depends on | Required downstream services or data |
| Enforces | Hard Invariants or deterministic rules owned here |
| Failure modes | How it can fail and whether the failure is fail-closed |
| Economics | Token, cycle, LM time, wall-clock, storage, or complexity cost |
| Evidence | Source paths, schema rows, and representative tests |
| Trust zone | Driver, zero-LLM substrate, read projection, or external system |
| Durability | Durable or ephemeral, `musubi-tier` tag, and removal trigger |

The minimum component inventory is:

- CLI entry and setup wizard;
- Console shell, Orchestrator, and Pipeline Studio;
- root goal-state controller;
- worker loop and nested dispatch;
- deterministic pipeline runner and composer;
- model input fitting and replay/context handling;
- `LMRouter` and vendor implementations;
- external MCP federation and its explicit governance boundary;
- MCP server and tool surface;
- policy engine and dispatch validation;
- evaluator firewall and context builder;
- budget and cycle enforcement;
- validation/execution layer;
- skills catalog, recommender, router, and injection path;
- three-tier memory and session distillation;
- reversible compression and compression evaluation;
- workspace discovery;
- session, conversation, stage-attempt, pipeline-run, agent-cycle, and
  subagent-audit persistence;
- Rust database reader and privacy boundary;
- React view model and runtime evidence projection;
- Audit, Policy, Models, Skills, and Settings views.

The final inventory may split a listed item when the source audit proves that
two independent owners or boundaries exist. It may not merge components whose
failure policy, lifecycle, or mutation authority differs.

The atlas states the effective current pipeline-routing rule explicitly: the
normal model-visible root and child tool surfaces do not expose
`musubi_spawn_pipeline`. Whole pipelines are launched explicitly by the user
through CLI `--pipeline` or Orchestrator Pipeline mode. Internal tool and policy
support for pipeline spawning is defense-in-depth/runner machinery, not proof
that the current model may choose that route.

### 4. Execution Trace Lab

The reader can choose a scenario and advance one step at a time. Each step
shows:

- the active component;
- the input received and output produced;
- the control decision made;
- whether an LM call occurs;
- which token/cycle/time budget changes;
- which durable row or artifact is written;
- the next component;
- the fail-closed outcome if the step fails.

Required scenarios:

1. Direct CLI request completed by one worker.
2. Root selects and pushes a skill into a worker.
3. Multiple same-turn workers run concurrently.
4. A worker summons a nested helper.
5. An operator launches a deterministic pipeline.
6. A pipeline stage summons declared helper workers.
7. A tool request is denied by policy before execution.
8. An evaluator attempts to access forbidden context.
9. A worker reaches cycle, output, or total-run budget limits.
10. Context fitting elides replay data while preserving the original dispatch.
11. The Console reads audit/state databases and projects safe runtime evidence.
12. A historical Console session is viewed while another session owns the
    shared driver process.
13. An external MCP server fails to connect or exposes a namespaced tool.

Parallelism must be represented only when the source proves same-turn sibling
dispatch. Pipeline backbone stages remain sequential. A `summoned` edge must
not be used to imply sequential or parallel scheduling by itself.

The external MCP scenario must state that connection failure is isolated and
that external tool calls are outside the Musubi-owned policy/audit boundary.
The phrase “every agent-tool boundary” is used only when qualified as every
Musubi-owned tool boundary.

### 5. Governance and Invariants

Each active Hard Invariant receives a causal card with:

- the risk it controls;
- the design assumption;
- every enforcement point;
- the evidence generated;
- a concrete violation example;
- the system behavior if an enforcement point is removed.

Stable identifiers remain `HI #1`, `#2`, `#3`, `#5`, `#7`, `#8`, and `#9`.
Retired identifiers `#4` and `#6` appear only in the evolution chapter and are
explicitly marked retired.

The atlas must distinguish:

- a **model failure**, where the model chooses or produces a poor result;
- a **driver failure**, where orchestration, context, or recovery logic is
  incorrect;
- a **substrate denial**, where a deterministic boundary intentionally blocks
  the action;
- a **substrate defect**, where the enforcement boundary itself violates its
  documented contract.

### 6. Token and Runtime Economics

The economics chapter maps each limit to its single owner:

| Dimension | Owner |
|---|---|
| Per-call output tokens | Per-worker effort/output budget |
| Worker turns/cycles | Worker or pipeline-stage contract |
| Model-input characters | Context fitting boundary |
| Total pipeline stage allowance | Parent `ChildTokenBudget` allocation |
| Root worker count and recovery | Root routing/goal-state controller |
| LM time | Each actual router call |
| Audit/storage growth | Append-only persistence boundaries |

Explain provider-reported input, cached input subset, output, LM time, and
estimated/clamped usage without introducing credit or pricing abstractions.
Replay/context material is explained as model input when it is resent; cached
input remains a subset of input rather than an additional charge category.

Every execution trace marks the exact steps that do and do not call a model.
Substrate operations, database reads, validation, policy checks, and Console
projection are explicitly zero-LLM even though they consume CPU and wall-clock
time.

### 7. Evolution and Dissolution Map

The history chapter uses the record:

`previous shape → observed problem → design decision → current shape → future removal trigger`

It covers at least:

- fixed four-stage pipeline to user-defined worker recipes;
- main/sub-agent distinction to a unified worker model;
- embedded VS Code host to standalone CLI plus native Console;
- Pipeline Studio execution surface to builder-only workspace;
- accumulated root transcripts to current-run goal state and bounded outcome
  packets;
- skill-only reachability gap to root-selected pushed skills;
- unbounded or duplicated limits to one owner per budget dimension;
- fixed prompt scaffolding toward thinner ephemeral structure;
- planned relocation of platform-neutral catalogs out of `.github/`.

Historical claims must cite roadmap, design documents, or commits and display
an explicit `Historical interpretation` label. Current runtime cards never use
historical behavior as evidence.

### 8. Interactive Quiz

Place a short quiz after each major chapter and a cumulative assessment at the
end. Use multiple-choice questions only. Ship at least 24 distinct questions;
the cumulative assessment draws 12 without changing their answer semantics.

Each question contains:

- one unambiguous prompt;
- three or four plausible options;
- exactly one correct answer;
- a causal explanation shown only after submission;
- a link to the relevant atlas section;
- a difficulty level: `boundary`, `trace`, `economics`, `failure`, or
  `evolution`.

At least half the questions require reasoning about a scenario rather than
recalling a name. Example question patterns include predicting whether an LM
call occurs, locating the correct enforcement boundary, identifying the audit
evidence created, and distinguishing a model error from a substrate denial.

The question bank must cover these causal chains:

- why vendor neutrality does not move governance into each router;
- why root file mutation is blocked and delegated to a mutate worker;
- why same-turn spawn siblings can run concurrently but cross-turn spawns do
  not form one parallel batch;
- why evaluator independence requires artifact-only context;
- where a selected pushed skill is revalidated, recorded, loaded, and injected;
- why a final-turn worker with a verified artifact may complete while an
  unsupported text-only completion claim remains escalated;
- why fair-share child budgets protect later pipeline stages;
- why an elision marker is valid in replay history but invalid as a complete
  filesystem tool argument;
- why Console exposes sanitized skill provenance but not raw tool arguments or
  results, and when evidence remains unassigned;
- why Console joins audit and state databases;
- why an unavailable external MCP server does not necessarily abort a run and
  why its tools are outside the Musubi-owned governance boundary;
- why a nested pipeline role outside the declared role firewall is rejected.

The quiz displays chapter score, total score, answered count, explanations,
and a reset action. Progress persists in `localStorage` under a versioned key.
Storage failure must degrade to an in-memory quiz without breaking the atlas.

## Evidence Model

Every claim carries one of four badges:

- **Verified fact:** directly supported by current source, schema, or test.
- **Design rationale:** an explicit design document statement or a clearly
  labeled inference from current boundaries.
- **Historical interpretation:** reconstructed from roadmap, superseded design,
  or git history.
- **Open question:** active backlog or unresolved design decision.
- **Stale or contradicted:** current prose/comment/config residue that conflicts
  with a higher-priority runtime source or enforced boundary.

Verified facts include repository-relative source paths and line numbers from
the recorded source commit. Important system claims should also cite a
representative test or schema table where available.

The generation process must prefer these canonical sources:

1. Runtime source and database schema.
2. Tests that assert the boundary.
3. `CLAUDE.md` Hard Invariants and decision rules.
4. `AGENTS.md` system map.
5. `docs/roadmap.md` current direction and dissolution state.
6. Historical specs, plans, and git log for evolution only.

If canonical prose conflicts with runtime source, the atlas records the
conflict as an open documentation defect instead of silently choosing prose.

### Known evidence hazards to surface

The source audit identified current repository residue that must not be taught
as active behavior:

- `AGENTS.md` and parts of `docs/guide.md` still say workers can summon whole
  pipelines, while the effective root/child tool surfaces hide
  `musubi_spawn_pipeline` and reserve pipeline launch for explicit operator
  invocation.
- `agent/subagent.py` contains leaf/one-level language although allowed nested
  workers can run to the configured depth cap.
- pipeline YAML and older memory documents contain `credit` terminology even
  though live economics are token-only and compatibility readers ignore legacy
  credit fields.
- several server/schema comments still say “extension-side runner” after the
  VS Code host was removed.
- legacy Pipeline Studio chat/session fields remain readable in the Rust state
  contract although the current Studio is builder-only.
- root prompt metadata is not necessarily the enforced runtime cap owner; the
  atlas follows executable budget resolution rather than prompt declarations.

These appear in a “documentation archaeology” callout with the
`Stale or contradicted` badge and the higher-priority evidence that supersedes
them. The atlas does not silently rewrite or normalize the repository history.

## User Interface

### Desktop layout

- **Left sidebar:** table of contents, reading progress, component-layer
  filters, and search.
- **Center canvas:** chapter content, maps, atlas cards, trace lab, economics,
  evolution, and quiz.
- **Right evidence drawer:** glossary, evidence links, selected-component
  relationships, and the current mental-model summary. It can collapse.

### Visual language

Use Musubi's dark navy visual identity while optimizing for long-form reading:

- amber: orchestration and active flow;
- mint: deterministic allow, completion, and verified substrate;
- red: denial, defect, or failed boundary;
- blue: model/provider and read-only information;
- violet: skills, memory, and capability injection;
- muted gray: historical or retired structure.

Do not encode meaning by color alone. Every state has a text label, icon, line
style, or pattern.

### Interaction

- Search matches component name, responsibility, source path, invariant, and
  failure mode.
- Filters combine layer and lifecycle without hiding the active selection.
- Filters expose trust zone and durability as separate controls.
- Component cards support compact and expanded states.
- The system map is keyboard navigable and exposes a textual relationship list.
- Execution traces support next, previous, restart, and direct step selection.
- Quiz controls are keyboard accessible and announce result text.
- Reduced-motion preference disables animated flow.
- A `<noscript>` answer key keeps quiz questions, correct answers, explanations,
  and section references available when JavaScript is disabled; it does not
  attempt scoring.

## Failure Handling

- Missing `localStorage`: continue with in-memory state.
- Invalid saved quiz payload: discard it and start clean.
- JavaScript disabled: core prose, component records, diagrams, evidence, and
  the `<noscript>` quiz answer key remain readable; interactive controls and
  scoring are unavailable.
- Narrow viewport: collapse sidebars into drawers and render maps as scrollable
  canvases without shrinking text below a readable size.
- Unknown or conflicting evidence: display an open-question callout; do not
  invent a resolved claim. When runtime evidence resolves the conflict, label
  the lower-priority claim `Stale or contradicted` and cite both sides.
- Missing source line: keep the path, omit the line number, and mark the
  evidence as needing refresh.

## Privacy and Safety

The atlas embeds no secrets, environment values, API keys, raw audit rows,
database contents, user prompts, or generated artifacts from prior runs.
Examples use synthetic identifiers and sanitized content. It does not import
or execute repository code.

## Verification

Verification is proportional to a standalone HTML teaching artifact:

1. Validate that the file contains one document and no external network
   dependencies.
2. Parse the HTML and assert required landmark IDs, navigation targets,
   component records, scenarios, evidence badges, and quiz questions exist.
3. Run the embedded JavaScript in a browser and exercise search, filters,
   component selection, both map overlays, trace navigation, quiz scoring,
   reset, and storage fallback.
4. Test desktop and narrow viewport screenshots for overflow, illegible text,
   clipped controls, and unusable diagrams.
5. Verify every repository-relative source path exists at the recorded commit.
6. Sample-check line references against the source snapshot.
7. Search for secrets, external URLs, placeholder text, stale credit/pricing
   language, and claims that Pipeline Studio executes runs.
8. Confirm the quiz has one correct answer per question and explanations point
   to valid atlas sections.

## Acceptance Criteria

- `artifacts/musubi-system-atlas.html` opens directly and remains useful
  without JavaScript.
- The first screen establishes the model/driver/substrate mental model.
- Every required component has responsibility, rationale, interfaces,
  dependencies, enforcement, failure, economics, evidence, and lifecycle.
- Trust zone and durability are represented as separate axes; durable driver
  code is never mislabeled as part of the zero-LLM substrate.
- All required execution scenarios are navigable step by step.
- Active Hard Invariants have causal explanations and enforcement evidence.
- The economics chapter attributes every token/cycle/time dimension to one
  owner and contains no credit/pricing abstraction.
- The evolution chapter clearly separates current facts from historical
  interpretation and open backlog.
- The quiz is scored, explanatory, resettable, and resilient to storage
  failure, with at least 24 questions and causal coverage listed above.
- The document contains no external dependency, secret, live DB content, or
  runtime mutation.
- Automated structural checks and browser interaction checks pass.
