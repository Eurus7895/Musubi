# Advisory Scope and Worker Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `simple_artifact` advisory, allow root to replace a terminal failed coder, and stop runs promptly when no mutation-capable worker attempt remains.

**Architecture:** Remove classifier-specific worker enforcement from `ScopeHint`; it may recommend an initial route but cannot consume lifetime recovery authority. Govern all direct root runs with one classifier-independent cumulative worker ceiling of three attempts. Record terminal worker outcomes in `Orchestration`, inject the prior outcome into a replacement worker, allow at most two root analysis cycles before replacement, and return deterministic `[incomplete]` when the generic worker ceiling is exhausted.

**Tech Stack:** Python 3.11+, asyncio, MCP/FastMCP, SQLite audit tables, pytest.

**Status:** Implemented on `fix/agent-artifact-recovery`. Focused agent,
parallel-dispatch, subagent, audit, and observability regressions pass.

## Global Constraints

- Preserve Hard Invariants #1, #5, #8, and #9.
- Keep `DEFAULT_SUBAGENT_MAX_CYCLES = 8`; every replacement is a new audited worker with its own eight-cycle budget.
- Keep `simple_artifact` and `simple_edit` as observability/routing labels only.
- Do not give root `Write`, `Edit`, or `Bash`.
- Do not increase the 200,000-token root budget.
- Keep the existing per-turn same-role width cap of three and add a separate cumulative root-run ceiling of three total direct workers.
- Pipeline stage workers retain pipeline-specific sequencing and are not charged against a direct root run's replacement state.
- The default `agent` tool surface already hides `musubi_run_command`; no catalog expansion or policy relaxation is part of this change. Hallucinated disallowed calls remain fail-closed.

## Decisions

- Delete the hard meaning of `ScopeHint.max_workers`; do not replace it with a `simple_artifact` recovery exception.
- A later coder after terminal failure is an ordinary replacement, not a special four-turn recovery worker.
- The harness does not use scope classification to decide whether multiple initial workers are legal; initial parallelism is bounded only by the existing per-turn width cap and the new generic root-run ceiling.
- Root may spend at most two LM cycles reading/retrieving after a terminal worker failure before it must spawn a replacement or return incomplete.
- Once the generic root-run ceiling is exhausted and root has no direct mutation capability, the harness returns deterministic incomplete output without a forced no-tools LM call.

---

### Task 1: Remove the prompt/boundary contradiction

**Files:**
- Modify: `.github/agents/workers/coder.agent.md:24-54`
- Test: `musubi/tests/test_agent_context.py`

**Interfaces:**
- Consumes: the real coder agent markdown loaded by `subagent._read_agent_md`.
- Produces: complete-first artifact instructions compatible with `_file_tool_argument_error`.

- [ ] **Step 1: Write failing prompt-contract tests**

```python
from pathlib import Path


def _read_coder_prompt() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / ".github/agents/workers/coder.agent.md").read_text(
        encoding="utf-8"
    )


def test_coder_prompt_never_requests_empty_write() -> None:
    text = _read_coder_prompt().lower()
    assert "write_file` with empty content" not in text
    assert "never reset a file with an empty write" in text


def test_coder_prompt_requires_complete_first_html() -> None:
    text = _read_coder_prompt().lower()
    assert "complete valid html" in text
    assert "closing tags" in text
    assert "at most one verification round" in text
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_context.py -k "coder_prompt" -q
```

Expected: FAIL because the shipped prompt explicitly recommends an empty reset and does not require a complete-first baseline.

- [ ] **Step 3: Replace the contradictory instructions**

Use this contract in `coder.agent.md`:

```markdown
4. For HTML/page/dashboard work, the first successful mutation must create a
   complete valid HTML document containing every requested section at minimal
   fidelity, including closing tags and required JavaScript initialization.
   Enhance it only after that complete baseline exists.
5. Never reset a file with an empty write; Musubi rejects empty content. For a
   genuinely large non-HTML artifact, start with a non-empty chunk and use
   ordered `musubi_append_file` calls with `expected_offset`.
6. After a successful artifact mutation, use at most one verification round
   unless that verification returns a concrete failure. Never use `cat`,
   `type`, or `Get-Content` merely to reread a file.
```

- [ ] **Step 4: Run prompt and boundary regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_context.py musubi/tests/test_agent_loop.py -k "coder_prompt or empty_write or invalid_args" -q
```

Expected: PASS; the prompt no longer causes empty writes and the deterministic boundary still rejects hallucinated empty writes.

- [ ] **Step 5: Commit Task 1**

```powershell
git add .github/agents/workers/coder.agent.md musubi/tests/test_agent_context.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): align coder prompt with file boundary"
```

---

### Task 2: Make scope classification advisory

