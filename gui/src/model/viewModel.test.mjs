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
    selectPipeSession() {},
    clearSelect() {},
    setAuditFilter() {},
    movePipe() {},
    removePipe() {},
    addPipe() {},
    loadPreset() {},
    runPipe() {},
    stopPipe() {},
    resetPipe() {},
    clearPipe() {},
    selectProfile() {},
    toggleProcess() {},
    openProcessLog() {},
    closeProcessLog() {},
    clearDriverChat() {},
    onDraft() {},
    onDraftKey() {},
    sendChat() {},
    sendPipelineTask() {},
    cancelAgent() {},
    openArtifact() {},
    onPipeDraft() {},
    onPipeDraftKey() {},
    clearPipeDriverChat() {},
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

test('does not treat a pipeline preset as selected by default', () => {
  const vm = buildViewModel(baseState(), actions())

  assert.equal(vm.pipeName, 'choose preset')
  assert.match(vm.pipeStatusText, /Choose a registered pipeline/)
  assert.equal(vm.pipePresets.some((p) => p.name === 'feature-dev' && p.selected), false)
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
  assert.equal(vm.sendMode, 'cancel')
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

test('pipeline flow exposes every configured stage and designer metadata', () => {
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

  assert.deepEqual(vm.pipeStepsView.map((step) => step.role), ['planner', 'designer', 'coder', 'reviewer'])
  assert.equal(vm.pipeStepsView[1].toolsLabel, '3 tools')
  assert.equal(vm.pipeStepsView[1].maxLabel, 'max 12 turns')
  assert.equal(vm.pipeStageOverflowLabel, '1 more stage →')
  assert.equal(vm.pipeSessionTitle, undefined)
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
  assert.ok(chosen.cardStyle.includes('#ff9b3d'))
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

test('active running session keeps cancel available', () => {
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
  assert.equal(vm.sendDisabled, false)
  assert.equal(vm.sendMode, 'cancel')
})
