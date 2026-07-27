import test from 'node:test'
import assert from 'node:assert/strict'
import { classifyChatCommand, pipelineNameFromCommand } from './chatCommands.js'

const picker = { kind: 'openPipelinePicker' }
const agent = { kind: 'sendToAgent' }

// ── Picker commands ────────────────────────────────────────────────────

test('opens pipeline picker for ambiguous pipeline command', () => {
  assert.deepEqual(classifyChatCommand('pipeline'), picker)
  assert.deepEqual(classifyChatCommand('/pipeline'), picker)
  assert.deepEqual(classifyChatCommand('run pipeline'), picker)
})

test('every registered picker phrasing reaches the picker', () => {
  // Locks the PIPELINE_COMMANDS set itself. A phrasing dropped from the set
  // is a silent regression: the message goes to the driver agent as a work
  // order instead, costing a planner round trip.
  for (const phrase of [
    'pipeline', '/pipeline', 'the pipeline',
    'run pipeline', 'run the pipeline',
    'start pipeline', 'start the pipeline',
    'use pipeline', 'use the pipeline',
    'open pipeline', 'open the pipeline',
  ]) {
    assert.deepEqual(classifyChatCommand(phrase), picker, phrase)
  }
})

test('accepts the pipeline command after conversational filler', () => {
  // The traced conversation typed exactly this after being told to run a
  // pipeline, and the exact-match classifier sent it to the agent instead.
  assert.deepEqual(classifyChatCommand('ok then run pipeline'), picker)
  assert.deepEqual(classifyChatCommand('OK, run the pipeline'), picker)
  assert.deepEqual(classifyChatCommand('yes please start the pipeline'), picker)
  assert.deepEqual(classifyChatCommand('run pipeline?'), picker)
})

test('filler stripping handles stacked tokens, commas and contractions', () => {
  assert.deepEqual(classifyChatCommand('ok, then, run pipeline'), picker)
  assert.deepEqual(classifyChatCommand('yeah sure lets just run pipeline'), picker)
  assert.deepEqual(classifyChatCommand("let's run the pipeline"), picker)
  assert.deepEqual(classifyChatCommand('lets use pipeline'), picker)
})

test('normalization absorbs case, inner whitespace and trailing punctuation', () => {
  assert.deepEqual(classifyChatCommand('PIPELINE'), picker)
  assert.deepEqual(classifyChatCommand('Run The Pipeline'), picker)
  assert.deepEqual(classifyChatCommand('  run   pipeline  '), picker)
  assert.deepEqual(classifyChatCommand('pipeline.'), picker)
  assert.deepEqual(classifyChatCommand('pipeline?'), picker)
  assert.deepEqual(classifyChatCommand('run pipeline!'), picker)
})

// ── Inline pipeline names ──────────────────────────────────────────────

