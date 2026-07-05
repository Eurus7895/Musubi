import test from 'node:test'
import assert from 'node:assert/strict'
import { buildViewModel } from './viewModel.js'

function baseState(overrides = {}) {
  return {
    view: 'orchestrator',
    selected: null,
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
    driverStatus: { running: false, task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
    agentTurns: [],
    ...overrides,
  }
}

function agent(id, parentSession, status, role = 'coder') {
  return {
    id,
    handle: `h${id}`,
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
  }
}

function actions() {
  return {
    setView() {},
    selectAgent() {},
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
    cancelAgent() {},
    openArtifact() {},
    onPipeDraft() {},
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
  assert.equal(vm.runs[1].id, 'session-old')
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

test('does not treat a pipeline preset as selected by default', () => {
  const vm = buildViewModel(baseState(), actions())

  assert.equal(vm.pipeName, 'choose preset')
  assert.match(vm.pipeStatusText, /Choose a pipeline preset/)
  assert.equal(vm.pipePresets.some((p) => p.name === 'feature-dev' && p.selected), false)
})
