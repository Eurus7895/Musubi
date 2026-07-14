# Documentation Source-of-Truth Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove superseded GUI documentation and make each canonical document describe the current Console driver, session, pipeline, and schema behavior without overlap.

**Architecture:** Git history and closed pull requests retain implementation chronology; repository documents retain only current behavior. Each canonical file owns one audience-specific layer, while the 2026-07-14 historical-session design and plan remain the sole detailed session references.

**Tech Stack:** Markdown, PowerShell, ripgrep, Git, Node.js test runner, Vite, Rust, Cargo.

## Global Constraints

- Do not change Console runtime behavior, database schema, or process ownership.
- Do not create a documentation archive.
- Do not rewrite historical `CHANGELOG.md` entries.
- Preserve the zero-LLM substrate boundary: the GUI shell launches the standalone driver process, and only that driver reaches `LMRouter`.
- Preserve busy historical browsing as read-only and idle resume as one atomic send-time promotion.
- Preserve `artifacts/hanoi-dashboard.html` as an unrelated untracked user artifact.
- Stage only documentation files named by this plan.

---

### Task 1: Remove superseded plans and repair roadmap ownership

**Files:**
- Delete: `docs/superpowers/plans/2026-07-01-gui-on-demand-task-launcher.md`
- Delete: `docs/superpowers/plans/2026-07-05-gui-pipeline-separate-session.md`
- Delete: `docs/superpowers/plans/2026-07-09-gui-cli-orchestrator-tokens.md`
- Delete: `docs/superpowers/plans/2026-07-13-read-only-session-browsing.md`
- Delete: `docs/superpowers/specs/2026-07-13-read-only-session-browsing-design.md`
- Modify: `docs/roadmap.md:90-115,148-159`

**Interfaces:**
- Consumes: the deletion decision in `docs/superpowers/specs/2026-07-14-documentation-source-of-truth-design.md`.
- Produces: a roadmap containing no live link to a deleted historical plan.

- [ ] **Step 1: Capture the obsolete-reference baseline**

Run:

```powershell
rg -n "gui-on-demand-task-launcher|gui-pipeline-separate-session|gui-cli-orchestrator-tokens|read-only-session-browsing" docs README.md AGENTS.md gui/README.md
```

Expected: roadmap and historical documents still contain matches.

- [ ] **Step 2: Delete the five superseded files**

Use `apply_patch` file-deletion patches for the five exact paths above. Do not
delete either 2026-07-14 documentation cleanup file or either 2026-07-14
resumable historical-session file.

- [ ] **Step 3: Replace roadmap links with current sources**

Keep the completed token-economics summary and link only:

```markdown
Plans:
[`2026-07-13-orchestrator-token-economics.md`](./superpowers/plans/2026-07-13-orchestrator-token-economics.md)
```

For project-scoped GUI sessions, keep the runtime/session-list plans and the
current resume plan:

```markdown
Plans:
[`2026-07-12-project-scoped-session-runtime.md`](./superpowers/plans/2026-07-12-project-scoped-session-runtime.md),
[`2026-07-12-orchestrator-session-list.md`](./superpowers/plans/2026-07-12-orchestrator-session-list.md), and
[`2026-07-14-resumable-historical-session.md`](./superpowers/plans/2026-07-14-resumable-historical-session.md)
```

Remove the completed-track link to the deleted task-launcher plan. Retain the
fact that the separate launcher was removed, but point readers to the current
Console guide instead:

```markdown
- GUI audit/orchestrator Console first-run slice — the separate task launcher
  was removed so Orchestrator chat remains the single interactive session
  surface. Current operation is documented in [`guide.md`](./guide.md).
```

- [ ] **Step 4: Verify deleted references are gone**

Run:

```powershell
rg -n "gui-on-demand-task-launcher|gui-pipeline-separate-session|gui-cli-orchestrator-tokens|read-only-session-browsing" docs README.md AGENTS.md gui/README.md
```

Expected: exit code 1 and no output.

- [ ] **Step 5: Commit the deletion boundary**

```powershell
git add docs/roadmap.md docs/superpowers/plans docs/superpowers/specs
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs: remove superseded implementation plans"
```

---

### Task 2: Correct product and session-start architecture

**Files:**
- Modify: `AGENTS.md:12-29`
- Modify: `README.md:12-40,315-321`

**Interfaces:**
- Consumes: the current Tauri `send_chat` and pipeline action boundaries.
- Produces: short overview copy that distinguishes the GUI shell from the launched standalone driver.

- [ ] **Step 1: Capture stale architecture claims**

Run:

```powershell
rg -n "never starts an agent|console only observes|Console \(GUI, observer\)|zero LLM calls" AGENTS.md README.md
```

Expected: the old observer-only claims are present.

- [ ] **Step 2: Update the AGENTS session-start map**

Replace the Console bullet with this bounded description:

```markdown
- **Console (GUI, operator):** the Tauri desktop app reads `audit.db` directly
  and may launch the standalone `agent` CLI only after an explicit chat or
  Pipeline Studio submission. The GUI shell and substrate make zero model
  calls; the launched driver reaches the model through `LMRouter`. It exposes
  orchestrator sessions, policy, audit, models, skills, and deterministic
  pipeline runs.
```

