import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('./Pipeline.jsx', import.meta.url), 'utf8')

test('Pipeline Studio exposes the four builder steps and persistent recipe actions', () => {
  for (const text of ['Basics', 'Stages', 'Handoffs', 'Validate', 'New Pipeline', 'Save Pipeline']) {
    assert.match(source, new RegExp(text))
  }
  assert.match(source, /onUpdateRecipe/)
  assert.match(source, /onSave/)
  assert.match(source, /onNew/)
})

test('Pipeline Studio owns stage drag-drop and nested May spawn editing', () => {
  assert.match(source, /draggable/)
  assert.match(source, /onDragStart/)
  assert.match(source, /onDrop/)
  assert.match(source, /onAddStage/)
  assert.match(source, /onMoveStage/)
  assert.match(source, /onAddSpawn/)
  assert.match(source, /May spawn/)
  assert.match(source, /Runs in parallel only when summoned in the same worker turn\./)
})

test('Pipeline Studio contains no execution chat or run-history surface', () => {
  for (const legacy of ['Chat · pipeline', 'New pipeline session', 'Studio runs', 'Pipeline run history', 'sendPipelineTask']) {
    assert.equal(source.includes(legacy), false, legacy)
  }
})

test('Stages does not duplicate the Validate topology preview', () => {
  assert.match(source, /data-step="stages"/)
  assert.match(source, /data-step="validate"/)
  assert.equal((source.match(/Final recipe topology/g) || []).length, 1)
})

test('Pipeline Studio preserves runtime correction keys and guards drag reorder payloads', () => {
  assert.match(source, /correction\.max_retries/)
  assert.doesNotMatch(source, /correction\.maxAttempts/)
  assert.match(source, /if \(!raw\) return/)
})
