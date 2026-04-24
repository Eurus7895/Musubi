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

## Surface

Everything renders inline in Copilot Chat. Each stage streams a markdown
section with a status emoji (⏳ running, ✓ complete, ↻ retry), the injected
skill / memory / firewall / schema / policy tags, elapsed seconds, and — on
reviewer fail — a blockquote with the verdict and fix_instructions before
the next coder attempt. On completion the chat emits a **View plan.md**
anchor pointing at `.harness/sessions/<sid>/plan.md`.

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
