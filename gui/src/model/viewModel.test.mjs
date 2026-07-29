import test from 'node:test'
import assert from 'node:assert/strict'
import { buildViewModel, formatChatTimestamp } from './viewModel.js'

function baseState(overrides = {}) {
  return {
    view: 'orchestrator',
    selected: null,
    selectedSession: null,
    selectedPipeSession: null,
    paused: false,
    t: 3,
    auditFilter: 'all',
    draft: '',
    pipeDraft: '',
    processOpen: false,
    logWindowOpen: false,
    subagents: [],
    events: [],
    policy: [],
    audit: [],
    chat: [],
    pipeChat: [],
    totalSpawned: 0,
    totalDone: 0,
    allowCount: 0,
    denyCount: 0,
    activeProfile: 'deepseek.deepseek',
    profiles: [{ name: 'deepseek.deepseek', family: 'deepseek', model: 'deepseek-v4-flash' }],
    pipeSteps: [],
    pipeName: '',
    pipeRunning: false,
    pipeCur: -1,
    pipeProg: 0,
    pipeDoneFlag: false,
    runtimeSource: 'workspace',
    setupStatus: {},
    driverStatus: { running: false, chatId: '', surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
    agentTurns: [],
    agentCycles: [],
    runtimeLogEvents: [],
    orchestratorSessions: [],
    pipelineRuns: [],
    pipelineCatalog: [],
    pipelineBuilderCatalog: { presets: [], agents: [] },
    runMode: 'direct',
    selectedPipeline: '',
    pipelineBuilder: {
      step: 'catalog',
      draft: { name: '', description: '', version: '', baselineChecks: [], correction: null, stages: [] },
      savedRecipe: { name: '', description: '', version: '', baselineChecks: [], correction: null, stages: [] },
      selectedStageIndex: null,
      findings: [],
      saveResult: null,
      loading: false,
      pendingTransition: null,
    },
    orchestratorChatId: '',
    pipelineChatId: '',
    pipeModified: false,
    ...overrides,
  }
}

function agent(id, parentSession, status, role = 'coder', chatId = '') {
  return {
    id,
    handle: `h${id}`,
    spawnEpoch: id,
    role,
    brief: `brief ${id}`,
    status,
    turns: status === 'running' ? 2 : 4,
    max: 8,
    tools: ['Read', 'Write'],
    wall: 60,
    model: '',
    profile: '',
    parent: 'driver',
    parentSession,
    parentAgent: 'agent',
    chatId,
  }
}

function actions() {
  return {
    setView() {},
    selectAgent() {},
    selectSession() {},
    clearSelect() {},
    setAuditFilter() {},
    selectProfile() {},
    toggleProcess() {},
    openProcessLog() {},
    closeProcessLog() {},
    clearDriverChat() {},
    onDraft() {},
    onDraftKey() {},
    sendChat() {},
    cancelAgent() {},
    openArtifact() {},
    updatePipelineRecipe() {},
  }
}

test('projects Orchestrator run mode and registered pipeline options', () => {
  const vm = buildViewModel(baseState({
    runMode: 'pipeline',
    selectedPipeline: 'feature-dev',
    pipelineCatalog: [
      { name: 'feature-dev', description: 'ship features', runnable: true, blockedReason: '', stages: ['planner', 'coder'] },
      { name: 'blocked', description: 'blocked', runnable: false, blockedReason: 'invalid evaluator', stages: [] },
    ],
  }), actions())

  assert.equal(vm.runMode, 'pipeline')
  assert.equal(vm.selectedPipeline, 'feature-dev')
  assert.deepEqual(vm.pipelineOptions.map((option) => [option.name, option.selected, option.runnable]), [
    ['feature-dev', true, true],
    ['blocked', false, false],
  ])
  assert.equal(vm.selectedPipelineRunnable, true)
})

test('Pipeline composer fails closed until a runnable recipe is selected', () => {
  const vm = buildViewModel(baseState({
    runMode: 'pipeline',
    selectedPipeline: 'blocked',
    pipelineCatalog: [{ name: 'blocked', runnable: false, blockedReason: 'invalid contract', stages: [] }],
  }), actions())

  assert.equal(vm.sendDisabled, true)
  assert.match(vm.disabledText, /runnable pipeline/i)
})

test('projects evidence-backed runtime graph logs and successful skill provenance', () => {
  const coder = agent(10, 'root-session', 'done', 'coder', 'gui-orchestrator-project-one')
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-project-one',
    selectedSession: 'gui-orchestrator-project-one',
    selected: coder.handle,
    orchestratorSessions: [{
      chatId: 'gui-orchestrator-project-one', title: 'ship it', lastRequest: 'ship it',
      rootTurns: 1, workers: 1,
    }],
    subagents: [coder],
    agentTurns: [{
      id: 1, chatId: 'gui-orchestrator-project-one', parentSession: 'root-session',
      request: 'ship it', startedAt: 100, cycles: 1, tokensInEstimate: 20, tokensOutEstimate: 5,
    }],
    agentCycles: [{
      sessionId: 'root-session', stage: 'coder', workerId: coder.handle, cycleIdx: 1,
      lmMs: 12, tokensIn: 20, cachedInputTokens: 0, tokensOut: 5,
      tokenSource: 'provider', toolNames: ['musubi_get_skill'], cycleStatus: 'final',
    }],
    toolEvidence: [
      { id: 20, ts: '10:00:01', sessionId: 'root-session', chatId: 'gui-orchestrator-project-one', role: 'coder', workerId: coder.handle, tool: 'musubi_get_skill', category: 'skills', status: 'ok', skillId: 'python', detail: 'skill python' },
      { id: 21, ts: '10:00:02', sessionId: 'root-session', chatId: 'gui-orchestrator-project-one', role: 'coder', workerId: coder.handle, tool: 'musubi_get_skill', category: 'skills', status: 'error', skillId: 'unsafe', detail: 'skill unsafe' },
    ],
    policy: [{ id: 30, ts: '10:00:03', verdict: 'DENY', tool: 'musubi_write_file', role: 'coder', handle: coder.handle, reason: 'outside surface' }],
  }), actions())

  assert.deepEqual(vm.runtimeGraph.nodes.map((node) => [node.id, node.parentId]), [
    ['root', null], [coder.handle, 'root'],
  ])
  assert.deepEqual(vm.skillsByWorker[coder.handle], ['python'])
  // Per-worker token total surfaces on the worker's runtime node (tokensIn +
  // tokensOut from its agent_cycles, keyed by worker_id under the parent).
  assert.equal(vm.runtimeGraph.nodes.find((node) => node.id === coder.handle).tokens, 25)
  assert.equal(vm.runtimeLogs.some((row) => row.category === 'skills' && row.status === 'error'), true)
  assert.equal(vm.runtimeLogs.some((row) => row.category === 'policy' && row.status === 'deny'), true)
  assert.equal(vm.runtimeLogs.some((row) => row.category === 'model' && row.workerId === coder.handle), true)
  assert.equal(JSON.stringify(vm.runtimeLogs).includes('rawArgs'), false)
})

