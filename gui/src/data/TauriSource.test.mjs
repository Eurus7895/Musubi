import test from 'node:test'
import assert from 'node:assert/strict'
import TauriSource from './TauriSource.js'

function sourceWithActionSpy() {
  const source = new TauriSource({})
  const calls = []
  source._action = (kind, args) => calls.push({ kind, args })
  return { source, calls }
}

test('merges pipeline chat from backend snapshots', () => {
  const { source } = sourceWithActionSpy()

  source._mergeDomain({
    chat: [{ role: 'driver', text: 'orchestrator' }],
    pipeChat: [{ role: 'driver', text: 'pipeline' }],
  })

  assert.equal(source.state.chat[0].text, 'orchestrator')
  assert.equal(source.state.pipeChat[0].text, 'pipeline')
})

test('merges durable orchestrator session summaries from backend snapshots', () => {
  const { source } = sourceWithActionSpy()

  source._mergeDomain({
    orchestratorSessions: [{ chatId: 'gui-orchestrator-project-old', title: 'old request' }],
  })

  assert.equal(source.state.orchestratorSessions[0].title, 'old request')
})

test('selectSession switches the active backend session without deleting history', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorSessions: [{ chatId: 'gui-orchestrator-project-old', title: 'old request' }],
    chat: [{ role: 'driver', text: 'current answer' }],
    driverStatus: { running: false },
  })

  source.actions.selectSession('gui-orchestrator-project-old')

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.deepEqual(source.state.orchestratorSessions.map((session) => session.chatId), [
    'gui-orchestrator-project-old',
  ])
  assert.deepEqual(calls, [{
    kind: 'select_session',
    args: ['gui-orchestrator-project-old'],
  }])
})

test('selectSession browses history while the active session keeps running', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorChatId: 'gui-orchestrator-project-live',
    selectedSession: null,
    chat: [{ role: 'driver', text: 'live output' }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-project-live',
    },
  })

  source.actions.selectSession('gui-orchestrator-project-old')

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.deepEqual(source.state.chat, [])
  assert.deepEqual(calls, [{
    kind: 'select_session',
    args: ['gui-orchestrator-project-old'],
  }])
})

test('backend history snapshots preserve the locally selected session', () => {
  const { source } = sourceWithActionSpy()
  source._setLocal({ selectedSession: 'gui-orchestrator-project-old' })

  source._mergeDomain({
    viewedOrchestratorChatId: 'gui-orchestrator-project-old',
    chat: [{ role: 'driver', text: 'old answer' }],
  })

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.equal(
    source.state.viewedOrchestratorChatId,
    'gui-orchestrator-project-old',
  )
  assert.equal(source.state.chat[0].text, 'old answer')
})

test('sendPipelineTask passes brief and selected registered pipeline', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    pipeDraft: '  ship it  ',
    pipeName: 'feature-dev',
    pipeModified: false,
    pipelineCatalog: [{ name: 'feature-dev', runnable: true, stages: ['planner', 'coder'] }],
  })

  source.actions.sendPipelineTask()

  assert.deepEqual(calls, [{ kind: 'send_pipeline_task', args: ['ship it', 'feature-dev'] }])
  assert.equal(source.state.pipeDraft, '')
})

test('repeated backend snapshots preserve an unchanged studio composition', () => {
  const { source } = sourceWithActionSpy()
  const snapshot = {
    pipelineCatalog: [{ name: 'feature-dev', runnable: true, stages: ['planner', 'coder'] }],
  }

  source._mergeDomain(snapshot)
  const firstUids = source.state.pipeSteps.map((step) => step.uid)
  source._mergeDomain(snapshot)

  assert.deepEqual(source.state.pipeSteps.map((step) => step.uid), firstUids)
})

test('sendPipelineTask refuses a modified client-only composition', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    pipeDraft: 'ship it',
    pipeName: 'feature-dev',
    pipeModified: true,
    pipelineCatalog: [{ name: 'feature-dev', runnable: true, stages: ['planner', 'coder'] }],
  })

  source.actions.sendPipelineTask()

  assert.deepEqual(calls, [])
  assert.equal(source.state.pipeDraft, 'ship it')
})

test('clearPipeDriverChat clears pipeline chat only', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    chat: [{ role: 'driver', text: 'keep me' }],
    pipeChat: [{ role: 'driver', text: 'clear me' }],
    pipeDraft: 'draft',
    driverStatus: { running: false, surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
  })

  source.actions.clearPipeDriverChat()

  assert.deepEqual(source.state.chat.map((m) => m.text), ['keep me'])
  assert.deepEqual(source.state.pipeChat, [])
  assert.equal(source.state.pipeDraft, '')
  assert.deepEqual(calls, [{ kind: 'clear_driver_chat', args: ['pipeline'] }])
})

test('newSession re-mints the orchestrator session and clears the chat', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    chat: [{ role: 'driver', text: 'old turn' }],
    pipeChat: [{ role: 'driver', text: 'keep me' }],
    draft: 'draft',
    driverStatus: { running: false, surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
  })

  source.actions.newSession()

  assert.deepEqual(source.state.chat, [])
  assert.deepEqual(source.state.pipeChat.map((m) => m.text), ['keep me'])
  assert.equal(source.state.draft, '')
  assert.deepEqual(calls, [{ kind: 'new_session', args: ['orchestrator'] }])
})

test('newPipeSession re-mints the pipeline session only', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    chat: [{ role: 'driver', text: 'keep me' }],
    pipeChat: [{ role: 'driver', text: 'old turn' }],
    pipeDraft: 'draft',
    driverStatus: { running: false, surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
  })

  source.actions.newPipeSession()

  assert.deepEqual(source.state.chat.map((m) => m.text), ['keep me'])
  assert.deepEqual(source.state.pipeChat, [])
  assert.equal(source.state.pipeDraft, '')
  assert.deepEqual(calls, [{ kind: 'new_session', args: ['pipeline'] }])
})

test('newSession is a no-op while the agent is running', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    chat: [{ role: 'driver', text: 'busy' }],
    driverStatus: { running: true, surface: 'orchestrator', task: 't', startedAt: 1, stdoutTail: '', stderrTail: '' },
  })

  source.actions.newSession()

  assert.deepEqual(source.state.chat.map((m) => m.text), ['busy'])
  assert.deepEqual(calls, [])
})

test('openArtifact forwards the requested surface', () => {
  const { source, calls } = sourceWithActionSpy()

  source.actions.openArtifact('report.html', 'pipeline')

  assert.deepEqual(calls, [{ kind: 'open_artifact', args: ['report.html', 'pipeline'] }])
})

test('pipeline session selection stays local and clears with pipeline chat', () => {
  const { source } = sourceWithActionSpy()
  source._setLocal({
    selectedPipeSession: 'pipe-old',
    selectedSession: 'orch-old',
    pipeChat: [{ role: 'driver', text: 'pipeline answer' }],
    driverStatus: { running: false, surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
  })

  source.actions.selectPipeSession('pipe-new')
  assert.equal(source.state.view, 'pipeline')
  assert.equal(source.state.selectedPipeSession, 'pipe-new')
  assert.equal(source.state.selectedSession, 'orch-old')

  source.actions.clearPipeDriverChat()
  assert.equal(source.state.selectedPipeSession, null)
  assert.deepEqual(source.state.pipeChat, [])
})
