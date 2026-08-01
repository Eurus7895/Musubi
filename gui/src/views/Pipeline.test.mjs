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

test('Pipeline Studio exposes governed stage and command ceilings', () => {
  for (const text of [
    'Max iterations', 'Allowed checks', 'Allowed commands',
    'Named commands', 'Exact argv', 'Timeout seconds',
  ]) assert.match(source, new RegExp(text))
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
  // The payload guard used to be an inline `if (!raw) return` this asserted by
  // spelling. A string match cannot tell a guard that works from one that
  // silently drops a gesture, which is what it was doing — see stageDrag.test.
  assert.match(source, /readStageDrop\(event\.dataTransfer\)/)
  assert.doesNotMatch(source, /getData\('application\/x-musubi/)
})

test('Pipeline Studio can open, clone and remove a saved recipe', () => {
  // `onLoad` existed in the view model from the start with nothing rendering
  // it, so a saved recipe could never be reopened. All four verbs are present.
  for (const action of ['onLoad', 'onClone', 'onDelete', 'onNew']) {
    assert.match(source, new RegExp(action))
  }
  assert.match(source, /Clone/)
  assert.match(source, /Remove/)
  // Removing deletes a directory, so it confirms first — the same pattern the
  // Orchestrator's Clean all uses.
  assert.match(source, /window\.confirm/)
  assert.match(source, /builder\.deletable/)
})

test('Pipeline Studio never offers to delete a repository-owned recipe', () => {
  // The backend refuses a musubi-tier-tagged recipe; the button says so before
  // the click rather than surfacing an error after it.
  assert.match(source, /Repository-owned recipe/)
  assert.match(source, /disabled=\{builder\.loading \|\| !builder\.deletable\}/)
})

test('Save Pipeline is offered once, from the header', () => {
  // Validate used to carry a second primary Save beside its Validate button,
  // so the Validate step showed two identical calls to action.
  assert.equal(source.match(/Save Pipeline/g).length, 1)
  assert.match(source, /validate-toolbar.*onClick=\{onValidate\}/s)
})

test('saved recipes are listed beside the identity form, not collapsed in the header', () => {
  assert.match(source, /className="basics-workspace"/)
  assert.match(source, /<RecipeList saved=\{saved\}/)
  // A select showed one name at a time; the list shows every recipe, its stage
  // count, and which ones the repository owns without being opened.
  assert.match(source, /recipe-list__item/)
  assert.match(source, /entry\.protected \? ' · repository' : ''/)
  assert.doesNotMatch(source, /<select/)
})