test('projects every request and its exact agents as append-only session history', () => {
  const first = agent(1, 'root-1', 'done', 'planner', 'gui-orchestrator-history')
  const second = agent(2, 'root-2', 'done', 'coder', 'gui-orchestrator-history')
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-history',
    selectedSession: 'gui-orchestrator-history',
    orchestratorSessions: [{
      chatId: 'gui-orchestrator-history', title: 'history', lastRequest: 'add export',
      rootTurns: 2, workers: 2,
    }],
    subagents: [first, second],
    agentTurns: [
      { id: 1, requestId: 'request-1', chatId: 'gui-orchestrator-history', parentSession: 'root-1', request: 'create website', startedAt: 100 },
      { id: 2, requestId: 'request-2', chatId: 'gui-orchestrator-history', parentSession: 'root-2', request: 'add export', startedAt: 200 },
    ],
    runtimeLogEvents: [
      { id: 1, requestId: 'request-1', chatId: 'gui-orchestrator-history', seq: 1, ts: 'epoch:100', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: '[musubi] launch request 1' },
      { id: 2, requestId: 'request-1', chatId: 'gui-orchestrator-history', seq: 2, ts: 'epoch:101', source: 'worker', stream: 'stderr', agentHandle: first.handle, role: 'planner', category: 'model', message: '[agent] planner cycle 0' },
      { id: 3, requestId: 'request-2', chatId: 'gui-orchestrator-history', seq: 1, ts: 'epoch:200', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: '[musubi] launch request 2' },
      { id: 4, requestId: 'request-2', chatId: 'gui-orchestrator-history', seq: 2, ts: 'epoch:201', source: 'worker', stream: 'stderr', agentHandle: second.handle, role: 'coder', category: 'tools', message: '[agent] write ok' },
    ],
  }), actions())

  assert.deepEqual(vm.runtimeGraph.nodes.map((node) => [node.id, node.parentId]), [
    ['request:request-1', null],
    [first.handle, 'request:request-1'],
    ['request:request-2', 'request:request-1'],
    [second.handle, 'request:request-2'],
  ])
  assert.equal(vm.runtimeGraph.requests.length, 2)
  assert.equal(vm.runtimeLogs.filter((row) => row.requestId === 'request-1').length, 2)
  assert.equal(vm.runtimeLogs.find((row) => row.agentHandle === second.handle).message, '[agent] write ok')
})

test('the in-flight request sorts newest, not oldest, despite having no turn row', () => {
  // agent_turns is written with ended_at=time.time(), so a running request has
  // no turn row at all. Sorting on `turn.startedAt || events[0].id` compared
  // epoch seconds against an AUTOINCREMENT rowid, so the running request —
  // the only one falling back to the rowid — always ranked oldest, took the
  // R01 label, and was handed the head of the continuation chain.
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'chat-live',
    selectedSession: 'chat-live',
    orchestratorSessions: [{ chatId: 'chat-live', title: 'calc', lastRequest: 'calc', rootTurns: 2, workers: 0 }],
    agentTurns: [
      { id: 1, requestId: 'req-old', chatId: 'chat-live', parentSession: 'root-1', request: 'first', startedAt: 1785166000 },
      { id: 2, requestId: 'req-mid', chatId: 'chat-live', parentSession: 'root-2', request: 'second', startedAt: 1785166300 },
    ],
    runtimeLogEvents: [
      { id: 1, requestId: 'req-old', chatId: 'chat-live', seq: 1, ts: '16:26:40', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: 'launch first' },
      { id: 2, requestId: 'req-mid', chatId: 'chat-live', seq: 1, ts: '16:31:40', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: 'launch second' },
      // Still running: ledger lines exist, the turn row does not.
      { id: 3, requestId: 'req-live', chatId: 'chat-live', seq: 1, ts: '16:37:21', source: 'root', stream: 'stderr', agentHandle: '', role: 'root', category: 'output', message: 'worker planner' },
    ],
  }), actions())

  assert.deepEqual(
    vm.runtimeGraph.requests.map((request) => request.requestId),
    ['req-old', 'req-mid', 'req-live'],
  )
  // Numbering is chronological, so the live request takes the highest R-number.
  assert.equal(vm.runtimeGraph.requests[2].label, 'Request 03')
  // And it continues the chain rather than heading it.
  assert.equal(vm.runtimeGraph.requests[0].parentId, null)
  assert.equal(vm.runtimeGraph.requests[2].parentId, 'request:req-mid')
})

test('includes pipeline stages launched by an Orchestrator chat in the runtime graph', () => {
  const stage = agent(40, 'pipeline-session', 'done', 'reviewer', 'gui-orchestrator-unified')
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-unified',
    selectedSession: 'gui-orchestrator-unified',
    orchestratorSessions: [{ chatId: 'gui-orchestrator-unified', title: 'review', lastRequest: 'review', rootTurns: 1, workers: 1 }],
    agentTurns: [{ id: 1, chatId: 'gui-orchestrator-unified', parentSession: 'root-session', request: 'review', startedAt: 100 }],
    pipelineRuns: [{
      sessionId: 'pipeline-session', chatId: 'gui-orchestrator-unified', pipelineName: 'code-review',
      brief: 'review', startedAt: 101, endedAt: 120, status: 'success', stages: [stage],
    }],
  }), actions())

  assert.equal(vm.runtimeGraph.mode, 'pipeline')
  assert.equal(vm.runtimeGraph.pipelineName, 'code-review')
  assert.equal(vm.runtimeGraph.nodes.some((node) => node.id === stage.handle), true)
})

test('does not attach an older pipeline run to a newer direct root turn', () => {
  const staleStage = agent(41, 'old-pipeline', 'done', 'coder', 'gui-orchestrator-unified')
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-unified',
    selectedSession: 'gui-orchestrator-unified',
    orchestratorSessions: [{ chatId: 'gui-orchestrator-unified', title: 'new direct turn', lastRequest: 'new direct turn', rootTurns: 2, workers: 0 }],
    agentTurns: [{ id: 2, chatId: 'gui-orchestrator-unified', parentSession: 'new-root', request: 'new direct turn', startedAt: 100 }],
    pipelineRuns: [{ sessionId: 'old-pipeline', chatId: 'gui-orchestrator-unified', pipelineName: 'feature-dev', startedAt: 90, status: 'success', stages: [staleStage] }],
  }), actions())

  assert.equal(vm.runtimeGraph.mode, 'direct')
  assert.equal(vm.runtimeGraph.nodes.some((node) => node.id === staleStage.handle), false)
})

test('keeps ambiguous audit rows visible without guessing a worker', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-one',
    selectedSession: 'gui-orchestrator-one',
    orchestratorSessions: [{ chatId: 'gui-orchestrator-one', title: 'task', lastRequest: 'task', rootTurns: 1, workers: 2 }],
    agentTurns: [{ id: 1, chatId: 'gui-orchestrator-one', parentSession: 'root-one', request: 'task', startedAt: 100 }],
    toolEvidence: [{ id: 8, ts: '10:00:00', sessionId: 'root-one', chatId: 'gui-orchestrator-one', role: 'coder', workerId: '', tool: 'musubi_read_file', category: 'tools', status: 'ok', skillId: '', detail: '' }],
  }), actions())

  assert.equal(vm.runtimeGraph.nodes.some((node) => node.id === 'unassigned'), true)
  assert.equal(vm.runtimeLogs[0].workerId, 'unassigned')
})

