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

test('sendPipeChat calls pipeline backend action', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({ pipeDraft: '  run feature-dev  ' })

  source.actions.sendPipeChat()

  assert.deepEqual(calls, [{ kind: 'send_pipe_chat', args: ['run feature-dev'] }])
  assert.equal(source.state.pipeDraft, '')
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
