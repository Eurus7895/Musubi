const clone = (value) => value == null ? value : structuredClone(value)

function normalizeSpawns(roles = []) {
  return [...new Set((Array.isArray(roles) ? roles : [])
    .map((role) => String(role || '').trim().toLowerCase())
    .filter(Boolean))]
}

function normalizeStage(stage = {}) {
  return {
    preset: String(stage.preset || ''),
    agent: String(stage.agent || ''),
    stage: String(stage.stage || ''),
    spawns: normalizeSpawns(stage.spawns),
  }
}

export function createPipelineDraft(recipe = {}) {
  return {
    name: String(recipe.name || ''),
    description: String(recipe.description || ''),
    version: String(recipe.version || ''),
    baselineChecks: clone(Array.isArray(recipe.baselineChecks) ? recipe.baselineChecks : []),
    correction: clone(recipe.correction ?? null),
    stages: (Array.isArray(recipe.stages) ? recipe.stages : []).map(normalizeStage),
  }
}

export function addStage(draft, stage, index = draft.stages.length) {
  const stages = [...draft.stages]
  const target = Math.max(0, Math.min(Number(index), stages.length))
  stages.splice(target, 0, normalizeStage(stage))
  return { ...draft, stages }
}

export function moveStage(draft, fromIndex, toIndex) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0
    || fromIndex >= draft.stages.length || toIndex >= draft.stages.length) return draft
  const stages = [...draft.stages]
  const [stage] = stages.splice(fromIndex, 1)
  stages.splice(toIndex, 0, stage)
  return { ...draft, stages }
}

export function removeStage(draft, index) {
  if (index < 0 || index >= draft.stages.length) return draft
  return { ...draft, stages: draft.stages.filter((_, current) => current !== index) }
}

export function updateStage(draft, index, patch) {
  if (index < 0 || index >= draft.stages.length) return draft
  const current = draft.stages[index]
  const next = normalizeStage({ ...current, ...clone(patch) })
  return {
    ...draft,
    stages: draft.stages.map((stage, currentIndex) => currentIndex === index ? next : stage),
  }
}

export function updateRecipe(draft, patch = {}) {
  const next = { ...draft }
  if (Object.hasOwn(patch, 'name')) next.name = String(patch.name || '')
  if (Object.hasOwn(patch, 'description')) next.description = String(patch.description || '')
  if (Object.hasOwn(patch, 'version')) next.version = String(patch.version || '')
  if (Object.hasOwn(patch, 'baselineChecks')) {
    next.baselineChecks = clone(Array.isArray(patch.baselineChecks) ? patch.baselineChecks : [])
  }
  if (Object.hasOwn(patch, 'correction')) next.correction = clone(patch.correction ?? null)
  return next
}

export function setStageSpawns(draft, index, roles) {
  return updateStage(draft, index, { spawns: normalizeSpawns(roles) })
}

export function isDirty(draft, savedRecipe) {
  return JSON.stringify(createPipelineDraft(draft)) !== JSON.stringify(createPipelineDraft(savedRecipe))
}

export function validateDraft(draft) {
  const findings = []
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(draft?.name || '')) {
    findings.push({ severity: 'error', step: 'recipe', field: 'name', message: 'Use a lowercase safe recipe name.' })
  }
  if (!Array.isArray(draft?.stages) || draft.stages.length < 2) {
    findings.push({ severity: 'error', step: 'recipe', field: 'stages', message: 'Add at least two stages.' })
  }
  return findings
}

function applyTransition(state, transition) {
  const recipe = transition?.recipe || {}
  if (transition?.type === 'close') {
    const empty = createPipelineDraft()
    return {
      ...state, step: 'catalog', draft: empty, savedRecipe: empty,
      selectedStageIndex: null, findings: [], saveResult: null,
      pendingTransition: null,
    }
  }
  if (transition?.type === 'new' || transition?.type === 'switch') {
    const draft = createPipelineDraft(recipe)
    const savedRecipe = clone(transition.savedRecipe ?? draft)
    return {
      ...state, step: 'edit', draft, savedRecipe, selectedStageIndex: null,
      findings: clone(savedRecipe.findings || []), saveResult: null,
      pendingTransition: null,
    }
  }
  return { ...state, pendingTransition: null }
}

export function requestTransition(state, transition) {
  if (isDirty(state.draft, state.savedRecipe)) return { ...state, pendingTransition: clone(transition) }
  return applyTransition(state, transition)
}

export function confirmTransition(state) {
  if (!state.pendingTransition) return state
  return applyTransition(state, state.pendingTransition)
}

export function cancelTransition(state) {
  return state.pendingTransition ? { ...state, pendingTransition: null } : state
}
