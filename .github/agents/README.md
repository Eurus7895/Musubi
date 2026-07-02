# .github/agents/ - Agent Catalog

Agent prompts are organized by runtime purpose. Legacy flat files remain during
the migration so older workspaces and packaged bundles keep working.

## Layout

```
.github/agents/
├── root/
│   └── agent.agent.md
├── workers/
│   ├── planner.agent.md
│   ├── designer.agent.md
│   ├── coder.agent.md
│   └── reviewer.agent.md
├── pipeline-stages/
│   ├── feature-dev/
│   │   ├── planner.agent.md
│   │   ├── designer.agent.md
│   │   ├── coder.agent.md
│   │   └── reviewer.agent.md
│   └── code-review/
│       ├── scoper.agent.md
│       ├── finder.agent.md
│       └── synthesizer.agent.md
├── meta/
│   ├── pipeline-builder.agent.md
│   └── skill-builder.agent.md
└── *.agent.md                         (legacy fallback during migration)
```

## Prompt Purposes

- **`root/`** is the top-level standalone chat/router prompt.
- **`workers/`** is for direct standalone workers spawned by the root agent.
  These prompts act on a firewalled brief and should not read pipeline stages.
- **`pipeline-stages/<pipeline>/`** preserves pipeline ceremony and JSON output
  contracts for supported slash-command pipelines.
- **`meta/`** is for agents that operate on Musubi's catalog or pipeline
  definitions rather than product code.
- **Flat files** are fallback only. New runtime-specific behavior belongs in a
  purpose directory first.

## Resolver Behavior

Python standalone workers use `musubi/agent/prompt_resolver.py`.
The VS Code extension mirrors that precedence in
`copilot-harness-extension/src/agentPromptResolver.ts`.

Resolution order by purpose:

- Root: `root/<role>.agent.md` -> `<role>.agent.md`
- Worker: `workers/<role>.agent.md` -> `<role>.agent.md`
- Pipeline stage:
  `pipeline-stages/<pipeline>/<role>.agent.md` ->
  `<pipeline>-<role>.agent.md` -> `<role>.agent.md`
- Meta: `meta/<role>.agent.md` -> `<role>.agent.md`

Invalid role or pipeline names containing path separators or `..` are rejected.

## Tool Surface Notes

The standalone root agent keeps the small `agent` tool surface. Spawned workers
are sized from the full local Musubi catalog, then narrowed by role policy.
That means a direct `coder` can receive `musubi_write_file`,
`musubi_edit_file`, and `musubi_run_command` while the root model still cannot
see those mutating tools.

Direct workers are leaves unless their prompt declares `spawn_allowlist`.
Pipeline-stage prompts may keep their existing nesting behavior when the
pipeline and policy allow it.
