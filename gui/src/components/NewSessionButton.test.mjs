import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))

test('Orchestrator is the only session surface and Studio is builder-only', () => {
  const button = readFileSync(join(here, 'NewSessionButton.jsx'), 'utf8')
  const pipeline = readFileSync(join(here, '..', 'views', 'Pipeline.jsx'), 'utf8')
  const orchestrator = readFileSync(join(here, '..', 'views', 'Orchestrator.jsx'), 'utf8')
  const chatBody = readFileSync(join(here, 'ChatBody.jsx'), 'utf8')

  assert.match(button, /New session/)
  assert.match(button, /＋/)
  assert.match(button, /height:\s*32/)
  assert.match(button, /borderRadius:\s*9/)
  assert.match(button, /aria-label/)
  assert.match(orchestrator, /<NewSessionButton/)
  assert.doesNotMatch(pipeline, /NewSessionButton|New pipeline session|Chat · pipeline/)
  assert.match(pipeline, /New Pipeline/)
  assert.match(pipeline, /Save Pipeline/)
  assert.doesNotMatch(pipeline, /onClearDriverChat|closePipeChat|openPipeChat/)
  assert.doesNotMatch(pipeline, /onClearPipe/)
  assert.match(chatBody, /latestUserMessageIndex/)
  assert.doesNotMatch(orchestrator, /onClearDriverChat/)
  assert.match(orchestrator, />Sessions</)
  assert.match(orchestrator, /project conversations/)
  assert.match(orchestrator, />Runtime evidence</)
  assert.match(orchestrator, /Back to graph/)
  assert.match(orchestrator, /Request log/)
  assert.match(orchestrator, /Agent log/)
  assert.doesNotMatch(orchestrator, /Parent runs|Session unavailable/)
  assert.match(orchestrator, /TokenEconomics/)
  assert.doesNotMatch(orchestrator, /Step detail|VerboseEvidence/)
})
