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
    driverStatus: { running: false, surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
    agentTurns: [],
    pipelineRuns: [],
    pipelineCatalog: [],
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

test('formats epoch chat timestamps in the requested local timezone', () => {
  assert.equal(formatChatTimestamp('epoch:1735689600', 'en-GB', 'Asia/Saigon'), '07:00:00')
  assert.equal(formatChatTimestamp('16:39:01', 'en-GB', 'Asia/Saigon'), '16:39:01')
})

test('numbers visible runs instead of using worker count', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(205, 'session-a', 'done', 'planner'),
      agent(206, 'session-a', 'done', 'coder'),
      agent(207, 'session-a', 'done', 'reviewer'),
    ],
    driverStatus: {
      running: true,
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
    subagents: [agent(1, 'session-a', 'running', 'coder')],
    driverStatus: {
      running: false,
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

test('driver card surfaces the replayed seed cost of a stateful turn', () => {
  const vm = buildViewModel(baseState({
    agentTurns: [{
      id: 7,
      parentSession: 'gui-session',
      startedAt: 1042,
      modelFamily: 'deepseek',
      cycles: 3,
      tokensInEstimate: 76743,
      tokensOutEstimate: 900,
      replayMessages: 49,
      replayTokens: 48120,
    }],
  }), actions())

  assert.equal(vm.driverSummary.replayLine, 'replayed 49 msgs · 48k seed tok')
})

test('driver card shows no replay line for a fresh-session turn', () => {
  const vm = buildViewModel(baseState({
    agentTurns: [{
      id: 8,
      parentSession: 'gui-session',
      startedAt: 1043,
      modelFamily: 'deepseek',
      cycles: 1,
      tokensInEstimate: 28333,
      tokensOutEstimate: 200,
      replayMessages: 0,
      replayTokens: 0,
    }],
  }), actions())

  assert.equal(vm.driverSummary.replayLine, '')
})

test('summarizes the active run for the driver card', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(1, 'session-a', 'done', 'planner'),
      agent(2, 'session-a', 'escalated', 'coder'),
      agent(3, 'session-a', 'escalated', 'reviewer'),
    ],
    driverStatus: {
      running: false,
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

test('scopes orchestrator and pipeline runs by chat id surface', () => {
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
  assert.deepEqual(vm.pipeRuns.map((run) => run.id), ['pipe-session'])
})

test('scopes real pipeline runs to the exact current studio session', () => {
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
  assert.deepEqual(vm.pipeRuns.map((run) => run.id), ['pipe-current'])
  assert.equal(vm.pipeRuns[0].title, 'feature-dev')
  assert.equal(vm.pipeRuns[0].subtitle, '2 stages')
  assert.equal(vm.pipeRuns[0].currentBrief, 'ship it')
  assert.doesNotMatch(JSON.stringify(vm.pipeRuns), /driver-only turn/)
})

test('live driver run appears only on owning surface', () => {
  const vm = buildViewModel(baseState({
    driverStatus: {
      running: true,
      surface: 'pipeline',
      task: 'pipeline task',
      startedAt: 77,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.runs.length, 0)
  assert.deepEqual(vm.pipeRuns.map((run) => run.id), ['driver-running-77'])
  assert.equal(vm.driverBusy, false)
  assert.equal(vm.sendDisabled, true)
  assert.equal(vm.sendMode, 'send')
  assert.match(vm.sendTitle, /Pipeline run is active/)
  assert.equal(vm.pipeChatBody.driverBusy, true)
  assert.equal(vm.pipeChatBody.sendDisabled, false)
  assert.equal(vm.pipeChatBody.sendMode, 'cancel')
})

test('chat view preserves message roles so a live process can anchor to the latest request', () => {
  const vm = buildViewModel(baseState({
    chat: [{ role: 'you', ts: '10:00:00', text: 'build it', tone: null }],
    pipeChat: [{ role: 'you', ts: '10:00:01', text: 'run it', tone: null }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      task: 'build it',
      startedAt: 88,
      stdoutTail: '[agent] working',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.driverBusy, true)
  assert.equal(vm.chat[0].role, 'you')
  assert.equal(vm.pipeChatBody.chat[0].role, 'you')
  assert.match(vm.driverProcessLog, /working/)
})

test('pipeline chat body uses pipe chat and disables while orchestrator owns process', () => {
  const vm = buildViewModel(baseState({
    chat: [{ role: 'driver', ts: '10:00:00', text: 'orchestrator answer', tone: null }],
    pipeChat: [{ role: 'driver', ts: '10:00:01', text: 'pipeline answer', tone: null }],
    pipeDraft: 'run pipeline',
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      task: 'orchestrator task',
      startedAt: 88,
      stdoutTail: '',
      stderrTail: '',
    },
  }), actions())

  assert.equal(vm.chat[0].text, 'orchestrator answer')
  assert.equal(vm.pipeChatBody.chat[0].text, 'pipeline answer')
  assert.equal(vm.pipeChatBody.draft, 'run pipeline')
  assert.equal(vm.pipeChatBody.driverBusy, false)
  assert.equal(vm.pipeChatBody.sendDisabled, true)
  assert.match(vm.pipeChatBody.sendTitle, /Orchestrator run is active/)
  assert.equal(vm.driverBusy, true)
  assert.equal(vm.sendMode, 'cancel')
})

test('pipeline studio exposes scoped run rail and active timeline', () => {
  const vm = buildViewModel(baseState({
    pipelineRuns: [
      { sessionId: 'pipe-old', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'old', startedAt: 1, status: 'success', stages: [agent(11, 'pipe-old', 'done', 'planner')] },
      { sessionId: 'pipe-new', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'new', startedAt: 2, status: 'running', stages: [agent(12, 'pipe-new', 'running', 'coder')] },
    ],
  }), actions())

  assert.deepEqual(vm.pipeRuns.map((run) => run.id), ['pipe-new', 'pipe-old'])
  assert.equal(vm.activePipeRunId, 'pipe-new')
  assert.deepEqual(vm.activePipeRunSteps.map((step) => step.handle), ['h12'])
  assert.equal(vm.activePipeRunSteps[0].isCurrent, true)
  assert.match(vm.pipeRunSummary.countLine, /1 steps/)
  assert.match(vm.pipeSessionSubtitle, /1 workers/)
})

test('pipeline studio does not synthesize history after an audited run exists', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    pipelineRuns: [
      { sessionId: 'real-run', chatId: 'gui-pipeline-current', pipelineName: 'feature-dev', brief: 'retry', startedAt: 2, status: 'success', stages: [agent(12, 'real-run', 'done', 'planner')] },
    ],
    driverStatus: { running: true, surface: 'pipeline', task: 'retry', startedAt: 77, stdoutTail: '', stderrTail: '' },
  }), actions())

  assert.deepEqual(vm.pipeRuns.map((run) => run.id), ['real-run'])
})

test('pipeline run uses the exited driver budget status instead of active-worker copy', () => {
  const vm = buildViewModel(baseState({
    pipelineChatId: 'gui-pipeline-current',
    pipelineRuns: [
      { sessionId: 'budget-run', chatId: 'gui-pipeline-current', pipelineName: 'feature-dev', brief: 'retry', startedAt: 2, status: 'escalated', stages: [agent(12, 'budget-run', 'escalated', 'coder')] },
    ],
    driverStatus: {
      running: false, surface: 'pipeline', task: 'retry', startedAt: 2,
      terminalStatus: 'budget_halted', stdoutTail: '', stderrTail: 'TokenBudgetExhaustedError',
    },
  }), actions())

  assert.equal(vm.pipeRuns[0].status, 'budget_halted')
  assert.equal(vm.pipeChatBody.driverBusy, false)
  assert.match(vm.pipeRunSummary.alertLine, /Budget halted/)
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
  assert.equal(vm.pipeSessionTitle, 'feature-dev · run run-abcdef')
})

test('completed process logs stay available only on their owning surface', () => {
  const vm = buildViewModel(baseState({
    logWindowOpen: true,
    driverStatus: {
      running: false, surface: 'pipeline', terminalStatus: 'failed', task: 'ship it',
      startedAt: 9, stdoutTail: '', stderrTail: 'failure details',
    },
  }), actions())

  assert.equal(vm.pipeChatBody.driverBusy, false)
  assert.match(vm.pipeChatBody.driverProcessLog, /failure details/)
  assert.equal(vm.pipeChatBody.hasDriverLog, true)
  assert.equal(vm.pipeChatBody.logWindowOpen, true)
  assert.equal(vm.driverProcessLog, '')
})

test('pipeline studio honours selected pipeline session', () => {
  const vm = buildViewModel(baseState({
    selectedPipeSession: 'pipe-old',
    pipelineRuns: [
      { sessionId: 'pipe-old', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'old', startedAt: 1, status: 'success', stages: [agent(11, 'pipe-old', 'done', 'planner')] },
      { sessionId: 'pipe-new', chatId: 'gui-pipeline-abc', pipelineName: 'feature-dev', brief: 'new', startedAt: 2, status: 'running', stages: [agent(12, 'pipe-new', 'running', 'coder')] },
    ],
  }), actions())

  assert.equal(vm.activePipeRunId, 'pipe-old')
  assert.deepEqual(vm.activePipeRunSteps.map((step) => step.handle), ['h11'])
  const chosen = vm.pipeRuns.find((run) => run.id === 'pipe-old')
  assert.ok(chosen.cardStyle.includes('#ff9b3d'))
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
