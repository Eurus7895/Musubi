import test from 'node:test'
import assert from 'node:assert/strict'
import TauriSource from './TauriSource.js'

function sourceWithActionSpy() {
  const source = new TauriSource({})
  const calls = []
  source._action = (kind, args) => calls.push({ kind, args })
  return { source, calls }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
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

test('merges durable orchestrator session summaries from backend snapshots', () => {
  const { source } = sourceWithActionSpy()

  source._mergeDomain({
    orchestratorSessions: [{ chatId: 'gui-orchestrator-project-old', title: 'old request' }],
  })

  assert.equal(source.state.orchestratorSessions[0].title, 'old request')
})

test('selectSession switches the active backend session without deleting history', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorSessions: [{ chatId: 'gui-orchestrator-project-old', title: 'old request' }],
    chat: [{ role: 'driver', text: 'current answer' }],
    driverStatus: { running: false },
  })

  source.actions.selectSession('gui-orchestrator-project-old')

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.deepEqual(source.state.orchestratorSessions.map((session) => session.chatId), [
    'gui-orchestrator-project-old',
  ])
  assert.deepEqual(calls, [{
    kind: 'select_session',
    args: ['gui-orchestrator-project-old'],
  }])
})

test('selectSession browses history while the active session keeps running', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorChatId: 'gui-orchestrator-project-live',
    selectedSession: null,
    chat: [{ role: 'driver', text: 'live output' }],
    driverStatus: {
      running: true,
      surface: 'orchestrator',
      chatId: 'gui-orchestrator-project-live',
    },
  })

  source.actions.selectSession('gui-orchestrator-project-old')

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.deepEqual(source.state.chat, [])
  assert.deepEqual(calls, [{
    kind: 'select_session',
    args: ['gui-orchestrator-project-old'],
  }])
})

test('backend history snapshots preserve the locally selected session', () => {
  const { source } = sourceWithActionSpy()
  source._setLocal({ selectedSession: 'gui-orchestrator-project-old' })

  source._mergeDomain({
    viewedOrchestratorChatId: 'gui-orchestrator-project-old',
    chat: [{ role: 'driver', text: 'old answer' }],
  })

  assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
  assert.equal(
    source.state.viewedOrchestratorChatId,
    'gui-orchestrator-project-old',
  )
  assert.equal(source.state.chat[0].text, 'old answer')
})

test('immediate send after New ignores stale viewed session until new-session acknowledgement', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorChatId: 'gui-orchestrator-project-old',
    viewedOrchestratorChatId: 'gui-orchestrator-project-history',
    selectedSession: 'gui-orchestrator-project-history',
    driverStatus: { running: false },
  })

  source.actions.newSession()
  source._mergeDomain({
    orchestratorChatId: 'gui-orchestrator-project-old',
    viewedOrchestratorChatId: 'gui-orchestrator-project-history',
  })
  source._setLocal({ draft: 'start fresh' })
  source.actions.sendChat()

  assert.equal(source.state.viewedOrchestratorChatId, '')
  assert.deepEqual(calls, [
    { kind: 'new_session', args: ['orchestrator'] },
    { kind: 'send_chat', args: ['start fresh', '', 'direct', ''] },
  ])
})

test('immediate send after selecting history uses local selection before backend acknowledgement', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    orchestratorChatId: 'gui-orchestrator-project-current',
    viewedOrchestratorChatId: 'gui-orchestrator-project-current',
    driverStatus: { running: false },
  })

  source.actions.selectSession('gui-orchestrator-project-history')
  source._setLocal({ draft: 'continue history' })
  source.actions.sendChat()

  assert.deepEqual(calls, [
    { kind: 'select_session', args: ['gui-orchestrator-project-history'] },
    { kind: 'send_chat', args: ['continue history', 'gui-orchestrator-project-history', 'direct', ''] },
  ])
})

test('direct sendChat forwards exact four-argument contract and viewed chat id', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    draft: '  continue this session  ',
    selectedSession: 'gui-orchestrator-project-old',
    driverStatus: { running: false },
  })

  source.actions.sendChat()

  assert.deepEqual(calls, [{
    kind: 'send_chat',
    args: ['continue this session', 'gui-orchestrator-project-old', 'direct', ''],
  }])
  assert.equal(source.state.selectedSession, null)
  assert.equal(source.state.draft, '')
})

