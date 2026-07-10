// Static design data — ported verbatim from the Musubi Console prototype.

export const C = {
  amber: '#ff9b3d', green: '#54c79a', red: '#e86a5f', yellow: '#e3b341',
  t0: '#e9e9ea', t1: '#9b9ba2', t2: '#6a6a72',
}

export const roleMeta = {
  explorer: {
    hue: '#8ab4d8', tools: ['musubi_read_file', 'musubi_run_command', 'musubi_retrieve'], max: 6,
    model: 'llama3.1', profile: 'ollama.local', modelColor: '#8ab4d8',
    briefs: [
      'Map callers of LMRouter across agent/vendors',
      'Survey compression/ for entry points',
      'Index the musubi_* tool catalog in server.py',
      'Trace .musubi/llm.toml profile resolution',
    ],
  },
  investigator: {
    hue: '#d8b48a', tools: ['musubi_read_file', 'musubi_run_command', 'musubi_query_subagent_events'], max: 8,
    model: 'claude-sonnet-4', profile: 'anthropic.default', modelColor: '#ff9b3d',
    briefs: [
      'Reproduce the failing pytest in storage/db.py',
      'Diff schema.sql against embedded _SCHEMA_SQL',
      'Trace the fail-closed denial on run_command',
      'Bisect the regression in session/state.py',
    ],
  },
  'reviewer-aux': {
    hue: '#9ed8b4', tools: ['musubi_read_file'], max: 4,
    model: 'gpt-5-mini', profile: 'openai.default', modelColor: '#9ed8b4',
    briefs: [
      'Verify the patch touches code only',
      'Confirm no LLM SDK import in server.py',
      'Check append-only writes to audit.db',
      'Validate the (agent, tool) policy table',
    ],
  },
}

export const statusMeta = {
  running: { label: 'running', color: '#ff9b3d' },
  done: { label: 'done', color: '#54c79a' },
  failed: { label: 'failed', color: '#e86a5f' },
  escalated: { label: 'escalated', color: '#e3b341' },
  abandoned: { label: 'abandoned', color: '#6a6a72' },
}

export const roleOrder = ['explorer', 'investigator', 'reviewer-aux']

export const pipeCatalog = [
  { role: 'explorer', hue: '#8ab4d8', desc: 'Map & scope the codebase; surface entry points and callers.', tools: ['musubi_read_file', 'musubi_run_command', 'musubi_retrieve'], max: 6 },
  { role: 'planner', hue: '#c8a8e0', desc: 'Break the work into ordered, reviewable steps with a clear contract.', tools: ['musubi_read_file', 'musubi_retrieve'], max: 5 },
  { role: 'coder', hue: '#e0a878', desc: 'Implement the change across code, tests and wiring.', tools: ['musubi_read_file', 'musubi_write_file', 'musubi_run_command'], max: 10 },
  { role: 'reviewer', hue: '#9ed8b4', desc: 'Code-only firewall review — verdict tied to policy (HI #3).', tools: ['musubi_read_file'], max: 4 },
  { role: 'investigator', hue: '#d8b48a', desc: 'Reproduce a failure and trace it to its root cause.', tools: ['musubi_read_file', 'musubi_run_command', 'musubi_query_subagent_events'], max: 8 },
  { role: 'tester', hue: '#86c7c0', desc: 'Cover the changed surface with pytest / node:test cases.', tools: ['musubi_read_file', 'musubi_run_command'], max: 6 },
]

export const policyRoleDefs = [
  { role: 'driver', hue: '#ff9b3d', scope: 'full catalog', tools: 'musubi_* — all tools' },
  { role: 'explorer', hue: '#8ab4d8', scope: 'read + run', tools: 'read_file · run_command · retrieve' },
  { role: 'investigator', hue: '#d8b48a', scope: 'read + query', tools: 'read_file · run_command · query_subagent_events' },
  { role: 'reviewer-aux', hue: '#9ed8b4', scope: 'code-only', tools: 'read_file  —  firewall (HI #3)' },
]

export const profileDefs = [
  { name: 'anthropic.default', family: 'anthropic', model: 'claude-sonnet-4', transport: 'SDK', endpoint: 'api.anthropic.com', keyEnv: 'ANTHROPIC_API_KEY', fc: '#ff9b3d' },
  { name: 'openai.default', family: 'openai', model: 'gpt-5-mini', transport: 'SDK', endpoint: 'api.openai.com', keyEnv: 'OPENAI_API_KEY', fc: '#9ed8b4' },
  { name: 'ollama.local', family: 'ollama', model: 'llama3.1', transport: 'local', endpoint: '127.0.0.1:11434', keyEnv: '— (no key)', fc: '#8ab4d8' },
  { name: 'azure.work', family: 'azure', model: 'gpt-4o', transport: 'curl · mTLS', endpoint: 'gw.corp.internal', keyEnv: 'AZURE_API_KEY', fc: '#d8b48a' },
]

// ── presentation lookups ──
// Domain rows from any source carry only role/status; colour is derived here so
// a backend (audit.db) need not store presentation. Covers the driver, the
// sub-agent roles, and every pipeline catalog role.
export const hueByRole = (() => {
  const m = { driver: C.amber }
  for (const [role, meta] of Object.entries(roleMeta)) m[role] = meta.hue
  for (const c of pipeCatalog) m[c.role] = c.hue
  return m
})()
export const hueFor = (role) => hueByRole[role] || '#8a8a92'
export const modelColorFor = (role) => roleMeta[role]?.modelColor || hueFor(role)

export const skillDefs = [
  { name: 'bootstrap-mcp-tool', mode: 'PUSHED', appliesTo: 'coder', desc: 'Scaffold a new musubi_* MCP tool: handler, schema, audit wiring, test.' },
  { name: 'cross-cutting-rename', mode: 'PULLED', appliesTo: 'agent', desc: 'Coordinate a multi-path rename that touches code, tests, CI and docs together.' },
  { name: 'fail-closed-policy', mode: 'PULLED', appliesTo: 'agent', desc: 'Add an (agent, tool) entry to the policy table without relaxing to fail-open.' },
  { name: 'docs-from-reference', mode: 'PULLED', appliesTo: 'agent', desc: 'Write docs grounded in a reference file; never invent API surface.' },
  { name: 'test-existing-helper', mode: 'PUSHED', appliesTo: 'coder', desc: 'Cover an untested pure helper with node:test / pytest cases.' },
  { name: 'write-conventional-commit', mode: 'PUSHED', appliesTo: 'all', desc: 'Compose a Conventional Commits 1.0.0 message; imperative, ≤ 72 chars.' },
]
