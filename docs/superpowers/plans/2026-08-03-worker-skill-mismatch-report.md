# Worker Skill Mismatch Report Implementation Plan

> **Status:** Implemented on 2026-08-03.

**Goal:** Let a worker state that the skill pushed into it does not fit its
brief, so a mismatch reaches the parent as an audited fact instead of dying
inside the one agent that knew it.

**Architecture:** HI #2 is untouched — the push still happens, is still not
opt-out-able, and a worker still cannot select its own skill. What is added is
a *statement*, not a *choice*: one MCP tool the harness validates, a per-worker
ContextVar sink (the same pattern `_worker_touched_files` uses), and one
compact line projected onto the worker's summary. The parent decides what to do
with it; the substrate only carries it.

**Tech Stack:** Python 3.11, FastMCP, stdlib JSON, pytest, Ruff.

## Why this exists

A `coder` was spawned for "create an application to check weather of cities in
vietnam" with `web-ui` pushed. That skill's procedure says:

- `web-ui/SKILL.md:38-39` — "One-off artifact → one `.html` file … **no
  external requests the viewer's network might block**"
- `web-ui/SKILL.md:71-72` — "it must open from `file://` with no server"

A weather app must fetch live data, so the pushed procedure contradicted the
brief. The worker had no way to say so: its step-1 decision tree has exactly
two leaves (one-off artifact / part of an existing site) and the request
belonged to neither. It produced a self-contained 6,377-byte page, and the
mismatch never reached the root that would spawn the next worker.

Note what this is NOT a fix for. The root's choice was not careless — it was
the least-wrong option in a catalog where `web-ui` is the only coder skill
whose `completion-contract` declares it produces a file. The deeper constraint
is arity: `pushed_skill_id` is singular (`server.py:1576`), so a task needing
presentation AND data acquisition cannot be expressed at spawn time at all.
This plan does not change that. It makes the resulting mismatch visible.

## Global Constraints

- HI #2 holds: no opt-out, no self-selection, no post-hoc swap. The worker runs
  under the pushed skill before and after calling this.
- The suggested skill passes the same firewall a spawn passes — the role's
  `AGENT_SKILL_ALLOWLIST` plus catalog existence. A worker that could name any
  skill here would have widened its own contract from inside the sandbox.
- Only a report the SERVER accepted counts. The parent reads the harness's
  verdict, never the worker's raw claim.
- The report is not failure evidence: it travels with a `done` outcome too.

---

### Task 1: The tool and its firewall

**Files:**
- Modify: `musubi/server.py:52, 1494, 1925-2010`
- Modify: `musubi/agent/boundary.py:140-152, 232-237`

**Interfaces:**
- Produces `musubi_report_skill_mismatch(handle_id, reason,
  suggested_skill_id=None) -> str`.
- Produces `_WORKER_REPORT_TOOLS`, allowed for every role.

**Steps:**
- [x] Reject an unknown handle, a terminal worker, and a blank reason.
- [x] Validate `suggested_skill_id` against the role allowlist and catalog.
- [x] Bound the recorded reason at `_SKILL_MISMATCH_REASON_CHARS` (400).
- [x] Echo `pushed_skill_id` from the spawn row — the worker never asserts it.

### Task 2: Reach every worker, including toolless ones

**Files:**
- Modify: `musubi/agent/subagent.py:616-640`

**Rationale:** the report is not a capability. Gating it behind one would
silence exactly the roles most likely to need it — `summarizer` maps to no
tools at all. `select_child_tools` grants it unconditionally.

**Steps:**
- [x] Add the tool to every worker surface regardless of symbolic capabilities.
- [x] Update the explorer surface regression to the new expected set.

### Task 3: Carry the verdict to the parent

**Files:**
- Modify: `musubi/agent/run.py:173-186, 3807-3850, 4090-4130`
- Modify: `musubi/agent/subagent.py:200-215, 283-340, 581-600`

**Interfaces:**
- Produces `_worker_skill_reports` ContextVar and `_record_skill_mismatch`.
- Produces `_skill_mismatch_line(role, reports)`.

**Steps:**
- [x] Record only an accepted report; ignore a rejected one.
- [x] One sink per `run_subagent`, so a nested worker's verdict never surfaces
      on its parent's outcome.
- [x] Project the FIRST report as one line onto the summary, whatever the
      worker's status.

### Task 4: Tell the worker the hatch exists

**Files:**
- Modify: `musubi/agent/subagent.py:584-625`
- Modify: `musubi/agent/pipeline_runner.py:590`

**Rationale:** a tool a worker cannot address is a tool it does not have, so
the prompt that advertises the report must also carry the worker's own
`handle_id`. The instruction is attached only when a skill was actually
pushed — with nothing pushed there is nothing to mismatch.

**Steps:**
- [x] Add `handle_id` to `build_subagent_system_prompt`; pass it from both the
      direct-worker and pipeline-stage call sites.
- [x] State the narrow trigger (procedure CONTRADICTS the brief), the "report
      once and keep working" rule, and that incomplete ≠ mismatch.

---

## Verification

- `pytest musubi/tests` — full suite green.
- New coverage: report accepted/echoed, suggestion firewall (both denial
  paths), unknown handle / blank reason / terminal worker, every role reaches
  the tool, toolless role still gets it, prompt carries the handle, accepted
  report rides a `done` outcome, rejected report never reaches the root, no
  leak from a nested worker, and the projection keeps one fact.
- Ruff: findings identical to baseline on every touched file.
- No paid model smoke run.

## Follow-ups this deliberately does not do

- **Push arity.** One skill per spawn is the constraint that made the wrong
  choice unavoidable. Changing it is a HI #2 design discussion.
- **The `web-ui` decision tree.** Its step 1 has no branch for "a new
  standalone app that needs live data", and its description ("or any
  browser-rendered artifact") claims territory it cannot serve.
- **Empty `completion-contract`s.** 7 of the 9 coder skills declare none, so
  the two that do read as the only ones that produce anything.