test('pipeline send requires a registered runnable recipe', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    draft: 'ship it',
    viewedOrchestratorChatId: 'gui-orchestrator-project-old',
    runMode: 'pipeline',
    selectedPipeline: 'feature-dev',
    pipelineCatalog: [{ name: 'feature-dev', runnable: false, stages: ['planner', 'coder'] }],
  })

  source.actions.sendChat()
  assert.deepEqual(calls, [])
  assert.equal(source.state.draft, 'ship it')

  source._setLocal({ pipelineCatalog: [{ name: 'feature-dev', runnable: true, stages: ['planner', 'coder'] }] })
  source.actions.sendChat()
  assert.deepEqual(calls, [{
    kind: 'send_chat',
    args: ['ship it', 'gui-orchestrator-project-old', 'pipeline', 'feature-dev'],
  }])
})

for (const [command, selectedPipeline] of [
  ['pipeline', ''],
  ['/pipeline', ''],
  ['pipeline feature-dev', 'feature-dev'],
  ['run pipeline feature-dev', 'feature-dev'],
]) {
  test(`pipeline command ${command} selects current Orchestrator composer`, () => {
    const { source, calls } = sourceWithActionSpy()
    source._setLocal({
      draft: command,
      selectedSession: 'gui-orchestrator-project-old',
      driverStatus: { running: false },
      pipelineCatalog: [{ name: 'feature-dev', runnable: true, stages: ['planner', 'coder'] }],
    })

    source.actions.sendChat()

    assert.equal(source.state.view, 'orchestrator')
    assert.equal(source.state.runMode, 'pipeline')
    assert.equal(source.state.selectedPipeline, selectedPipeline)
    assert.equal(source.state.selectedSession, 'gui-orchestrator-project-old')
    assert.equal(source.state.draft, '')
    assert.deepEqual(calls, [])
  })
}

test('backend polling preserves dirty builder drafts and initializes only pristine state', () => {
  const { source } = sourceWithActionSpy()

  source._mergeDomain({
    pipelineCatalog: [{ name: 'feature-dev', description: 'Feature flow', runnable: true, stages: ['planner', 'coder'] }],
  })

  assert.equal(source.state.pipelineBuilder.draft.name, 'feature-dev')
  assert.deepEqual(source.state.pipelineBuilder.draft.stages.map((stage) => stage.agent), ['planner', 'coder'])

  source._setLocal({
    pipelineBuilder: {
      ...source.state.pipelineBuilder,
      draft: { ...source.state.pipelineBuilder.draft, name: 'dirty-local' },
    },
  })

  source._mergeDomain({
    pipelineCatalog: [{ name: 'feature-dev', runnable: true, stages: ['planner', 'coder'] }],
  })

  assert.equal(source.state.pipelineBuilder.draft.name, 'dirty-local')
})

test('backend polling refreshes builder catalog without replacing a dirty draft', () => {
  const { source } = sourceWithActionSpy()
  source._setLocal({
    pipelineBuilder: {
      ...source.state.pipelineBuilder,
      draft: { ...source.state.pipelineBuilder.draft, name: 'dirty-local' },
    },
  })

  source._mergeDomain({
    pipelineBuilderCatalog: {
      presets: [{ id: 'plan', agent: 'planner', stage: 'plan', runnable: true }],
      agents: [{ name: 'planner', displayLabel: 'Planner', runnable: true }],
    },
  })

  assert.equal(source.state.pipelineBuilder.draft.name, 'dirty-local')
  assert.equal(source.state.pipelineBuilderCatalog.presets[0].id, 'plan')
})

test('newSession re-mints Orchestrator and defaults composer to Direct', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    chat: [{ role: 'driver', text: 'old turn' }],
    pipeChat: [{ role: 'driver', text: 'keep me' }],
    draft: 'draft',
    runMode: 'pipeline',
    selectedPipeline: 'feature-dev',
    driverStatus: { running: false, surface: 'orchestrator', task: '', startedAt: null, stdoutTail: '', stderrTail: '' },
  })

  source.actions.newSession()

  assert.deepEqual(source.state.chat, [])
  assert.deepEqual(source.state.pipeChat.map((m) => m.text), ['keep me'])
  assert.equal(source.state.draft, '')
  assert.equal(source.state.runMode, 'direct')
  assert.equal(source.state.selectedPipeline, '')
  assert.deepEqual(calls, [{ kind: 'new_session', args: ['orchestrator'] }])
})

test('newSession is a no-op while the agent is running', () => {
  const { source, calls } = sourceWithActionSpy()
  source._setLocal({
    chat: [{ role: 'driver', text: 'busy' }],
    driverStatus: { running: true, surface: 'orchestrator', task: 't', startedAt: 1, stdoutTail: '', stderrTail: '' },
  })

  source.actions.newSession()

  assert.deepEqual(source.state.chat.map((m) => m.text), ['busy'])
  assert.deepEqual(calls, [])
})

