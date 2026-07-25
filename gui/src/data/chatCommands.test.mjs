import test from 'node:test'
import assert from 'node:assert/strict'
import { classifyChatCommand, pipelineNameFromCommand } from './chatCommands.js'

test('opens pipeline picker for ambiguous pipeline command', () => {
  assert.deepEqual(classifyChatCommand('pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('/pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('run pipeline'), { kind: 'openPipelinePicker' })
})

test('accepts the pipeline command after conversational filler', () => {
  // The traced conversation typed exactly this after being told to run a
  // pipeline, and the exact-match classifier sent it to the agent instead.
  assert.deepEqual(classifyChatCommand('ok then run pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('OK, run the pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('yes please start the pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('run pipeline?'), { kind: 'openPipelinePicker' })
})

test('reads the pipeline named inline, with or without filler', () => {
  assert.equal(pipelineNameFromCommand('run pipeline feature-dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('ok then run pipeline feature-dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('/pipeline code-review'), 'code-review')
  assert.equal(pipelineNameFromCommand('create a dashboard'), '')
})

test('leaves normal chat requests for the driver agent', () => {
  assert.deepEqual(classifyChatCommand('create a dashboard'), { kind: 'sendToAgent' })
  assert.deepEqual(classifyChatCommand('/pipeline feature-dev create a dashboard'), { kind: 'sendToAgent' })
  // A real work order that merely mentions a pipeline must never be hijacked.
  assert.deepEqual(classifyChatCommand('add a pipeline stage to the runner'), { kind: 'sendToAgent' })
  assert.deepEqual(classifyChatCommand('explain the pipeline runner'), { kind: 'sendToAgent' })
})