test('projects builder state without active Studio runtime controls', () => {
  const vm = buildViewModel(baseState({
    pipelineBuilder: {
      step: 'edit',
      draft: {
        name: 'feature-dev', description: '', version: '', baselineChecks: [], correction: null,
        stages: [{ preset: 'planner', agent: 'planner', stage: 'plan', spawns: ['researcher'] }],
      },
      savedRecipe: { name: 'feature-dev', description: '', version: '', baselineChecks: [], correction: null, stages: [] },
      selectedStageIndex: 0,
      findings: [{ severity: 'warning', message: 'check it' }],
      saveResult: null,
      loading: false,
      pendingTransition: { type: 'close' },
    },
  }), actions())

  assert.equal(vm.pipelineBuilder.step, 'edit')
  assert.equal(vm.pipelineBuilder.dirty, true)
  assert.equal(vm.pipelineBuilder.selectedStage.agent, 'planner')
  assert.equal(vm.pipelineBuilder.findings[0].message, 'check it')
  assert.equal(typeof vm.pipelineBuilder.actions.onUpdateRecipe, 'function')
  for (const field of ['pipeChatBody', 'pipeRuns', 'activePipeRunId', 'activePipeRunSteps', 'pipeRunSummary', 'pipeChat']) {
    assert.equal(vm[field], undefined, field)
  }
})

test('projects a sorted searchable builder library with blocked metadata and spawn roles', () => {
  const vm = buildViewModel(baseState({
    pipelineBuilder: {
      ...baseState().pipelineBuilder,
      libraryQuery: 'plan',
    },
    pipelineBuilderCatalog: {
      presets: [
        { id: 'z-build', agent: 'coder', stage: 'code', runnable: false, blockedReason: 'invalid tools' },
        { id: 'a-plan', agent: 'planner', stage: 'plan', runnable: true, blockedReason: '' },
      ],
      agents: [
        { name: 'reviewer-aux', displayLabel: 'Reviewer Aux', runnable: true, spawnAllowlist: [] },
        { name: 'planner', displayLabel: 'Planner', runnable: true, spawnAllowlist: ['reviewer-aux'] },
        { name: 'broken', displayLabel: 'Broken', runnable: false, blockedReason: 'bad frontmatter', spawnAllowlist: [] },
      ],
    },
  }), actions())

  assert.deepEqual(vm.pipelineBuilder.library.presets.map((item) => item.id), ['a-plan'])
  assert.deepEqual(vm.pipelineBuilder.library.agents.map((item) => item.name), ['planner'])
  assert.deepEqual(vm.pipelineBuilder.library.spawnRoles.map((item) => item.name), [])
  assert.equal(vm.pipelineBuilder.library.presets[0].blocked, false)

  const all = buildViewModel(baseState({
    pipelineBuilderCatalog: {
      presets: [
        { id: 'z-blocked', runnable: false, blockedReason: 'unknown agent' },
        { id: 'a-plan', runnable: true, blockedReason: '' },
      ],
      agents: [
        { name: 'reviewer-aux', displayLabel: 'Reviewer Aux', runnable: true },
        { name: 'planner', displayLabel: 'Planner', runnable: true, spawnAllowlist: ['reviewer-aux'] },
        { name: 'broken', displayLabel: 'Broken', runnable: false, blockedReason: 'bad frontmatter' },
      ],
    },
  }), actions())
  assert.deepEqual(all.pipelineBuilder.library.presets.map((item) => item.id), ['a-plan', 'z-blocked'])
  assert.deepEqual(all.pipelineBuilder.library.agents.map((item) => item.name), ['broken', 'planner', 'reviewer-aux'])
  assert.deepEqual(all.pipelineBuilder.library.spawnRoles.map((item) => item.name), ['reviewer-aux'])
  assert.equal(all.pipelineBuilder.library.presets[1].blocked, true)
  assert.equal(all.pipelineBuilder.library.agents[0].blockedReason, 'bad frontmatter')
})

test('groups workers into parent runs newest first', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(1, 'session-old', 'done', 'planner'),
      agent(2, 'session-new', 'done', 'planner'),
      agent(3, 'session-new', 'running', 'coder'),
    ],
  }), actions())

  assert.equal(vm.runs.length, 2)
  assert.equal(vm.runs[0].id, 'session-new')
  assert.equal(vm.runs[0].workerCount, 2)
  assert.equal(vm.runs[0].statusLabel, 'running')
  // Chronological numbering: the newest run gets the highest number, the
  // oldest is R01 — even though the list is shown newest-first.
  assert.equal(vm.runs[0].orderLabel, 'R02')
  assert.equal(vm.runs[1].orderLabel, 'R01')
  assert.equal(vm.runs[1].id, 'session-old')
})

test('keeps prior chat sessions listed after New session changes the active id', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-project-new',
    orchestratorSessions: [
      {
        chatId: 'gui-orchestrator-project-new', title: 'new request', lastRequest: 'new request',
        createdAt: '200', updatedAt: '201', rootTurns: 1, workers: 0,
      },
      {
        chatId: 'gui-orchestrator-project-old', title: 'old request', lastRequest: 'old request',
        createdAt: '100', updatedAt: '101', rootTurns: 1, workers: 1,
      },
    ],
    agentTurns: [
      { id: 1, chatId: 'gui-orchestrator-project-old', parentSession: 'root-old', request: 'old request', startedAt: 100 },
      { id: 2, chatId: 'gui-orchestrator-project-new', parentSession: 'root-new', request: 'new request', startedAt: 200 },
    ],
  }), actions())

  assert.deepEqual(vm.runs.map((session) => session.id), [
    'gui-orchestrator-project-new',
    'gui-orchestrator-project-old',
  ])
  assert.equal(vm.runs[0].orderLabel, 'S02')
  assert.equal(vm.runs[1].orderLabel, 'S01')
})

test('fresh empty session leaves retained history unselected until its first message', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-project-empty',
    orchestratorSessions: [{
      chatId: 'gui-orchestrator-project-old', title: 'old request', lastRequest: 'old request',
      createdAt: '100', updatedAt: '101', rootTurns: 1, workers: 0,
    }],
    agentTurns: [{
      id: 1, chatId: 'gui-orchestrator-project-old', parentSession: 'root-old',
      request: 'old request', startedAt: 100,
    }],
  }), actions())

  assert.deepEqual(vm.runs.map((session) => session.id), ['gui-orchestrator-project-old'])
  assert.equal(vm.activeRunId, '')
  assert.deepEqual(vm.activeRunSteps, [])
})

