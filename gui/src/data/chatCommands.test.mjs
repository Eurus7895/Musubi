import test from 'node:test'
import assert from 'node:assert/strict'
import { classifyChatCommand } from './chatCommands.js'

test('opens pipeline picker for ambiguous pipeline command', () => {
  assert.deepEqual(classifyChatCommand('pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('/pipeline'), { kind: 'openPipelinePicker' })
  assert.deepEqual(classifyChatCommand('run pipeline'), { kind: 'openPipelinePicker' })
})

test('leaves normal chat requests for the driver agent', () => {
  assert.deepEqual(classifyChatCommand('create a dashboard'), { kind: 'sendToAgent' })
  assert.deepEqual(classifyChatCommand('/pipeline feature-dev create a dashboard'), { kind: 'sendToAgent' })
})
