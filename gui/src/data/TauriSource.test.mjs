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

test('openArtifact forwards the requested surface', () => {
  const { source, calls } = sourceWithActionSpy()

  source.actions.openArtifact('report.html', 'pipeline')

  assert.deepEqual(calls, [{ kind: 'open_artifact', args: ['report.html', 'pipeline'] }])
})
