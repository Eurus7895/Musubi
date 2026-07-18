# Roadmap Status Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `docs/roadmap.md` distinguish completed bounded tracks from the ongoing skill-catalog focus without changing any unfinished work.

**Architecture:** Reclassify existing roadmap prose instead of rewriting implementation history. `Active` will contain the ongoing skill-catalog track, while the two bounded and merged runtime tracks will move intact to `Completed Tracks`; Backlog, Postponed, and the runtime-limit ownership rule remain semantically unchanged.

**Tech Stack:** Markdown, Git, PowerShell, ripgrep

## Global Constraints

- Move existing prose instead of rewriting implementation claims.
- Do not duplicate a track across status sections.
- Keep historical detail concise and retain links to governing plans and designs.
- Do not change product code, tests, or runtime behavior.
- Preserve every unfinished Backlog and Postponed entry.

---

### Task 1: Reclassify roadmap tracks

**Files:**
- Modify: `docs/roadmap.md:36-157`
- Reference: `docs/superpowers/specs/2026-07-18-roadmap-status-cleanup-design.md`

**Interfaces:**
- Consumes: the approved track-classification rules in the design note.
- Produces: one mutually exclusive status location for every roadmap track.

- [x] **Step 1: Capture the pre-edit status inventory**

Run:

```powershell
rg -n "^### Active|^### Backlog|^## Completed Tracks|Bounded standalone pipeline runtime|Root goal-state controller|Skill catalog growth" docs/roadmap.md
```

Expected before editing:

- `Bounded standalone pipeline runtime` and `Root goal-state controller` occur under `### Active`.
- `Skill catalog growth` occurs under `### Backlog`.
- Each track occurs exactly once.

- [x] **Step 2: Reclassify the three tracks**

Edit `docs/roadmap.md` so that:

```markdown
### Active

1. **Skill catalog growth.** Skills remain the cheapest optimization surface.
```

retains the existing complete skill-catalog paragraph, including the landed
first batch, direct-worker reachability, and pipeline-stage reachability.
Remove that paragraph from `### Backlog`.

Move the existing bounded-runtime and root-goal-state paragraphs, with their
plan/design links and implementation claims intact, to the top of
`## Completed Tracks` as two bullets:

```markdown
- Bounded standalone pipeline runtime — ...
- Root goal-state controller and token economics — ...
```

Keep the paragraph beginning `Runtime limits have one owner per dimension`
between `Active` and `Backlog`; it remains a current architectural constraint.

- [x] **Step 3: Verify exclusive status placement and preserved backlog**

Run:

```powershell
rg -n "^### Active|^### Backlog|^## Completed Tracks|Bounded standalone pipeline runtime|Root goal-state controller|Skill catalog growth|Installer runtime reduction|Signing and release hardening|Stage extension by user grant|Incomplete-artifact continuation policy|Lines-of-substrate|Relocate substrate" docs/roadmap.md
```

Expected after editing:

- The three reclassified track names each occur exactly once.
- `Skill catalog growth` occurs between `### Active` and `### Backlog`.
- Both bounded tracks occur after `## Completed Tracks`.
- All six listed unfinished backlog entries remain present.

- [x] **Step 4: Inspect Markdown integrity and content movement**

Run:

```powershell
git diff --check
git diff -- docs/roadmap.md
```

Expected: `git diff --check` exits `0`; the diff contains only text movement
and numbering/bullet adjustments, with no product-code changes or lost links.

- [x] **Step 5: Commit the roadmap cleanup**

Run:

```powershell
git add docs/roadmap.md docs/superpowers/plans/2026-07-18-roadmap-status-cleanup.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(roadmap): reclassify completed tracks"
```

Expected: one Conventional Commit containing only the roadmap update and this
implementation plan.
