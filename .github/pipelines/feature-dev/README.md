# feature-dev pipeline

Guided 4-agent pipeline for feature development in CopilotHarness.

## Stages

| # | Agent    | Writes  | Reads        | Role                                  |
|---|----------|---------|--------------|---------------------------------------|
| 1 | planner  | plan    | —            | Decomposes request into tasks         |
| 2 | designer | design  | plan         | Defines module interfaces + schemas   |
| 3 | coder    | code    | plan, design | Implements the design                 |
| 4 | reviewer | review  | code         | Evaluates code against the checklist  |

The reviewer runs under an evaluator firewall — it does **not** see the
request, plan, or design. It judges the code artifact against the
`code-review` skill checklist only (Week 3a).

## Correction loop

If reviewer returns `status: "fail"`, the coder retries with
`fix_instructions`. Max 3 attempts, then the pipeline escalates.

## Surfaces

Two native surfaces, both fed from the same pipeline events:

1. **Copilot Chat** (v0.3.1 in-chat rendering) — each stage streams a
   markdown section with status emoji (⏳ running, ✓ complete, ↻ retry),
   skill / memory / firewall / schema / policy tag line, elapsed seconds,
   and — on reviewer fail — a blockquote with the verdict and
   `fix_instructions` before the next coder attempt. Pipeline end emits a
   **View plan.md** anchor.
2. **Tasks TreeView** (v0.4.0) — activity-bar sidebar view listing the
   Active session's stages (pending / in_progress / complete / failed)
   and a History of past runs. Click a stage to open
   `.harness/sessions/<sid>/<stage>.md` in an editor tab.

## Level

`level: 2` — multi-agent generator. Week 3a deferred the Level-1
single-generator viability probe; we keep the 4-agent shape until
that evidence arrives.

## Skills

Skills live at `.github/skills/` (pipeline-agnostic) and are
auto-injected per `(stage, agent)` pair by `server.py::_STAGE_SKILL_MAP`:

- designer ← api-design
- coder ← python
- reviewer ← code-review (always, regardless of task)

Planner-declared `required_skills` are filtered through each agent's
allowlist in `context_builder.AGENT_SKILL_ALLOWLIST`.

## See also

- `/CLAUDE.md` — full design doc
- `/AGENTS.md` — session-start orientation
- `.github/commands/feature-dev.md` — the `/feature-dev` slash command
