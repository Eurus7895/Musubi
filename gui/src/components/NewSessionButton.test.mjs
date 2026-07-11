import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))

test('both chat surfaces use only the approved New session control', () => {
  const button = readFileSync(join(here, 'NewSessionButton.jsx'), 'utf8')
  const pipeline = readFileSync(join(here, '..', 'views', 'Pipeline.jsx'), 'utf8')
  const orchestrator = readFileSync(join(here, '..', 'views', 'Orchestrator.jsx'), 'utf8')
  const chatBody = readFileSync(join(here, 'ChatBody.jsx'), 'utf8')

  assert.match(button, /New session/)
  assert.match(button, /＋/)
  assert.match(button, /height:\s*32/)
  assert.match(button, /borderRadius:\s*9/)
  assert.match(button, /aria-label/)
  assert.match(pipeline, /<NewSessionButton/)
  assert.match(orchestrator, /<NewSessionButton/)
  assert.match(pipeline, /label="New pipeline session"/)
  assert.doesNotMatch(pipeline, /onClearDriverChat|closePipeChat|openPipeChat/)
  assert.doesNotMatch(pipeline, /onClearPipe/)
  assert.match(chatBody, /latestUserMessageIndex/)
  assert.doesNotMatch(orchestrator, /onClearDriverChat/)
})