test('openArtifact forwards the requested surface', () => {
  const { source, calls } = sourceWithActionSpy()

  source.actions.openArtifact('report.html', 'pipeline')

  assert.deepEqual(calls, [{ kind: 'open_artifact', args: ['report.html', 'pipeline'] }])
})

test('recipe load validate and save invoke exact Tauri commands and capture success', async () => {
  const source = new TauriSource({})
  const recipe = { name: 'feature-dev', stages: [{ agent: 'planner' }, { agent: 'coder' }] }
  const calls = []
  source._invoke = async (command, args) => {
    calls.push({ command, args })
    if (command === 'load_pipeline_recipe') return recipe
    if (command === 'validate_pipeline_recipe') return [{ severity: 'warning', message: 'advisory' }]
    return { saved: true, catalogRefreshed: true, path: 'pipeline.yaml', findings: [], error: '' }
  }

  await source.actions.loadPipelineRecipe('feature-dev')
  await source.actions.validatePipelineRecipe()
  await source.actions.savePipelineRecipe()

  assert.deepEqual(calls.map((call) => call.command), [
    'load_pipeline_recipe', 'validate_pipeline_recipe', 'save_pipeline_recipe',
  ])
  assert.deepEqual(calls[0].args, { name: 'feature-dev' })
  assert.equal(calls[1].args.recipe.name, 'feature-dev')
  assert.deepEqual(calls[1].args.recipe.resolvedContracts, [])
  assert.equal(calls[2].args.recipe.findings[0].message, 'advisory')
  assert.equal(source.state.pipelineBuilder.savedRecipe.name, 'feature-dev')
  assert.deepEqual(source.state.pipelineBuilder.findings, [])
  assert.equal(source.state.pipelineBuilder.saveResult.saved, true)
})

test('recipe command failures are recorded in builder state', async () => {
  const source = new TauriSource({})
  source._invoke = async () => { throw new Error('IPC unavailable') }

  await source.actions.loadPipelineRecipe('feature-dev')
  assert.match(source.state.pipelineBuilder.findings[0].message, /IPC unavailable/)
  await source.actions.validatePipelineRecipe()
  assert.match(source.state.pipelineBuilder.findings[0].message, /IPC unavailable/)
  await source.actions.savePipelineRecipe()
  assert.match(source.state.pipelineBuilder.saveResult.error, /IPC unavailable/)
})

test('out-of-order recipe loads keep the newest result and loading until all requests settle', async () => {
  const source = new TauriSource({})
  const first = deferred()
  const second = deferred()
  source._invoke = (_command, { name }) => name === 'first' ? first.promise : second.promise

  const firstRequest = source.actions.loadPipelineRecipe('first')
  const secondRequest = source.actions.loadPipelineRecipe('second')
  second.resolve({ name: 'second', stages: [{ agent: 'planner' }, { agent: 'coder' }], resolvedContracts: [], findings: [] })
  await secondRequest

  assert.equal(source.state.pipelineBuilder.draft.name, 'second')
  assert.equal(source.state.pipelineBuilder.loading, true)

  first.resolve({ name: 'first', stages: [{ agent: 'planner' }, { agent: 'coder' }], resolvedContracts: [], findings: [] })
  await firstRequest

  assert.equal(source.state.pipelineBuilder.draft.name, 'second')
  assert.equal(source.state.pipelineBuilder.loading, false)
})

test('overlapping validation and save ignore stale validation completion', async () => {
  const source = new TauriSource({})
  const validation = deferred()
  const save = deferred()
  source._invoke = (command) => command === 'validate_pipeline_recipe' ? validation.promise : save.promise

  const validationRequest = source.actions.validatePipelineRecipe()
  const saveRequest = source.actions.savePipelineRecipe()
  save.resolve({
    saved: true, catalogRefreshed: true, path: 'pipeline.yaml',
    findings: [{ severity: 'warning', message: 'save finding' }], error: '',
  })
  await saveRequest

  assert.equal(source.state.pipelineBuilder.findings[0].message, 'save finding')
  assert.equal(source.state.pipelineBuilder.loading, true)

  validation.resolve([{ severity: 'warning', message: 'stale validation' }])
  await validationRequest

  assert.equal(source.state.pipelineBuilder.findings[0].message, 'save finding')
  assert.equal(source.state.pipelineBuilder.saveResult.saved, true)
  assert.equal(source.state.pipelineBuilder.loading, false)
})