Change “One driver surface plus one observer” to “One driver host exposed
through CLI and native operator surfaces”. Keep `AGENTS.md` under 120 lines.

- [ ] **Step 3: Reduce README Console claims to overview scope**

The surface table must state:

```markdown
| Console (GUI) | start, observe, and resume governed sessions | native Tauri operator surface; launches the standalone driver on explicit submission and reads orchestration/audit state from `audit.db` |
```

The Console section must say that the Tauri shell itself makes no model calls,
while explicitly submitted work launches the standalone driver process. Link
session operation to the Console section of `docs/guide.md` instead of
duplicating the state machine.

- [ ] **Step 4: Verify the architecture boundary**

Run:

```powershell
rg -n "never starts an agent|Console \(GUI, observer\)|console only observes" AGENTS.md README.md
rg -n "standalone.*agent|explicit.*submission|LMRouter|audit.db" AGENTS.md README.md
```

Expected: the first command has no output; the second finds the new boundary
in both files.

- [ ] **Step 5: Commit the overview update**

```powershell
git add AGENTS.md README.md
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs: align Console driver architecture"
```

---

### Task 3: Make user and contributor workflows canonical

**Files:**
- Modify: `docs/guide.md:344-414`
- Modify: `gui/README.md:61-128`

**Interfaces:**
- Consumes: the overview boundary from Task 2 and exact active/viewed semantics from the 2026-07-14 session design.
- Produces: user instructions in `docs/guide.md` and contributor architecture in `gui/README.md` without duplicated schema details.

- [ ] **Step 1: Capture stale workflow claims**

Run:

```powershell
rg -n "Start work through the standalone|Run a pipeline by asking the driver|root agent spawns it|The eight views" docs/guide.md gui/README.md
```

Expected: the old CLI-only and chat-spawn pipeline instructions are present.

- [ ] **Step 2: Replace the guide's Console operation section**

Document this user flow:

```markdown
### Start and continue an Orchestrator session

Opening the Console is passive. Submitting Orchestrator chat explicitly
launches the standalone `agent` driver under that exact durable chat ID. While
one run owns the shared process, you may select another historical session to
inspect its chat and worker flow, but its input remains read-only. When the
driver becomes idle, that viewed session becomes writable; the first follow-up
atomically validates, promotes, persists, and launches under the viewed ID.
```

Add cancellation ownership: only the active owning session exposes Cancel.
Correct “eight views” to “seven views”. Replace the Pipeline Studio row with a
direct registered-recipe workflow using the selected preset and explicit Run
action; keep the CLI equivalent `agent "<brief>" --pipeline <name>`.

- [ ] **Step 3: Update contributor architecture in GUI README**

Under Data Source, add:

```markdown
The Rust shell owns one shared driver process per project. Explicit
Orchestrator or Pipeline Studio submission launches the standalone Python
driver; the React/Tauri shell does not call a model directly.
```

Under Orchestrator, describe active versus viewed chat IDs and atomic idle
promotion. Under Pipeline Studio, state that the selected registered recipe is
launched directly and is not inferred by root from an Orchestrator message.
Link exact serialized fields to `src-tauri/SCHEMA.md`.

- [ ] **Step 4: Verify current workflow wording**

Run:

```powershell
rg -n "Start work through the standalone|Run a pipeline by asking the driver|root agent spawns it|The eight views" docs/guide.md gui/README.md
rg -n "historical|read-only|atomically|registered recipe|seven views" docs/guide.md gui/README.md
```

Expected: the first command has no output; the second finds current user and
contributor behavior.

- [ ] **Step 5: Commit workflow documentation**

```powershell
git add docs/guide.md gui/README.md
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs(gui): document resumable sessions"
```

---

### Task 4: Correct the backend contract and canonical session design

**Files:**
- Modify: `gui/src-tauri/SCHEMA.md:19-27,97-113,143-171`
- Modify: `docs/superpowers/specs/2026-07-14-resumable-historical-session-design.md:1-18`

**Interfaces:**
- Consumes: `orchestratorChatId`, `viewedOrchestratorChatId`, `driverStatus.chatId`, and the existing `send_chat` Rust action.
- Produces: one durable contract for current selection, runtime ownership, and resume behavior.

- [ ] **Step 1: Capture stale contract claims**

Run:

```powershell
rg -n "stubbed with a `todo`|changes the active exact ID|orchestratorChatId.*pipelineChatId" gui/src-tauri/SCHEMA.md
```

Expected: all three outdated or incomplete contract statements are found.

- [ ] **Step 2: Correct GUI-side write and action ownership**

Replace the stub claim with:

```markdown
The app writes only GUI-side `chat_log` and `meta` state directly. Explicit
driver actions launch the standalone CLI, which performs governed mutations
through the MCP substrate; the GUI never writes append-only audit rows itself.
```

- [ ] **Step 3: Document active/viewed selection and atomic resume**