**Files:**
- Modify: `musubi/agent/scope.py:23-79`
- Modify: `musubi/agent/run.py:131-180`
- Modify: `musubi/agent/run.py:1738-1781`
- Modify: `.github/agents/root/agent.agent.md:81-90`
- Test: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ScopeHint`, `_spawn_overflow_reasons`, and root prompt injection.
- Produces: `ScopeHint` without an enforceable worker count and `DEFAULT_MAX_ROOT_WORKERS = 3` on `Orchestration`.

- [ ] **Step 1: Write failing advisory-scope tests**

```python
def test_simple_artifact_scope_has_no_enforceable_worker_cap() -> None:
    hint = classify_task("create a dashboard about china")
    assert hint.kind is ScopeKind.SIMPLE_ARTIFACT
    assert not hasattr(hint, "max_workers")
    assert "max_workers=" not in hint.prompt_block()
    assert "start with one coder" in hint.prompt_block().lower()


def test_sequential_replacement_is_not_blocked_by_simple_scope() -> None:
    simple = classify_task("create an html dashboard")
    orch = Orchestration(parent_session_id="root")
    orch.spawned_workers = 1
    overflow = _spawn_overflow_reasons(
        [{"id": "retry", "name": "musubi_spawn_subagent",
          "input": {"role": "coder"}}],
        io.StringIO(),
        role="agent",
        scope_hint=simple,
        orchestration=orch,
        cycle_index=5,
    )
    assert overflow == {}
```

Retain the existing test that refuses the fourth same-role spawn in one batch. Add a separate assertion that three initial coders are allowed by the generic caps even when the scope label is `simple_artifact`; the prompt recommends one, but the classifier does not enforce it.

- [ ] **Step 2: Run the tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py -k "advisory or replacement or spawn_overflow" -q
```

Expected: FAIL because `ScopeHint.max_workers=1` is still emitted and `_spawn_overflow_reasons` still enforces it cumulatively.

- [ ] **Step 3: Remove `max_workers` from `ScopeHint`**

Delete the field from the dataclass and all classifier constructors. Replace the simple-route guidance with:

```text
Simple route: start with one coder using a compact implementation-ready brief.
This is an initial routing recommendation, not a lifetime worker cap. If that
worker fails or escalates, analyze the concrete failure and summon a replacement.
```

Remove `max_workers` from `prompt_block()` and `log_line()`.

- [ ] **Step 4: Remove scope-specific enforcement from spawn overflow**

Delete the branch that compares `orchestration.spawned_workers` with `scope_hint.max_workers`. Keep:

- fail-closed role policy;
- depth limit;
- `DEFAULT_MAX_SPAWNS_PER_ROLE = 3` for one tool batch; and
- a new classifier-independent `DEFAULT_MAX_ROOT_WORKERS = 3` cumulative ceiling stored on root `Orchestration`.

The cumulative refusal must say `root worker ceiling (3) reached`, never `simple_artifact worker cap`.

- [ ] **Step 5: Update root guidance**

Replace “use at most one coder” with “start with one coder”. State that a terminal failed/escalated coder may be replaced with a brief that continues existing files; a `done` coder must not be repeated without new user scope.

- [ ] **Step 6: Run scope and dispatch tests**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_parallel_dispatch.py -q
```

Expected: PASS; scope stays visible in logs/prompts but cannot deny a sequential replacement.

- [ ] **Step 7: Commit Task 2**

```powershell
git add musubi/agent/scope.py musubi/agent/run.py .github/agents/root/agent.agent.md musubi/tests/test_agent_loop.py musubi/tests/test_parallel_dispatch.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): make scope worker guidance advisory"
```

---

### Task 3: Carry worker outcomes into generic replacements

**Files:**
- Modify: `musubi/agent/run.py:131-180`
- Modify: `musubi/agent/run.py:481-782`
- Modify: `musubi/agent/run.py:1902-1947`
- Modify: `musubi/agent/subagent.py:138-212`
- Test: `musubi/tests/test_agent_loop.py`
- Test: `musubi/tests/test_subagent_orchestrator.py`
- Test: `musubi/tests/test_subagent_audit.py`

**Interfaces:**
- Produces:
  - `WorkerOutcome(role, status, summary, touched_files)`.
  - `Orchestration.record_worker_outcome(...)`.
  - `Orchestration.latest_failed_outcome(role)`.
  - `_replacement_brief(original_brief, outcome) -> str`.

- [ ] **Step 1: Write failing outcome/replacement tests**

```python
def test_terminal_worker_outcome_is_recorded_on_parent_orchestration() -> None:
    orch = Orchestration(parent_session_id="root")
    orch.record_worker_outcome(
        role="coder", status="escalated", summary="chart missing",
        touched_files={"china-dashboard.html"},
    )
    outcome = orch.latest_failed_outcome("coder")
    assert outcome.summary == "chart missing"
    assert outcome.touched_files == ("china-dashboard.html",)


def test_replacement_brief_continues_prior_artifact() -> None:
    outcome = WorkerOutcome(
        role="coder", status="escalated", summary="chart missing",
        touched_files=("china-dashboard.html",),
    )
    brief = _replacement_brief("finish the dashboard", outcome)
    assert "continue the existing artifact" in brief.lower()
    assert "china-dashboard.html" in brief
    assert "chart missing" in brief
    assert "do not restart" in brief.lower()
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py -k "outcome or replacement" -q
```

Expected: FAIL because terminal summaries are returned to root but not retained structurally.

- [ ] **Step 3: Add immutable outcome records**

```python
@dataclass(frozen=True)
class WorkerOutcome:
    role: str
    status: str
    summary: str
    touched_files: tuple[str, ...]
