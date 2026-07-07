import test from 'node:test'
import assert from 'node:assert/strict'
import { buildViewModel, formatChatTimestamp } from './viewModel.js'

function baseState(overrides = {}) {
  return {
    view: 'orchestrator',
    selected: null,
    selectedSession: null,
    paused: false,
    t: 3,
    auditFilter: 'all',
    draft: '',
    pipeChatOpen: false,
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
    movePipe() {},
    removePipe() {},
    addPipe() {},
    loadPreset() {},
    runPipe() {},
    stopPipe() {},
    resetPipe() {},
    clearPipe() {},
    openPipeChat() {},
    closePipeChat() {},
    selectProfile() {},
    toggleProcess() {},
    openProcessLog() {},
    closeProcessLog() {},
    clearDriverChat() {},
    onDraft() {},
    onDraftKey() {},
    sendChat() {},
    sendPipeChat() {},
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
  assert.match(vm.pipeStatusText, /Choose a pipeline preset/)
  assert.equal(vm.pipePresets.some((p) => p.name === 'feature-dev' && p.selected), false)
})

test('scopes orchestrator and pipeline runs by chat id surface', () => {
  const vm = buildViewModel(baseState({
    subagents: [
      agent(1, 'orch-session', 'done', 'planner', 'gui-orchestrator-abc'),
      agent(2, 'pipe-session', 'done', 'coder', 'gui-pipeline-abc'),
    ],
    agentTurns: [
      { id: 1, chatId: 'gui-orchestrator-abc', parentSession: 'orch-direct', startedAt: 1000 },
      { id: 2, chatId: 'gui-pipeline-abc', parentSession: 'pipe-direct', startedAt: 1001 },
    ],
  }), actions())

  assert.deepEqual(vm.runs.map((run) => run.id), ['orch-direct', 'orch-session'])
  assert.deepEqual(vm.pipeRuns.map((run) => run.id), ['pipe-direct', 'pipe-session'])
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