State that session selection while idle promotes the requested exact ID.
Selection while busy changes only `viewedOrchestratorChatId`, leaving
`orchestratorChatId`, `driverStatus.chatId`, and nonce ownership unchanged.
The viewed session is read-only until the driver is idle. On send, the optional
requested ID is validated and promoted before both `chat_log` insertion and
driver launch; a busy race fails closed.

- [ ] **Step 4: Complete the serialized state shape**

The state-shape paragraph must list:

```markdown
`orchestratorChatId`, `viewedOrchestratorChatId`, `pipelineChatId`,
`orchestratorSessions[]`, and `driverStatus.chatId`
```

Explain that `orchestratorChatId` is the active/future-write owner,
`viewedOrchestratorChatId` is the optional navigation target, and
`driverStatus.chatId` is the exact live or retained process owner.

- [ ] **Step 5: Mark the current session design as canonical**

Add after its title:

```markdown
> Canonical Orchestrator historical-session lifecycle. This design replaces
> the read-only browsing design dated 2026-07-13 while preserving its busy-run
> active-versus-viewed isolation.
```

- [ ] **Step 6: Verify the contract language**

Run:

```powershell
rg -n "stubbed with a `todo`|Select it again|must remain read-only until" gui/src-tauri/SCHEMA.md docs/superpowers/specs/2026-07-14-resumable-historical-session-design.md
rg -n "viewedOrchestratorChatId|atomic|busy|fail-closed|Canonical" gui/src-tauri/SCHEMA.md docs/superpowers/specs/2026-07-14-resumable-historical-session-design.md
```

Expected: the first command has no output; the second finds every current
contract term.

- [ ] **Step 7: Commit the contract update**

```powershell
git add gui/src-tauri/SCHEMA.md docs/superpowers/specs/2026-07-14-resumable-historical-session-design.md
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs(gui): define active and viewed sessions"
```

---

### Task 5: Verify the repository-wide documentation cleanup

**Files:**
- Modify: `docs/superpowers/plans/2026-07-14-documentation-source-of-truth-cleanup.md`
- Test: all Markdown files, frontend session tests, Rust Console tests, GUI production build.

**Interfaces:**
- Consumes: the canonical documents and deletions from Tasks 1-4.
- Produces: checked plan status and fresh evidence that documentation matches the implemented behavior.

- [ ] **Step 1: Check deleted names and stale claims repository-wide**

Run:

```powershell
rg -n -i --glob '*.md' "gui-on-demand-task-launcher|gui-pipeline-separate-session|gui-cli-orchestrator-tokens|read-only-session-browsing|stubbed with a `todo`|Select it again after the active run|Run a pipeline by asking the driver"
```

Expected: no output. The canonical session design may describe that it
replaces the design “dated 2026-07-13” without using the deleted filename.

- [ ] **Step 2: Validate relative Markdown links**

Run:

```powershell
$missing = @()
$files = rg --files -g '*.md'
foreach ($file in $files) {
  $text = Get-Content -Raw -LiteralPath $file
  $text = [regex]::Replace($text, '(?ms)^```.*?^```\s*', '')
  foreach ($match in [regex]::Matches($text, '\[[^\]]*\]\(([^)]+)\)')) {
    $target = $match.Groups[1].Value.Trim()
    if ($target -match '^(https?://|mailto:|#|file:|app:)') { continue }
    $target = $target.Split('#')[0].Split('?')[0].Trim('<', '>')
    if (-not $target) { continue }
    $base = Split-Path -Parent $file
    if (-not $base) { $base = '.' }
    $resolved = Join-Path $base $target
    if (-not (Test-Path -LiteralPath $resolved)) {
      $missing += "$file -> $target"
    }
  }
}
if ($missing.Count) {
  $missing | ForEach-Object { Write-Error $_ }
  throw "$($missing.Count) missing Markdown links"
}
Write-Output "Validated $($files.Count) Markdown files; no missing links."
```

Expected: zero missing live relative links.

- [ ] **Step 3: Run frontend session regression tests**

```powershell
node --test gui/src/model/viewModel.test.mjs gui/src/data/TauriSource.test.mjs gui/src/components/NewSessionButton.test.mjs gui/src/components/chatLinks.test.mjs gui/src/data/chatCommands.test.mjs
```

Expected: 58 tests pass.

- [ ] **Step 4: Run Rust Console tests**

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml
```

Expected: 27 tests pass, with no failures in unit or doc tests.

- [ ] **Step 5: Build the GUI**

```powershell
npm run build --prefix gui
```

Expected: Vite transforms 58 modules and exits 0.

- [ ] **Step 6: Check final Git scope**

```powershell
git diff --check
git status -sb
git diff --stat origin/dev...HEAD
```

Expected: no whitespace errors; only the planned documentation and existing
GUI implementation commits differ from `origin/dev`; the untracked Hanoi
dashboard remains untouched.

- [ ] **Step 7: Mark this plan implemented and commit verification**

Change every checkbox in this plan to `[x]` and add:

```markdown
**Status:** Implemented and verified on `fix/gui-resume-historical-session`.
```

Then commit only this plan:

```powershell
git add docs/superpowers/plans/2026-07-14-documentation-source-of-truth-cleanup.md
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs: record documentation cleanup verification"
```