test('selected session agent flow starts at root then shows summoned workers', () => {
  const worker = agent(10, 'root-old', 'done', 'coder', 'gui-orchestrator-project-old')
  worker.brief = 'implement the page'
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-project-old',
    selectedSession: 'gui-orchestrator-project-old',
    orchestratorSessions: [{
      chatId: 'gui-orchestrator-project-old', title: 'build the dashboard',
      lastRequest: 'build the dashboard', createdAt: '100', updatedAt: '101',
      rootTurns: 1, workers: 1,
    }],
    agentTurns: [{
      id: 1, chatId: 'gui-orchestrator-project-old', parentSession: 'root-old',
      request: 'build the dashboard', startedAt: 100, modelFamily: 'deepseek', cycles: 2,
      tokensInEstimate: 100, tokensOutEstimate: 20,
    }],
    subagents: [worker],
  }), actions())

  assert.deepEqual(vm.activeRunSteps.map((step) => step.role), ['root', 'coder'])
  assert.equal(vm.activeRunSteps[0].brief, 'build the dashboard')
  assert.equal(vm.activeRunSteps[1].brief, 'implement the page')
})

test('driver-only session renders a completed root node instead of an empty flow', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-project-one',
    orchestratorSessions: [{
      chatId: 'gui-orchestrator-project-one', title: 'hello', lastRequest: 'hello',
      createdAt: '100', updatedAt: '101', rootTurns: 1, workers: 0,
    }],
    agentTurns: [{
      id: 1, chatId: 'gui-orchestrator-project-one', parentSession: 'root-one',
      request: 'hello', startedAt: 100, modelFamily: 'deepseek', cycles: 1,
      tokensInEstimate: 20, tokensOutEstimate: 10,
    }],
  }), actions())

  assert.deepEqual(vm.activeRunSteps.map((step) => step.role), ['root'])
  assert.equal(vm.activeRunSteps[0].brief, 'hello')
  assert.equal(vm.activeRunSteps[0].status, 'done')
})

test('formats epoch chat timestamps in the requested local timezone', () => {
  assert.equal(formatChatTimestamp('epoch:1735689600', 'en-GB', 'Asia/Saigon'), '07:00:00')
  assert.equal(formatChatTimestamp('16:39:01', 'en-GB', 'Asia/Saigon'), '16:39:01')
})

test('numbers visible runs instead of using worker count', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-current',
    subagents: [
      agent(205, 'session-a', 'done', 'planner', 'gui-orchestrator-current'),
      agent(206, 'session-a', 'done', 'coder', 'gui-orchestrator-current'),
      agent(207, 'session-a', 'done', 'reviewer', 'gui-orchestrator-current'),
    ],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-current',
      task: 'new request',
      startedAt: 99,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  // Every session is listed. The running turn is newest (R02); the completed
  // session-a before it is R01. Label is a run ordinal, not the worker count (3).
  assert.equal(vm.runs.length, 2)
  assert.equal(vm.runs[0].id, 'driver-running-99')
  assert.equal(vm.runs[0].orderLabel, 'R02')
  assert.equal(vm.runs[1].id, 'session-a')
  assert.equal(vm.runs[1].orderLabel, 'R01')
})

test('chooses selected step parent session before newest running run', () => {
  const vm = buildViewModel(baseState({
    selected: 'h1',
    subagents: [
      agent(1, 'session-old', 'done', 'planner'),
      agent(2, 'session-new', 'running', 'coder'),
    ],
  }), actions())

  assert.equal(vm.activeRunId, 'session-old')
  assert.deepEqual(vm.activeRunSteps.map((s) => s.handle), ['h1'])
})

test('marks the running step as current and explains budget halts', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-current',
    subagents: [agent(1, 'session-a', 'running', 'coder', 'gui-orchestrator-current')],
    driverStatus: {
      running: false,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-current',
      task: '',
      startedAt: null,
      stdoutTail: '',
      stderrTail: 'TokenBudgetExhaustedError: agent token budget exhausted at preflight',
    },
  }), actions())

  assert.equal(vm.activeRunSteps[0].isCurrent, true)
  assert.match(vm.runStatusSummary, /Budget halted/)
  assert.match(vm.activeRunSteps[0].stopHint, /budget/i)
})

test('creates a parent run for a driver turn with no spawned workers', () => {
  const vm = buildViewModel(baseState({
    agentTurns: [{
      id: 42,
      parentSession: 'direct-session',
      startedAt: 1042,
      modelFamily: 'deepseek',
      cycles: 1,
      tokensInEstimate: 100,
      tokensOutEstimate: 20,
    }],
    chat: [
      { role: 'you', ts: '10:00:00', text: 'hello', tone: null },
      { role: 'driver', ts: '10:00:03', text: 'Hi!', tone: null },
    ],
  }), actions())

  assert.equal(vm.runs.length, 1)
  assert.equal(vm.runs[0].id, 'direct-session')
  assert.equal(vm.runs[0].workerCount, 0)
  assert.equal(vm.activeRunId, 'direct-session')
  assert.match(vm.runStatusSummary, /Driver turn completed/)
})

test('driver card aggregates selected-session token economics', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-one',
    orchestratorSessions: [{
      chatId: 'gui-orchestrator-one', title: 'one', lastRequest: 'work',
    }],
    agentTurns: [{
      id: 7,
      chatId: 'gui-orchestrator-one',
      parentSession: 'gui-session',
      startedAt: 1042,
      modelFamily: 'deepseek',
      cycles: 3,
      tokensInEstimate: 76743,
      tokensOutEstimate: 900,
    }],
    agentCycles: [
      { sessionId: 'gui-session', tokensIn: 1000, cachedInputTokens: 600, tokensOut: 100, lmMs: 100, tokenSource: 'provider', toolNames: ['musubi_grep', 'musubi_read_file'] },
      { sessionId: 'gui-session', tokensIn: 500, cachedInputTokens: 100, tokensOut: 20, lmMs: 50, tokenSource: 'provider', toolNames: ['musubi_grep'] },
      { sessionId: 'other-session', tokensIn: 9999, cachedInputTokens: 9999, tokensOut: 9999, lmMs: 9999, tokenSource: 'provider', toolNames: ['ignored'] },
    ],
  }), actions())

  assert.deepEqual(vm.driverSummary.economics, {
    cycles: 2, inputTokens: 1500, cachedInputTokens: 700,
    outputTokens: 120, lmMs: 150, tokenSource: 'provider',
    tools: [
      { name: 'musubi_grep', count: 2 },
      { name: 'musubi_read_file', count: 1 },
    ],
  })
})

test('driver card marks mixed and clamped cycle usage estimated', () => {
  const vm = buildViewModel(baseState({
    agentTurns: [{
      id: 8,
      parentSession: 'gui-session',
      startedAt: 1043,
      modelFamily: 'deepseek',
      cycles: 1,
      tokensInEstimate: 28333,
      tokensOutEstimate: 200,
    }],
    agentCycles: [
      { sessionId: 'gui-session', tokensIn: 100, cachedInputTokens: 150, tokensOut: -3, lmMs: -1, tokenSource: 'provider', toolNames: [] },
      { sessionId: 'gui-session', tokensIn: 50, cachedInputTokens: 20, tokensOut: 5, lmMs: 10, tokenSource: 'estimated', toolNames: [] },
    ],
  }), actions())

  assert.equal(vm.driverSummary.economics.cachedInputTokens, 120)
  assert.equal(vm.driverSummary.economics.outputTokens, 5)
  assert.equal(vm.driverSummary.economics.lmMs, 10)
  assert.equal(vm.driverSummary.economics.tokenSource, 'estimated')
})