test('reads the pipeline named inline, with or without filler', () => {
  assert.equal(pipelineNameFromCommand('run pipeline feature-dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('ok then run pipeline feature-dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('/pipeline code-review'), 'code-review')
  assert.equal(pipelineNameFromCommand('create a dashboard'), '')
})

test('every named-pipeline prefix yields the same name', () => {
  for (const prefix of [
    '/pipeline', 'pipeline',
    'run pipeline', 'run the pipeline',
    'start pipeline', 'start the pipeline',
    'use pipeline', 'use the pipeline',
  ]) {
    assert.equal(pipelineNameFromCommand(`${prefix} feature-dev`), 'feature-dev', prefix)
  }
})

test('pipeline names keep hyphens and digits, and survive normalization', () => {
  assert.equal(pipelineNameFromCommand('pipeline code-review-v2'), 'code-review-v2')
  assert.equal(pipelineNameFromCommand('run pipeline v2'), 'v2')
  assert.equal(pipelineNameFromCommand('pipeline Feature-Dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('pipeline feature-dev?'), 'feature-dev')
})

test('a name-shaped tail that is not a bare identifier yields no name', () => {
  // Anything the recipe catalog cannot key on must fall through to the agent
  // rather than resolve to a half-parsed name.
  assert.equal(pipelineNameFromCommand('pipeline feature_dev'), '')
  assert.equal(pipelineNameFromCommand('pipeline -dev'), '')
  assert.equal(pipelineNameFromCommand('pipeline feature-'), '')
  assert.equal(pipelineNameFromCommand('run pipeline feature dev'), '')
  assert.equal(pipelineNameFromCommand('pipeline feature-dev please'), '')
})

test('naming a pipeline is not a picker command', () => {
  // TauriSource routes on the name, not on the picker verdict; the two must
  // stay distinguishable or an inline name would also pop the picker.
  assert.deepEqual(classifyChatCommand('pipeline feature-dev'), agent)
  assert.deepEqual(classifyChatCommand('/pipeline feature-dev create a dashboard'), agent)
})

// ── Everything else belongs to the driver agent ────────────────────────

test('leaves normal chat requests for the driver agent', () => {
  assert.deepEqual(classifyChatCommand('create a dashboard'), agent)
  assert.deepEqual(classifyChatCommand('/pipeline feature-dev create a dashboard'), agent)
  // A real work order that merely mentions a pipeline must never be hijacked.
  assert.deepEqual(classifyChatCommand('add a pipeline stage to the runner'), agent)
  assert.deepEqual(classifyChatCommand('explain the pipeline runner'), agent)
})

test('work orders that mention a pipeline claim no pipeline name', () => {
  for (const order of [
    'add a pipeline stage to the runner',
    'explain the pipeline runner',
    'why did the pipeline fail',
    'create a dashboard',
  ]) {
    assert.equal(pipelineNameFromCommand(order), '', order)
  }
})

test('degenerate input never reaches the pipeline path', () => {
  // sendChat passes the raw composer value through; a null/blank draft must
  // not throw and must not resolve to a pipeline.
  for (const value of [null, undefined, '', '   ', 'ok', 'ok ok ok', 'just']) {
    assert.deepEqual(classifyChatCommand(value), agent, JSON.stringify(value))
    assert.equal(pipelineNameFromCommand(value), '', JSON.stringify(value))
  }
})

// ── Known defects ──────────────────────────────────────────────────────
// These assert the behavior the module should have. They are marked `todo`
// so the suite records the gap on every run without failing CI over a
// pre-existing bug. Delete the `todo` marker when the fix lands.

test('an unknown one-word tail is not a pipeline name', { todo: 'unfixed: TauriSource clears the draft and drops the message' }, () => {
  // `pipelineNameFromCommand` accepts any single token after the prefix, so an
  // ordinary work order resolves to a name that is not in the recipe catalog.
  // TauriSource.js takes the `namedPipeline` branch on the truthy string,
  // fails the catalog lookup, then runs _setLocal({ draft: '', ... }) and
  // returns — the composer is wiped, nothing is sent, no error is shown.
  assert.equal(pipelineNameFromCommand('use the pipeline runner'), '')
  assert.equal(pipelineNameFromCommand('start pipeline stages'), '')
  assert.equal(pipelineNameFromCommand('ok pipeline design'), '')
  assert.equal(pipelineNameFromCommand('now pipeline again'), '')
})

test('every picker phrasing also accepts an inline name', { todo: 'unfixed: NAMED_PIPELINE omits the open/bare-the prefixes' }, () => {
  // PIPELINE_COMMANDS and NAMED_PIPELINE are two hand-maintained lists of the
  // same vocabulary. `open pipeline` opens the picker but `open pipeline
  // feature-dev` matches neither gate, so it ships to the driver agent as a
  // work order. Derive one from the other instead.
  assert.equal(pipelineNameFromCommand('open pipeline feature-dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('open the pipeline feature-dev'), 'feature-dev')
  assert.equal(pipelineNameFromCommand('the pipeline feature-dev'), 'feature-dev')
})
