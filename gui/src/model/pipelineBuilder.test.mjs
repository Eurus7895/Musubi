import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createPipelineDraft,
  addStage,
  moveStage,
  removeStage,
  updateStage,
  setStageSpawns,
  isDirty,
  validateDraft,
  requestTransition,
  confirmTransition,
  cancelTransition,
} from './pipelineBuilder.js'

const recipe = () => ({
  name: 'feature-dev',
  description: 'Feature pipeline',
  version: '1',
  baselineChecks: ['node --test'],
  correction: { enabled: true },
  stages: [
    { preset: 'planner', agent: 'planner', stage: 'plan', spawns: ['Researcher'] },
    { preset: 'coder', agent: 'coder', stage: 'build', spawns: ['Reviewer-Aux'] },
  ],
  resolvedContracts: [{ step: 'plan', allowedTools: ['Read'] }],
  findings: [{ severity: 'warning', message: 'backend-owned' }],
})

test('normalizes recipe-owned fields without mutating the input', () => {
  const input = recipe()
  const before = structuredClone(input)

  const draft = createPipelineDraft(input)

  assert.deepEqual(input, before)
  assert.deepEqual(draft.stages[0], {
    preset: 'planner', agent: 'planner', stage: 'plan', spawns: ['researcher'],
  })
  assert.deepEqual(draft.baselineChecks, ['node --test'])
  assert.deepEqual(draft.correction, { enabled: true })
  assert.equal('resolvedContracts' in draft, false)
  assert.equal('findings' in draft, false)
})

test('stage add move remove and update are immutable and preserve spawns', () => {
  const original = createPipelineDraft(recipe())
  const added = addStage(original, {
    preset: 'reviewer', agent: 'reviewer', stage: 'review', spawns: ['Evaluator'],
  }, 1)
  const moved = moveStage(added, 1, 0)
  const updated = updateStage(moved, 0, { stage: 'final-review' })
  const removed = removeStage(updated, 1)

  assert.deepEqual(original.stages.map((stage) => stage.stage), ['plan', 'build'])
  assert.deepEqual(added.stages.map((stage) => stage.stage), ['plan', 'review', 'build'])
  assert.deepEqual(moved.stages[0].spawns, ['evaluator'])
  assert.deepEqual(updated.stages[0].spawns, ['evaluator'])
  assert.deepEqual(removed.stages.map((stage) => stage.stage), ['final-review', 'build'])
  assert.notEqual(added, original)
  assert.notEqual(moved.stages, added.stages)
})

test('spawn roles lowercase and deduplicate without mutating inputs', () => {
  const draft = createPipelineDraft(recipe())
  const roles = ['Reviewer-Aux', ' reviewer-aux ', 'EVALUATOR']
  const before = [...roles]

  const next = setStageSpawns(draft, 0, roles)

  assert.deepEqual(roles, before)
  assert.deepEqual(next.stages[0].spawns, ['reviewer-aux', 'evaluator'])
  assert.deepEqual(draft.stages[0].spawns, ['researcher'])
})

test('dirty new close and switch transitions require confirmation', () => {
  const savedRecipe = createPipelineDraft(recipe())
  const draft = updateStage(savedRecipe, 0, { stage: 'changed' })
  const base = { step: 'edit', draft, savedRecipe, selectedStageIndex: 0, pendingTransition: null }

  for (const transition of [
    { type: 'new' },
    { type: 'close' },
    { type: 'switch', recipe: { ...recipe(), name: 'code-review' } },
  ]) {
    const requested = requestTransition(base, transition)
    assert.deepEqual(requested.draft, draft)
    assert.deepEqual(requested.pendingTransition, transition)

    const cancelled = cancelTransition(requested)
    assert.deepEqual(cancelled.draft, draft)
    assert.equal(cancelled.pendingTransition, null)

    const confirmed = confirmTransition(requested)
    assert.equal(confirmed.pendingTransition, null)
    assert.equal(isDirty(confirmed.draft, confirmed.savedRecipe), false)
    if (transition.type === 'switch') assert.equal(confirmed.draft.name, 'code-review')
    if (transition.type === 'close') assert.equal(confirmed.step, 'catalog')
  }
})

test('pristine transition applies immediately and client validation is advisory', () => {
  const savedRecipe = createPipelineDraft(recipe())
  const state = { step: 'edit', draft: savedRecipe, savedRecipe, selectedStageIndex: 0, pendingTransition: null }

  const switched = requestTransition(state, { type: 'switch', recipe: { ...recipe(), name: 'dev-lite' } })

  assert.equal(switched.draft.name, 'dev-lite')
  assert.equal(switched.pendingTransition, null)
  assert.equal(validateDraft(createPipelineDraft()).some((finding) => finding.field === 'name'), true)
  assert.equal(validateDraft(createPipelineDraft()).some((finding) => finding.field === 'stages'), true)
})