test('legacy pipeline economics are not projected as an active Studio card', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-one',
    pipelineRuns: [
      { sessionId: 'pipe-1', chatId: 'gui-pipeline-one', pipelineName: 'feature-dev', startedAt: 1, status: 'success', stages: [] },
    ],
    agentCycles: [
      { sessionId: 'pipe-1', tokensIn: 300, cachedInputTokens: 100, tokensOut: 40, lmMs: 25, tokenSource: 'provider', toolNames: ['musubi_read_file'] },
    ],
  }), actions())

  assert.equal(vm.pipeRunSummary, undefined)
})

test('empty run economics has zero totals', () => {
  const vm = buildViewModel(baseState(), actions())
  assert.deepEqual(vm.driverSummary.economics, {
    cycles: 0, inputTokens: 0, cachedInputTokens: 0,
    outputTokens: 0, lmMs: 0, tokenSource: 'estimated', tools: [],
  })
})

test('summarizes the active run for the driver card', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-current',
    subagents: [
      agent(1, 'session-a', 'done', 'planner', 'gui-orchestrator-current'),
      agent(2, 'session-a', 'escalated', 'coder', 'gui-orchestrator-current'),
      agent(3, 'session-a', 'escalated', 'reviewer', 'gui-orchestrator-current'),
    ],
    driverStatus: {
      running: false,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-current',
      task: '',
      startedAt: null,
      stdoutTail: '',
      stderrTail: 'token budget halt: projected=202314/200000 tokens',
    },
  }), actions())

  assert.equal(vm.driverSummary.title, 'Run summary')
  assert.equal(vm.driverSummary.countLine, '3 steps - 1 done - 2 escalated')
  assert.equal(vm.driverSummary.focusLine, 'Blocked at reviewer')
  assert.equal(vm.driverSummary.alertLine, 'Budget halted before the next model call.')
  assert.match(vm.driverSummary.metaLine, /deepseek-v4-flash/)
})

test('explains repeated workers as retry attempts', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(1, 'session-a', 'escalated', 'coder'),
      agent(2, 'session-a', 'done', 'coder'),
    ],
  }), actions())

  assert.equal(vm.driverSummary.countLine, '2 steps - 1 done - 1 escalated')
  assert.equal(vm.driverSummary.focusLine, 'Coder retried: 1 done, 1 escalated')
  assert.equal(vm.activeRunSteps[0].attemptLabel, 'attempt 1/2')
  assert.equal(vm.activeRunSteps[1].attemptLabel, 'attempt 2/2')
})

test('does not expose legacy Studio execution projections', () => {
  const vm = buildViewModel(baseState(), actions())

  for (const field of ['pipeName', 'pipeStatusText', 'pipePresets', 'pipeStepsView', 'pipeChatBody']) {
    assert.equal(vm[field], undefined)
  }
})

test('keeps Orchestrator runs while hiding legacy pipeline run history', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(1, 'orch-session', 'done', 'planner', 'gui-orchestrator-abc'),
    ],
    agentTurns: [
      { id: 1, chatId: 'gui-orchestrator-abc', parentSession: 'orch-direct', startedAt: 1000 },
    ],
    pipelineRuns: [
      {
        sessionId: 'pipe-session', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev',
        brief: 'ship it', startedAt: 1001, status: 'success',
        stages: [agent(2, 'pipe-session', 'done', 'coder')],
      },
    ],
  }), actions())

  assert.deepEqual(vm.runs.map((run) => run.id), ['orch-direct', 'orch-session'])
  assert.equal(vm.pipeRuns, undefined)
})

test('does not project legacy Studio sessions beside current Orchestrator history', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-current',
    pipelineChatId: 'gui-pipeline-current',
    agentTurns: [
      { id: 1, chatId: 'gui-orchestrator-old', parentSession: 'orch-old', startedAt: 1 },
      { id: 2, chatId: 'gui-orchestrator-current', parentSession: 'orch-current', startedAt: 2 },
    ],
    pipelineRuns: [
      {
        sessionId: 'pipe-old', chatId: 'gui-pipeline-old', pipelineName: 'feature-dev',
        brief: 'old brief', startedAt: 10, status: 'success', stages: [agent(10, 'pipe-old', 'done', 'planner')],
      },
      {
        sessionId: 'pipe-current', chatId: 'gui-pipeline-current', pipelineName: 'feature-dev',
        brief: 'ship it', startedAt: 20, status: 'success',
        stages: [agent(11, 'pipe-current', 'done', 'planner'), agent(12, 'pipe-current', 'done', 'coder')],
      },
    ],
  }), actions())

  assert.deepEqual(vm.runs.map((run) => run.id), ['orch-current'])
  assert.equal(vm.pipeRuns, undefined)
})

test('legacy Pipeline surface driver is not exposed through Studio runtime controls', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    driverStatus: {
      running: true,
      surface: 'pipeline',
      chatId: 'gui-pipeline-current',
      task: 'pipeline task',
      startedAt: 77,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.runs.length, 0)
  assert.equal(vm.driverBusy, false)
  assert.equal(vm.sendDisabled, true)
  assert.equal(vm.sendMode, 'send')
  assert.match(vm.sendTitle, /Pipeline run is active/)
  assert.equal(vm.pipeRuns, undefined)
  assert.equal(vm.pipeChatBody, undefined)
})

test('chat view preserves message roles so a live process can anchor to the latest request', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-current',
    chat: [{ role: 'you', ts: '10:00:00', text: 'build it', tone: null }],
    pipeChat: [{ role: 'you', ts: '10:00:01', text: 'run it', tone: null }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-current',
      task: 'build it',
      startedAt: 88,
      stdoutTail: '[agent] working',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.driverBusy, true)
  assert.equal(vm.chat[0].role, 'you')
  assert.equal(vm.pipeChatBody, undefined)
  assert.match(vm.driverProcessLog, /working/)
})

test('legacy pipeline chat is not projected while Orchestrator owns process', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-current',
    chat: [{ role: 'driver', ts: '10:00:00', text: 'orchestrator answer', tone: null }],
    pipeChat: [{ role: 'driver', ts: '10:00:01', text: 'pipeline answer', tone: null }],
    pipeDraft: 'run pipeline',
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-current',
      task: 'orchestrator task',
      startedAt: 88,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.chat[0].text, 'orchestrator answer')
  assert.equal(vm.pipeChatBody, undefined)
  assert.equal(vm.driverBusy, true)
  // Send never becomes cancel in place; stopping is a labelled banner button.
  assert.equal(vm.sendMode, 'send')
})