test('step navigation does not invalidate in-flight validation', async () => {
  const source = new TauriSource({})
  const validation = deferred()
  source._invoke = () => validation.promise

  const request = source.actions.validatePipelineRecipe()
  source.actions.selectPipelineBuilderStep('review')
  validation.resolve([{ severity: 'warning', message: 'validation complete' }])
  await request

  assert.equal(source.state.pipelineBuilder.step, 'review')
  assert.equal(source.state.pipelineBuilder.findings[0].message, 'validation complete')
  assert.equal(source.state.pipelineBuilder.loading, false)
})

test('stage navigation does not invalidate in-flight save', async () => {
  const source = new TauriSource({})
  const save = deferred()
  source._invoke = () => save.promise

  const request = source.actions.savePipelineRecipe()
  source.actions.selectPipelineStage(1)
  save.resolve({ saved: true, catalogRefreshed: true, path: 'pipeline.yaml', findings: [], error: '' })
  await request

  assert.equal(source.state.pipelineBuilder.selectedStageIndex, 1)
  assert.equal(source.state.pipelineBuilder.saveResult.saved, true)
  assert.equal(source.state.pipelineBuilder.loading, false)
})

test('recipe edit still invalidates an older validation completion', async () => {
  const source = new TauriSource({})
  const validation = deferred()
  source._invoke = () => validation.promise
  source._setLocal({
    pipelineBuilderCatalog: {
      presets: [],
      agents: [{ name: 'planner', step: 'plan', runnable: true }],
    },
  })

  const request = source.actions.validatePipelineRecipe()
  source.actions.addPipelineStage({ agent: 'planner' })
  validation.resolve([{ severity: 'warning', message: 'stale validation' }])
  await request

  assert.deepEqual(source.state.pipelineBuilder.findings, [])
  assert.equal(source.state.pipelineBuilder.draft.stages[0].agent, 'planner')
  assert.equal(source.state.pipelineBuilder.loading, false)
})

test('confirmed dirty recipe load retains resolved contracts in save payload', async () => {
  const source = new TauriSource({})
  source.actions.addPipelineStage({ agent: 'dirty' })
  source._invoke = async () => ({
    name: 'feature-dev',
    stages: [{ agent: 'planner' }, { agent: 'coder' }],
    resolvedContracts: [{ step: 'planner', allowedTools: ['Read'] }],
    findings: [{ severity: 'warning', message: 'backend finding' }],
  })

  await source.actions.loadPipelineRecipe('feature-dev')
  source.actions.confirmPipelineTransition()

  assert.deepEqual(source._recipePayload().resolvedContracts, [
    { step: 'planner', allowedTools: ['Read'] },
  ])
})

test('builder edits stages and spawn roles through immutable actions', () => {
  const { source } = sourceWithActionSpy()
  source._setLocal({
    pipelineBuilderCatalog: {
      presets: [],
      agents: [
        { name: 'planner', step: 'planner', runnable: true },
        { name: 'coder', step: 'coder', runnable: true },
      ],
    },
  })
  source.actions.addPipelineStage({ agent: 'planner' })
  source.actions.addPipelineStage({ agent: 'coder' })
  source.actions.addPipelineSpawn(1, 'Reviewer-Aux')
  source.actions.movePipelineStage(1, 0)

  assert.deepEqual(source.state.pipelineBuilder.draft.stages.map((stage) => stage.agent), ['coder', 'planner'])
  assert.deepEqual(source.state.pipelineBuilder.draft.stages[0].spawns, ['reviewer-aux'])
})

test('builder add-stage accepts only runnable backend catalog entries', () => {
  const { source } = sourceWithActionSpy()
  source._setLocal({
    pipelineBuilderCatalog: {
      presets: [
        { id: 'plan', agent: 'planner', stage: 'plan', runnable: true },
        { id: 'broken', agent: 'missing', stage: 'build', runnable: false, blockedReason: 'unknown agent' },
      ],
      agents: [{ name: 'coder', displayLabel: 'Coder', runnable: true }],
    },
  })

  source.actions.addPipelineStage({ preset: 'missing' })
  source.actions.addPipelineStage({ preset: 'broken' })
  source.actions.addPipelineStage({ preset: 'plan' })

  assert.deepEqual(source.state.pipelineBuilder.draft.stages, [
    { preset: 'plan', agent: '', stage: '', spawns: [] },
  ])
})

test('legacy Pipeline Studio runtime actions are absent', () => {
  const { actions } = new TauriSource({})
  for (const name of ['selectPipeSession', 'clearPipeDriverChat', 'newPipeSession', 'onPipeDraft', 'onPipeDraftKey', 'sendPipelineTask']) {
    assert.equal(actions[name], undefined, name)
  }
})
