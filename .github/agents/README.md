# .github/agents/ - Agent Catalog

Agent prompts are organized by runtime purpose. The purpose directories are
canonical; there are no flat files.

## Layout

```
.github/agents/
├── root/
│   └── root.agent.md
├── workers/
│   ├── planner.agent.md
│   ├── designer.agent.md
│   ├── coder.agent.md
│   ├── reviewer.agent.md
│   ├── explorer.agent.md
│   ├── investigator.agent.md
│   ├── reviewer-aux.agent.md
│   ├── summarizer.agent.md
│   ├── scoper.agent.md
│   ├── finder.agent.md
│   └── synthesizer.agent.md
└── proposed/            (human-reviewed prompt proposals)
```

## Prompt Purposes

- **`root/`** documents the top-level agent contract (spawn allowlist, budget,
  sees). The standalone CLI's root prompt itself is built in
  `musubi/agent/context.py`; the frontmatter here is what the policy engine
  reads for the root's `spawn_allowlist`.
- **`workers/`** is for workers spawned on a firewalled brief — directly by the
  root agent, or as a pipeline stage. One prompt per role, shared by both
  paths. Workers do not read pipeline stages; the brief is the task.
- **`pipeline-stages/<pipeline>/`** (optional, ships empty) may hold a
  pipeline-specific variant of a role. The standalone runner prefers
  `workers/<role>.agent.md` and falls back to
  `pipeline-stages/<pipeline>/<role>.agent.md`; authoring a variant requires
  3+ documented failures of the canonical worker prompt (Decision Rules).

## Resolver Behavior

`musubi/agent/prompt_resolver.py` resolves by purpose:

- Root: `root/<role>.agent.md` -> `<role>.agent.md`
- Worker: `workers/<role>.agent.md` -> `<role>.agent.md`
- Pipeline stage:
  `pipeline-stages/<pipeline>/<role>.agent.md` ->
  `<pipeline>-<role>.agent.md` -> `<role>.agent.md`

Pipeline stages in the standalone runner resolve Worker-first, then Pipeline
stage (`agent/pipeline_runner.py::_read_stage_agent_md`); a role with no
prompt in either place fails the stage closed. Invalid role or pipeline names
containing path separators or `..` are rejected.

## Tool Surface Notes

The standalone root agent keeps the small `agent` tool surface. Spawned workers
are sized from the full local Musubi catalog, then narrowed by role policy.
That means a direct `coder` can receive `musubi_write_file`,
`musubi_edit_file`, and `musubi_run_command` while the root model still cannot
see those mutating tools.

Direct workers are leaves unless their prompt declares `spawn_allowlist`.
Pipeline stages nest only when the server's `spawn_roles` (pipeline.yaml
`spawns:` ∩ the role's firewall) is non-empty and the caller still has depth
budget.