test('pipeline studio does not expose a run rail or active timeline', () => {
  const vm = buildViewModel(baseState({
    pipelineRuns: [
      { sessionId: 'pipe-old', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'old', startedAt: 1, status: 'success', stages: [agent(11, 'pipe-old', 'done', 'planner')] },
      { sessionId: 'pipe-new', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'new', startedAt: 2, status: 'running', stages: [agent(12, 'pipe-new', 'running', 'coder')] },
    ],
  }), actions())

  assert.equal(vm.pipeRuns, undefined)
  assert.equal(vm.activePipeRunId, undefined)
  assert.equal(vm.activePipeRunSteps, undefined)
  assert.equal(vm.pipeRunSummary, undefined)
  assert.equal(vm.pipeSessionSubtitle, undefined)
})

test('pipeline studio does not project audited run history', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    pipelineRuns: [
      { sessionId: 'real-run', chatId: 'gui-pipeline-current', pipelineName: 'feature-dev', brief: 'retry', startedAt: 2, status: 'success', stages: [agent(12, 'real-run', 'done', 'planner')] },
    ],
    driverStatus: { running: true, surface: 'pipeline', chatId: 'gui-pipeline-current', task: 'retry', startedAt: 77, stdoutTail: '', stderrTail: '' },
  }), actions())

  assert.equal(vm.pipeRuns, undefined)
})

test('pipeline budget status stays out of builder-only Studio projection', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    pipelineRuns: [
      { sessionId: 'budget-run', chatId: 'gui-pipeline-current', pipelineName: 'feature-dev', brief: 'retry', startedAt: 2, status: 'escalated', stages: [agent(12, 'budget-run', 'escalated', 'coder')] },
    ],
    driverStatus: {
      running: false, surface: 'pipeline', chatId: 'gui-pipeline-current', task: 'retry', startedAt: 2,
      terminalStatus: 'budget_halted', stdoutTail: '', stderrTail: 'TokenBudgetExhaustedError',
    },
  }), actions())

  assert.equal(vm.pipeRuns, undefined)
  assert.equal(vm.pipeChatBody, undefined)
  assert.equal(vm.pipeRunSummary, undefined)
})

test('keeps a complete legacy pipeline snapshot out of the builder-only Studio model', () => {
  const vm = buildViewModel(baseState({
    pipeName: 'feature-dev',
    pipelineChatId: 'gui-pipeline-current',
    pipeSteps: [
      { uid: 1, role: 'planner', status: 'idle' },
      { uid: 2, role: 'designer', status: 'idle' },
      { uid: 3, role: 'coder', status: 'idle' },
      { uid: 4, role: 'reviewer', status: 'idle' },
    ],
    pipelineRuns: [
      { sessionId: 'run-abcdef', chatId: 'gui-pipeline-current', pipelineName: 'feature-dev', brief: 'ship it', startedAt: 1, status: 'success', stages: [] },
    ],
  }), actions())

  for (const field of ['pipeStepsView', 'pipeStageOverflowLabel', 'pipeSessionTitle']) {
    assert.equal(vm[field], undefined)
  }
})

test('legacy pipeline process logs are absent from builder-only Studio', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    logWindowOpen: true,
    driverStatus: {
      running: false, surface: 'pipeline', chatId: 'gui-pipeline-current', terminalStatus: 'failed', task: 'ship it',
      startedAt: 9, stdoutTail: '', stderrTail: 'failure details',
    },
  }), actions())

  assert.equal(vm.pipeChatBody, undefined)
  assert.equal(vm.driverProcessLog, '')
})

test('retained legacy pipeline log is not projected for another session', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-new',
    logWindowOpen: true,
    driverStatus: {
      running: false, surface: 'pipeline', chatId: 'gui-pipeline-old',
      terminalStatus: 'failed', task: 'old task', startedAt: 9,
      stdoutTail: '', stderrTail: 'old failure',
    },
  }), actions())

  assert.equal(vm.pipeChatBody, undefined)
})

test('retained legacy pipeline log is not projected for its old session', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    logWindowOpen: true,
    driverStatus: {
      running: false, surface: 'pipeline', chatId: 'gui-pipeline-current',
      terminalStatus: 'failed', task: 'current task', startedAt: 9,
      stdoutTail: '', stderrTail: 'current failure',
    },
  }), actions())

  assert.equal(vm.pipeChatBody, undefined)
})

test('retained orchestrator process belongs only to its exact session', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'gui-orchestrator-new',
    processOpen: true,
    logWindowOpen: true,
    driverStatus: {
      running: false, surface: 'orchestrator', chatId: 'gui-orchestrator-old',
      terminalStatus: 'failed', task: 'old task', startedAt: 9,
      stdoutTail: 'old output', stderrTail: '',
    },
  }), actions())

  assert.equal(vm.hasDriverLog, false)
  assert.equal(vm.logWindowOpen, false)
  assert.equal(vm.driverTask, '')
})

test('pipeline studio ignores selected legacy pipeline runtime session', () => {
  const vm = buildViewModel(baseState({
    selectedPipeSession: 'pipe-old',
    pipelineRuns: [
      { sessionId: 'pipe-old', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'old', startedAt: 1, status: 'success', stages: [agent(11, 'pipe-old', 'done', 'planner')] },
      { sessionId: 'pipe-new', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'new', startedAt: 2, status: 'running', stages: [agent(12, 'pipe-new', 'running', 'coder')] },
    ],
  }), actions())

  assert.equal(vm.activePipeRunId, undefined)
  assert.equal(vm.activePipeRunSteps, undefined)
  assert.equal(vm.pipeRuns, undefined)
})

test('lists every session but focuses the latest driver turn', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(190, 'old-session', 'escalated', 'coder'),
      agent(191, 'older-session', 'abandoned', 'planner'),
    ],
    agentTurns: [{
      id: 7,
      parentSession: 'latest-direct',
      startedAt: 1000,  // newer than the worker sessions' spawn epochs (190/191)
      modelFamily: 'deepseek',
      cycles: 1,
      tokensInEstimate: 12,
      tokensOutEstimate: 8,
    }],
  }), actions())

  // The full run history stays listed (no collapse), sorted by REAL time — the
  // latest driver-only turn sorts newest even though it comes from a different
  // audit table than the worker sessions...
  assert.deepEqual(vm.runs.map((run) => run.id), ['latest-direct', 'older-session', 'old-session'])
  // ...and it is the focused/active one by default.
  assert.equal(vm.activeRunId, 'latest-direct')
})

test('selecting a session focuses and highlights it', () => {
  const vm = buildViewModel(baseState({
    selectedSession: 'old-session',
    subagents: [
      agent(190, 'old-session', 'done', 'coder'),
      agent(191, 'new-session', 'running', 'planner'),
    ],
  }), actions())

  assert.equal(vm.activeRunId, 'old-session')
  const chosen = vm.runs.find((run) => run.id === 'old-session')
  // Selection is a flag the stylesheet renders as a neutral raise plus a blue
  // bar. Orange is reserved for the live run, which is a different session.
  assert.equal(chosen.selected, true)
  assert.equal(vm.runs.find((run) => run.id === 'new-session').selected, false)
})

