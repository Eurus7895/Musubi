# pipeline-builder pipeline

Authors a NEW CopilotHarness pipeline from a brief. Outputs a directory under
`.github/pipelines/<new-name>/` plus the slash command, all wired to install.

## Stages

| # | Agent    | Writes  | Reads        | Role                                              |
|---|----------|---------|--------------|---------------------------------------------------|
| 1 | planner  | plan    | —            | Decomposes the brief into one task per artifact   |
| 2 | designer | design  | plan         | Specifies file structure + cross-file invariants  |
| 3 | coder    | code    | plan, design | Writes pipeline.yaml + agent files + plugin.json  |
| 4 | reviewer | review  | code         | Validates yaml parses, paths resolve, manifest matches |

The reviewer runs under the standard evaluator firewall — it sees only the
`code` artifact, not the original brief or the design. It judges the scaffold
against the **pipeline-config checklist** (A: structural validity, B: path
resolution, C: agent contract conformance, D: naming/scope, E: style) baked
into `agents/reviewer.agent.md`.

## Domain knowledge

Pipeline-authoring rules live **inside each agent's `.md` file**, not in a
separate `.github/skills/` entry. Reasoning:

- The harness's `_STAGE_SKILL_MAP` auto-injects feature-dev's defaults
  (`api-design` for designer, `python` for coder, `code-review` for reviewer)
  by `(stage, agent_name)` and is not pipeline-aware. Until that map is
  refactored to take `pipeline_name`, retargeting it would also retarget
  feature-dev.
- The pipeline-builder agents explicitly tell the LLM to disregard the
  noise-injected feature-dev skills and apply the embedded rules instead.

## Correction loop

If reviewer returns `status: "fail"`, the coder retries with
`fix_instructions`. Max 3 attempts, then escalate.

## Level

`level: 2` — multi-agent generator. The pipeline-authoring task has
clearly distinct phases (decomposition, structural design, file
authoring, structural validation), and the reviewer's checks are
deterministic enough that an evaluator firewall measurably improves
quality. This is the inverse of the feature-dev Level-1 probe debate:
for pipeline-builder, the structural reviewer pays for itself on every
run.

## Skills

This pipeline ships with **no new skill files**. Knowledge is embedded
in the agent prompts. If a future iteration extracts the pipeline-config
templates into a reusable skill, drop them at
`.github/skills/pipeline-authoring/SKILL.md` and add `pipeline-authoring`
to each pipeline-builder agent's allowlist in
`context_builder.AGENT_SKILL_ALLOWLIST`.

## Known limitations

- **Version locking is feature-dev-scoped.** `state.AGENTS_DIRS` only includes
  feature-dev's agent dir, so `lock_agent_versions` records feature-dev's
  planner/designer/coder/reviewer versions even when a pipeline-builder
  session is running. The runtime still loads the right agent files via
  `pipeline.ts:loadAgentPrompt` (which is pipeline-aware as of this work);
  only the crash-recovery version snapshot is misleading. Promote to a
  pipeline-aware version table (`{(pipeline, agent): version}`) when a
  second non-feature-dev pipeline ships.

## See also

- `pipeline.yaml` — pipeline definition
- `.github/commands/pipeline-builder.md` — slash command (`/pipeline-builder`)
- `.github/pipelines/feature-dev/` — reference pipeline used as the structural template
- `/CLAUDE.md` — full design doc