```

Store outcomes on the root `Orchestration`. `child()` and `stage_child()` start with independent empty outcome lists.

- [ ] **Step 4: Record outcomes after verified completion**

After `musubi_complete_subagent` returns:

```python
returned_summary = verified or summary
if orchestration is not None:
    orchestration.record_worker_outcome(
        role=role,
        status=status,
        summary=returned_summary,
        touched_files=touched,
    )
return returned_summary
```

Do not create a replacement outcome when the shared root token budget aborts the whole run.

- [ ] **Step 5: Inject replacement context automatically**

When root spawns a role whose latest outcome is `failed` or `escalated`, prepend:

```text
[worker-replacement]
Continue the existing artifact; do not restart it.
Prior status: escalated
Touched files: china-dashboard.html
Prior summary: chart missing
Complete missing acceptance criteria before optional enhancement.
[/worker-replacement]
```

Do not change `max_turns`; the new audited worker receives the normal eight-cycle budget.

- [ ] **Step 6: Verify audit identity**

Assert primary and replacement workers have different handles, both have spawn/completion rows, primary ends `escalated`, replacement ends `done`, and their `agent_cycles.worker_id` values remain separately attributable.

- [ ] **Step 7: Run subagent tests**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_subagent_audit.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```powershell
git add musubi/agent/run.py musubi/agent/subagent.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_subagent_audit.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): carry failures into replacement workers"
```

---

### Task 4: Bound root analysis and fail fast at the generic ceiling

**Files:**
- Modify: `musubi/agent/run.py:481-782`
- Test: `musubi/tests/test_agent_loop.py`
- Test: `musubi/tests/test_g3_observability.py`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Produces `DEFAULT_REPLACEMENT_ANALYSIS_CYCLES = 2` and `_mutation_path_exhausted_answer(...) -> str`.

- [ ] **Step 1: Write failing root-economics tests**

Cover the observed sequence:

1. Primary coder escalates after touching `china-dashboard.html`.
2. Root may use two read/retrieve cycles.
3. Root must then spawn a replacement or stop deterministically.
4. If the root worker ceiling is already three, no forced-final LM call occurs.

Assert the terminal result includes touched files, last status, and last summary. Assert provider call count does not increase after terminal exhaustion.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py -k "replacement_analysis or mutation_path_exhausted" -q
```

Expected: FAIL because root currently may continue until the general 16-cycle cap.

- [ ] **Step 3: Implement the two-cycle analysis window**

After a terminal failed/escalated worker outcome:

- allow two root batches containing only read/retrieve/discovery tools;
- reset the counter when root spawns a replacement;
- if a third non-spawn batch is requested, return deterministic `[incomplete] replacement window exhausted` without dispatching that batch;
- clear pending replacement state when a same-role worker returns `done`.

- [ ] **Step 4: Fail fast at the generic root ceiling**

When a spawn is refused because `DEFAULT_MAX_ROOT_WORKERS = 3` and root has no direct mutation capability, return:

```text
[incomplete] mutation path exhausted after 3 direct workers.
Touched files: china-dashboard.html
Last worker status: escalated
Last worker summary: chart and footer remain incomplete
```

Do not issue `musubi_list_subagents`, another file read, or a forced no-tools final LM call after this state is known.

- [ ] **Step 5: Add the China-dashboard integration regression**

Use fake root/worker responses to prove:

- primary coder: eight cycles, `escalated`;
- root analysis: at most two cycles;
- replacement coder: new handle, eight cycles available;
- replacement receives prior summary/touched files and completes;
- root returns success;
- token audit separates root, primary, and replacement usage.

- [ ] **Step 6: Update roadmap**

Record that scope classifications are advisory, direct root runs use a classifier-independent worker ceiling, and terminal worker failures support audited replacements with bounded root analysis. Link this plan.

- [ ] **Step 7: Run full verification**

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py musubi/tests/test_parallel_dispatch.py musubi/tests/test_salvage_on_exhaust.py musubi/tests/test_g3_observability.py -q
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
node --test gui/src/model/viewModel.test.mjs
npm run build --prefix gui
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 8: Commit Task 4**

```powershell
git add musubi/agent/run.py musubi/tests/test_agent_loop.py musubi/tests/test_g3_observability.py docs/roadmap.md docs/superpowers/plans/2026-07-13-simple-artifact-recovery.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "test(agent): cover bounded worker replacement"
```

## Acceptance criteria

- `simple_artifact` remains visible as a classification but has no hard or cumulative worker cap.
- The coder prompt never instructs an empty write and requires complete-first HTML.
- A terminal failed/escalated coder can be replaced by a new normal eight-cycle coder.
- The replacement has a distinct audited handle and receives prior summary plus touched files.
- Root spends at most two analysis cycles between terminal failure and replacement.
- All direct root runs share one classifier-independent ceiling of three workers.
- No provider call occurs after mutation authority is deterministically exhausted.
- Root remains read-only and token accounting remains token-only.