test('historical session is read-only while another session owns the driver', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'live-session',
    selectedSession: 'old-session',
    orchestratorSessions: [
      {
        chatId: 'live-session', title: 'live', lastRequest: 'working',
        createdAt: '200', updatedAt: '201', rootTurns: 1, workers: 0,
      },
      {
        chatId: 'old-session', title: 'old', lastRequest: 'old request',
        createdAt: '100', updatedAt: '101', rootTurns: 1, workers: 0,
      },
    ],
    chat: [{ role: 'driver', text: 'old answer' }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'live-session',
      task: 'working',
      startedAt: 1,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.activeRunId, 'old-session')
  assert.equal(vm.chat[0].text, 'old answer')
  assert.equal(vm.viewingHistoricalSession, true)
  assert.equal(vm.sendDisabled, true)
  assert.equal(vm.inputDisabled, true)
  assert.match(vm.disabledText, /read-only/i)
})

test('historical session becomes resumable after the other run finishes', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'live-session',
    selectedSession: 'old-session',
    orchestratorSessions: [{
      chatId: 'old-session', title: 'old', lastRequest: 'old request',
      createdAt: '100', updatedAt: '101', rootTurns: 1, workers: 0,
    }],
    driverStatus: {
      running: false,
      surface: 'orchestrator',
      chatId: 'live-session',
      task: 'finished',
      startedAt: 1,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.viewingHistoricalSession, true)
  assert.equal(vm.sendDisabled, false)
  assert.equal(vm.inputDisabled, false)
  assert.equal(vm.disabledText, '')
})

test('active running session offers Stop in the banner, not a mutated send button', () => {
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'live-session',
    selectedSession: 'live-session',
    orchestratorSessions: [{
      chatId: 'live-session', title: 'live', lastRequest: 'working',
      createdAt: '200', updatedAt: '201', rootTurns: 1, workers: 0,
    }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'live-session',
      task: 'working',
      startedAt: 1,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.viewingHistoricalSession, false)
  // The destructive control is its own labelled button in the Now banner, so
  // the composer stops swapping glyph and colour under the cursor.
  assert.equal(vm.sendMode, 'send')
  assert.equal(vm.sendDisabled, true)
  assert.match(vm.sendTitle, /stop it from the banner/)
  assert.equal(typeof vm.onStopRun, 'function')

  // And the banner has everything it needs to answer "what is it doing now".
  assert.equal(vm.nowRun.running, true)
  assert.equal(vm.nowRun.startedAt, 1)
  assert.match(vm.nowRun.headline, /^Driver is /)
})

test('the Orchestrator nav button navigates, then toggles the sessions rail', () => {
  const calls = []
  const act = { ...actions(), setView: (v) => calls.push(['setView', v]), toggleSessions: () => calls.push(['toggle']) }

  // From another view it must navigate, or the rail toggle would strand you.
  buildViewModel(baseState({ view: 'audit' }), act).selOrch()
  assert.deepEqual(calls, [['setView', 'orchestrator']])

  // Already on Orchestrator, it toggles the pane beside it.
  calls.length = 0
  const onOrch = buildViewModel(baseState({ view: 'orchestrator' }), act)
  onOrch.selOrch()
  assert.deepEqual(calls, [['toggle']])
  assert.equal(onOrch.sessionsHidden, false)
  assert.equal(onOrch.orchNavTitle, 'Hide sessions')

  const hidden = buildViewModel(baseState({ view: 'orchestrator', sessionsHidden: true }), act)
  assert.equal(hidden.sessionsHidden, true)
  assert.equal(hidden.orchNavTitle, 'Show sessions')
  assert.equal(buildViewModel(baseState({ view: 'audit' }), act).orchNavTitle, 'Orchestrator')
})

test('rail groups sessions by what the operator would do about them', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(200, 'done-session', 'done', 'coder'),
      agent(201, 'stuck-session', 'escalated', 'planner'),
      agent(202, 'live-session', 'running', 'planner'),
    ],
  }), actions())

  assert.deepEqual(vm.railGroups.map((group) => group.label), ['Active', 'Needs you', 'Earlier'])
  assert.deepEqual(vm.railGroups.map((group) => group.runs.length), [1, 1, 1])
  assert.equal(vm.railGroups[0].runs[0].id, 'live-session')
  assert.equal(vm.railGroups[1].runs[0].id, 'stuck-session')
  assert.equal(vm.railGroups[2].runs[0].id, 'done-session')
})

test('a failed turn-less request does not masquerade as the live one', () => {
  // "No turn row" was used as the marker for "running", but a process that
  // fails to spawn also leaves ledger rows and no turn — that request would
  // park itself at the newest slot forever. The driver reports which request
  // it actually owns.
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'chat-live',
    selectedSession: 'chat-live',
    orchestratorSessions: [{ chatId: 'chat-live', title: 'calc', lastRequest: 'calc', rootTurns: 2, workers: 0 }],
    agentTurns: [
      { id: 1, requestId: 'req-old', chatId: 'chat-live', parentSession: 'root-1', request: 'first', startedAt: 1785166000 },
      { id: 2, requestId: 'req-new', chatId: 'chat-live', parentSession: 'root-2', request: 'third', startedAt: 1785166600 },
    ],
    runtimeLogEvents: [
      { id: 1, requestId: 'req-old', chatId: 'chat-live', seq: 1, ts: '1', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: 'first' },
      // Spawn failure: ledger rows, no turn row, and NOT the driver's request.
      { id: 2, requestId: 'req-dead', chatId: 'chat-live', seq: 1, ts: '2', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: 'spawn failed' },
      { id: 3, requestId: 'req-new', chatId: 'chat-live', seq: 1, ts: '3', source: 'host', stream: 'host', agentHandle: '', role: 'host', category: 'host', message: 'third' },
      { id: 4, requestId: 'req-live', chatId: 'chat-live', seq: 1, ts: '4', source: 'root', stream: 'stderr', agentHandle: '', role: 'root', category: 'output', message: 'working' },
    ],
    driverStatus: {
      running: true, surface: 'orchestrator', chatId: 'chat-live',
      requestId: 'req-live', task: 'calc', startedAt: 1785166900, stdoutTail: '', stderrTail: '',
    },
  }), actions())

  // Chronological by ledger rowid, with the driver's own request pinned last.
  assert.deepEqual(
    vm.runtimeGraph.requests.map((request) => request.requestId),
    ['req-old', 'req-dead', 'req-new', 'req-live'],
  )
})

test('the Now banner reports the driver run, not the session being read', () => {
  // Clicking a historical session mid-run used to source the actor and the act
  // from the session on screen, presenting a finished run's last log line as
  // live activity.
  const live = agent(300, 'root-live', 'running', 'planner', 'chat-live')
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'chat-live',
    selectedSession: 'chat-old',
    orchestratorSessions: [
      { chatId: 'chat-live', title: 'live', lastRequest: 'now', rootTurns: 1, workers: 1 },
      { chatId: 'chat-old', title: 'old', lastRequest: 'then', rootTurns: 1, workers: 0 },
    ],
    subagents: [live],
    runtimeLogEvents: [
      { id: 1, requestId: 'req-old', chatId: 'chat-old', seq: 1, ts: '1', source: 'root', stream: 'stderr', agentHandle: '', role: 'root', category: 'output', message: 'ANCIENT LINE' },
      { id: 2, requestId: 'req-live', chatId: 'chat-live', seq: 1, ts: '2', source: 'worker', stream: 'stderr', agentHandle: live.handle, role: 'planner', category: 'tools', message: 'glob current' },
    ],
    driverStatus: {
      running: true, surface: 'orchestrator', chatId: 'chat-live',
      requestId: 'req-live', task: 'now', startedAt: 500, stdoutTail: '', stderrTail: '',
    },
  }), actions())

  assert.equal(vm.nowRun.running, true)
  assert.equal(vm.nowRun.act, 'glob current')
  assert.equal(vm.nowRun.actor, 'Planner')
  // And it admits the run is not what you are looking at.
  assert.equal(vm.nowRun.viewingElsewhere, true)
  assert.equal(typeof vm.nowRun.onOpenRunningSession, 'function')
})

test('trust strip carries live counters rather than fixed claims', () => {
  const vm = buildViewModel(baseState({ allowCount: 14, denyCount: 0 }), actions())
  const byKey = Object.fromEntries(vm.trustCounters.map((row) => [row.key, row]))

  assert.equal(byKey.policy.value, '14 allow / 0 deny')
  assert.equal(byKey.policy.ok, true)
  assert.equal(byKey.substrate.value, '0 LM calls')

  // A deny must be visible the moment it lands — that is the whole point.
  const denied = buildViewModel(baseState({ allowCount: 14, denyCount: 2 }), actions())
  const policy = denied.trustCounters.find((row) => row.key === 'policy')
  assert.equal(policy.value, '14 allow / 2 deny')
  assert.equal(policy.ok, false)
})

test('audit and firewall counters read uncapped evidence, not display lists', () => {
  // `audit` is truncated to 120 rows Rust-side, so its length plateaus and the
  // counter stops being evidence. totalSpawned + totalDone is the real ledger
  // size: HI #8 writes one row per spawn and one per completion.
  const vm = buildViewModel(baseState({
    audit: new Array(120).fill(0).map((_, i) => ({ id: i, event: 'spawned', status: 'ok', role: 'coder', ts: '1' })),
    totalSpawned: 240,
    totalDone: 197,
    subagents: [
      agent(400, 'r', 'done', 'reviewer'),
      // A helper from the exploration split, not the firewalled evaluator.
      agent(401, 'r', 'done', 'reviewer-aux'),
      agent(402, 'r', 'done', 'coder'),
    ],
    pipelineRuns: [{
      sessionId: 'p1', chatId: 'c', pipelineName: 'feature-dev', startedAt: 1, status: 'success',
      // Only the last stage is firewalled by the runner's stage brief.
      stages: [agent(410, 'p1', 'done', 'planner'), agent(411, 'p1', 'done', 'reviewer')],
    }],
  }), actions())
  const byKey = Object.fromEntries(vm.trustCounters.map((row) => [row.key, row]))

  assert.equal(byKey.audit.value, '437 rows appended')
  // h400 (reviewer) + h411 (pipeline last stage); reviewer-aux is excluded.
  assert.equal(byKey.firewall.value, '2 evaluator isolated')
})

test('session economics cover every request, not only the latest', () => {
  // The panel is headed "This session", but a chat opens one parent session
  // per root turn and only the newest was counted.
  const vm = buildViewModel(baseState({
    orchestratorChatId: 'chat-1',
    selectedSession: 'chat-1',
    orchestratorSessions: [{ chatId: 'chat-1', title: 't', lastRequest: 'r', rootTurns: 2, workers: 0 }],
    agentTurns: [
      { id: 1, requestId: 'r1', chatId: 'chat-1', parentSession: 'root-1', request: 'first', startedAt: 100 },
      { id: 2, requestId: 'r2', chatId: 'chat-1', parentSession: 'root-2', request: 'second', startedAt: 200 },
    ],
    agentCycles: [
      { sessionId: 'root-1', workerId: 'w1', tokensIn: 1000, cachedInputTokens: 400, tokensOut: 100, lmMs: 1000, tokenSource: 'provider', toolNames: ['read'] },
      { sessionId: 'root-2', workerId: 'w2', tokensIn: 500, cachedInputTokens: 0, tokensOut: 50, lmMs: 500, tokenSource: 'provider', toolNames: ['write'] },
    ],
  }), actions())

  const economics = vm.driverSummary.economics
  assert.equal(economics.cycles, 2)
  assert.equal(economics.inputTokens, 1500)
  assert.equal(economics.outputTokens, 150)
  assert.equal(economics.lmMs, 1500)
  assert.deepEqual(economics.tools.map((tool) => tool.name).sort(), ['read', 'write'])
})

test('clearing a node selection does not evict the operator from a session', () => {
  const calls = []
  const act = {
    ...actions(),
    clearSelect: () => calls.push('clearSelect'),
    clearNodeSelect: () => calls.push('clearNodeSelect'),
  }
  const vm = buildViewModel(baseState({ selectedSession: 'old' }), act)

  // clearSelect drops selectedSession too, so the narrower action exists for
  // "deselect this node" — opening the session log, or selecting a request.
  vm.clearNodeSelect()
  assert.deepEqual(calls, ['clearNodeSelect'])
})

const destructiveRefusal = {
  role: 'driver',
  text: 'This would DELETE 3 file(s): build/a.js, build/b.js, build/c.js.\n\n'
    + 'To approve exactly this and nothing else, reply with: allow-a3f9c1',
}

test('a pending destructive refusal offers approve and reject beside the chat', () => {
  const calls = []
  const act = {
    ...actions(),
    approveDestructive: (token) => calls.push(['approve', token]),
    dismissApproval: (token) => calls.push(['dismiss', token]),
  }
  const vm = buildViewModel(baseState({ chat: [{ role: 'you', text: 'clean build' }, destructiveRefusal] }), act)

  assert.equal(vm.approval.token, 'allow-a3f9c1')
  assert.equal(vm.approval.summary, 'delete 3 file(s)')
  vm.approval.onApprove()
  assert.deepEqual(calls, [['approve', 'allow-a3f9c1']])
})

test('rejecting clears the offer without sending anything', () => {
  const state = { chat: [destructiveRefusal], dismissedApproval: 'allow-a3f9c1' }

  assert.equal(buildViewModel(baseState(state), actions()).approval, null)
})

test('approval obeys the composer, so a busy or read-only surface offers nothing', () => {
  // The button is the composer minus a keystroke. If the composer cannot send
  // — driver running, pipeline holding the driver, historical session — then
  // approving would either be dropped or land in the wrong conversation.
  const busy = baseState({
    chat: [destructiveRefusal],
    driverStatus: { running: true, surface: 'orchestrator', chatId: '', task: 'x', startedAt: 1, stdoutTail: '', stderrTail: '' },
  })
  const vm = buildViewModel(busy, actions())

  assert.equal(vm.sendDisabled, true)
  assert.equal(vm.approval, null)
})

test('an ordinary answer leaves the composer as the only way to reply', () => {
  const vm = buildViewModel(baseState({ chat: [{ role: 'driver', text: 'All done.' }] }), actions())

  assert.equal(vm.approval, null)
})
