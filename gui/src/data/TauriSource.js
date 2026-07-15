// Native DataSource for the Tauri desktop shell. Backend domain snapshots are
// merged with local Orchestrator composer and Pipeline Studio builder state.
import { classifyChatCommand } from './chatCommands.js'
import {
  createPipelineDraft, addStage, moveStage, removeStage, updateStage,
  setStageSpawns, isDirty, requestTransition, confirmTransition, cancelTransition,
} from '../model/pipelineBuilder.js'

const DOMAIN_KEYS = [
  'subagents', 'events', 'policy', 'audit', 'chat', 'pipeChat',
  'agentTurns', 'agentCycles', 'totalSpawned', 'totalDone', 'allowCount',
  'denyCount', 'activeProfile', 'profiles', 'paused', 't', 'runtimeSource',
  'setupStatus', 'driverStatus', 'orchestratorChatId',
  'viewedOrchestratorChatId', 'pipelineChatId', 'orchestratorSessions',
  'pipelineCatalog', 'pipelineRuns',
  'pipelineBuilderCatalog',
]

export default class TauriSource {
  constructor(props) {
    this.props = props || {}
    this.subs = new Set()
    this._unlisten = null
    this._builderGeneration = 0
    this._pendingBuilderOperations = 0
    this._pendingNewSessionFrom = null
    const emptyDraft = createPipelineDraft()
    this.state = {
      view: this.props.startView || 'orchestrator',
      selected: null, selectedSession: null, paused: false, t: 0,
      auditFilter: 'all', draft: '', processOpen: false, logWindowOpen: false,
      subagents: [], agentTurns: [], agentCycles: [], orchestratorSessions: [],
      pipelineRuns: [], events: [], policy: [], audit: [], chat: [], pipeChat: [],
      orchestratorChatId: '', viewedOrchestratorChatId: '', pipelineChatId: '',
      pipelineCatalog: [], runMode: 'direct', selectedPipeline: '',
      pipelineBuilderCatalog: { presets: [], agents: [] },
      totalSpawned: 0, totalDone: 0, allowCount: 0, denyCount: 0,
      activeProfile: 'anthropic.default', profiles: [], runtimeSource: 'none',
      driverStatus: emptyDriverStatus(), setupStatus: emptySetupStatus(),
      pipelineBuilder: {
        step: 'catalog', draft: emptyDraft, savedRecipe: emptyDraft,
        selectedStageIndex: null, findings: [], saveResult: null,
        loading: false, pendingTransition: null,
      },
    }
  }

  subscribe(cb) { this.subs.add(cb); return () => this.subs.delete(cb) }
  _notify() { for (const cb of this.subs) cb() }
  _setLocal(patch) { this.state = { ...this.state, ...patch }; this._notify() }
  _setBuilder(patch, invalidate = true) {
    if (invalidate) this._builderGeneration += 1
    this._setLocal({ pipelineBuilder: { ...this.state.pipelineBuilder, ...patch } })
  }
  _replaceBuilder(pipelineBuilder, invalidate = true) {
    if (invalidate) this._builderGeneration += 1
    this._setLocal({ pipelineBuilder })
  }
  _beginBuilderOperation() {
    const generation = ++this._builderGeneration
    this._pendingBuilderOperations += 1
    this._setBuilder({ loading: true }, false)
    return generation
  }
  _finishBuilderOperation(generation, update) {
    this._pendingBuilderOperations = Math.max(0, this._pendingBuilderOperations - 1)
    const loading = this._pendingBuilderOperations > 0
    if (generation !== this._builderGeneration) {
      this._setBuilder({ loading }, false)
      return
    }
    this._replaceBuilder({ ...update(this.state.pipelineBuilder), loading }, false)
  }

  _mergeDomain(dom) {
    if (!dom || typeof dom !== 'object') return
    const patch = {}
    for (const key of DOMAIN_KEYS) if (key in dom) patch[key] = dom[key]
    if (this._pendingNewSessionFrom !== null) {
      const backendChatId = String(dom.orchestratorChatId || '')
      if (backendChatId && backendChatId !== this._pendingNewSessionFrom) {
        this._pendingNewSessionFrom = null
      } else {
        delete patch.orchestratorChatId
        delete patch.viewedOrchestratorChatId
      }
    }
    const catalog = Array.isArray(dom.pipelineCatalog) ? dom.pipelineCatalog : []
    const builder = this.state.pipelineBuilder
    if (catalog.length && !builder.draft.name && !isDirty(builder.draft, builder.savedRecipe)) {
      const entry = catalog[0]
      const recipe = {
        name: entry.name,
        description: entry.description || '',
        stages: (entry.stages || []).map((agent) => ({ agent })),
      }
      patch.pipelineBuilder = requestTransition(builder, { type: 'switch', recipe, savedRecipe: recipe })
      this._builderGeneration += 1
    }
    this.state = { ...this.state, ...patch }
    this._notify()
  }

  async start() {
    const { invoke } = await import('@tauri-apps/api/core')
    const { listen } = await import('@tauri-apps/api/event')
    this._invoke = invoke
    try {
      this._mergeDomain(await invoke('get_state'))
    } catch (error) {
      console.error('[musubi] get_state failed:', error)
    }
    this._unlisten = await listen('state://update', (event) => this._mergeDomain(event.payload))
  }

  stop() { if (this._unlisten) { this._unlisten(); this._unlisten = null } }

  _action(kind, args) {
    if (!this._invoke) return
    this._invoke('action', { kind, args: args || [] })
      .catch((error) => console.error('[musubi] action ' + kind + ' failed:', error))
  }

  _recipePayload() {
    const builder = this.state.pipelineBuilder
    return {
      ...createPipelineDraft(builder.draft),
      resolvedContracts: structuredClone(builder.savedRecipe?.resolvedContracts || []),
      findings: structuredClone(builder.findings || []),
    }
  }

  get actions() {
    if (this._actions) return this._actions
    const local = (patch) => () => this._setLocal(patch)
    this._actions = {
      setView: (view) => this._setLocal({ view }),
      selectAgent: (handle) => this._setLocal({ view: 'orchestrator', selected: handle, selectedSession: null }),
      selectSession: (id) => {
        this._setLocal({
          view: 'orchestrator', selectedSession: id, selected: null, chat: [],
          draft: '', processOpen: false, logWindowOpen: false,
        })
        this._action('select_session', [id])
      },
      clearSelect: local({ selected: null, selectedSession: null }),
      setAuditFilter: (auditFilter) => this._setLocal({ auditFilter }),
      toggleProcess: () => this._setLocal({ processOpen: !this.state.processOpen }),
      openProcessLog: () => this._setLocal({ logWindowOpen: true }),
      closeProcessLog: () => this._setLocal({ logWindowOpen: false }),
      clearDriverChat: () => {
        if (this.state.driverStatus?.running) return
        this._setLocal({
          chat: [], selected: null, draft: '', processOpen: false,
          logWindowOpen: false, driverStatus: emptyDriverStatus(),
        })
        this._action('clear_driver_chat', ['orchestrator'])
      },
      newSession: () => {
        if (this.state.driverStatus?.running) return
        this._pendingNewSessionFrom = this.state.orchestratorChatId || ''
        this._setLocal({
          chat: [], orchestratorChatId: '__pending_orchestrator_session__',
          viewedOrchestratorChatId: '', selectedSession: null, selected: null,
          draft: '', processOpen: false, logWindowOpen: false,
          driverStatus: emptyDriverStatus(), runMode: 'direct', selectedPipeline: '',
        })
        this._action('new_session', ['orchestrator'])
      },
      onDraft: (event) => this._setLocal({ draft: event.target.value }),
      onDraftKey: (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault()
          if (!this.state.driverStatus?.running) this.actions.sendChat()
        }
      },
      setRunMode: (mode) => {
        if (mode !== 'direct' && mode !== 'pipeline') return
        this._setLocal({ runMode: mode, selectedPipeline: mode === 'direct' ? '' : this.state.selectedPipeline })
      },
      selectPipeline: (name) => {
        if (!(this.state.pipelineCatalog || []).some((entry) => entry.name === name)) return
        this._setLocal({ runMode: 'pipeline', selectedPipeline: name })
      },
      cancelAgent: () => this._action('cancel_agent'),
      selectProfile: (name) => this._action('select_profile', [name]),
      sendChat: () => {
        const text = String(this.state.draft || '').trim()
        if (!text) return
        const requestedChatId = this.state.selectedSession
          || (this.state.orchestratorChatId === '__pending_orchestrator_session__'
            ? ''
            : this.state.viewedOrchestratorChatId || '')
        const command = classifyChatCommand(text)
        const namedPipeline = text.match(/^(?:\/pipeline|pipeline|run\s+pipeline)\s+([a-z0-9]+(?:-[a-z0-9]+)*)$/i)?.[1]?.toLowerCase()
        if (command.kind === 'openPipelinePicker' || namedPipeline) {
          const selected = namedPipeline
            ? (this.state.pipelineCatalog || []).find((entry) => entry.name === namedPipeline)?.name
            : this.state.selectedPipeline
          this._setLocal({ draft: '', runMode: 'pipeline', selectedPipeline: selected || '' })
          return
        }
        const mode = this.state.runMode === 'pipeline' ? 'pipeline' : 'direct'
        const pipelineName = mode === 'pipeline' ? this.state.selectedPipeline : ''
        if (mode === 'pipeline') {
          const entry = (this.state.pipelineCatalog || []).find((item) => item.name === pipelineName)
          if (!entry?.runnable) return
        }
        this._setLocal({ draft: '', selectedSession: null, selected: null })
        this._action('send_chat', [text, requestedChatId, mode, pipelineName])
      },
      openArtifact: (path, surface = 'orchestrator') => this._action('open_artifact', [path, surface]),

      newPipelineRecipe: () => this._replaceBuilder(
        requestTransition(this.state.pipelineBuilder, { type: 'new' }),
      ),
      closePipelineRecipe: () => this._replaceBuilder(
        requestTransition(this.state.pipelineBuilder, { type: 'close' }),
      ),
      selectPipelineBuilderStep: (step) => this._setBuilder({ step }, false),
      selectPipelineStage: (selectedStageIndex) => this._setBuilder({ selectedStageIndex }, false),
      addPipelineStage: (stage, index) => {
        const catalog = this.state.pipelineBuilderCatalog || { presets: [], agents: [] }
        const presetId = String(stage?.id || stage?.preset || '')
        const agentName = String(stage?.name || stage?.agent || '')
        const preset = (catalog.presets || []).find((item) => item.id === presetId && item.runnable)
        const agent = preset ? null : (catalog.agents || []).find((item) => item.name === agentName && item.runnable)
        if (!preset && !agent) return
        const recipeStage = preset
          ? { preset: preset.id }
          : { agent: agent.name, stage: agent.step || agent.name }
        this._setBuilder({ draft: addStage(this.state.pipelineBuilder.draft, recipeStage, index) })
      },
      movePipelineStage: (fromIndex, toIndex) => this._setBuilder({ draft: moveStage(this.state.pipelineBuilder.draft, fromIndex, toIndex) }),
      removePipelineStage: (index) => this._setBuilder({
        draft: removeStage(this.state.pipelineBuilder.draft, index),
        selectedStageIndex: this.state.pipelineBuilder.selectedStageIndex === index ? null : this.state.pipelineBuilder.selectedStageIndex,
      }),
      updatePipelineStage: (index, patch) => this._setBuilder({ draft: updateStage(this.state.pipelineBuilder.draft, index, patch) }),
      addPipelineSpawn: (index, role) => this._setBuilder({
        draft: setStageSpawns(this.state.pipelineBuilder.draft, index, [
          ...(this.state.pipelineBuilder.draft.stages[index]?.spawns || []), role,
        ]),
      }),
      removePipelineSpawn: (index, role) => this._setBuilder({
        draft: setStageSpawns(
          this.state.pipelineBuilder.draft,
          index,
          (this.state.pipelineBuilder.draft.stages[index]?.spawns || [])
            .filter((item) => item !== String(role || '').trim().toLowerCase()),
        ),
      }),
      confirmPipelineTransition: () => this._replaceBuilder(confirmTransition(this.state.pipelineBuilder)),
      cancelPipelineTransition: () => this._replaceBuilder(cancelTransition(this.state.pipelineBuilder)),
      loadPipelineRecipe: async (name) => {
        if (!this._invoke) return
        const generation = this._beginBuilderOperation()
        try {
          const recipe = await this._invoke('load_pipeline_recipe', { name })
          this._finishBuilderOperation(generation, (builder) => requestTransition(builder, {
            type: 'switch', recipe, savedRecipe: recipe,
          }))
        } catch (error) {
          this._finishBuilderOperation(generation, (builder) => ({
            ...builder, findings: [errorFinding(error, 'load')],
          }))
        }
      },
      validatePipelineRecipe: async () => {
        if (!this._invoke) return
        const recipe = this._recipePayload()
        const generation = this._beginBuilderOperation()
        try {
          const findings = await this._invoke('validate_pipeline_recipe', { recipe })
          this._finishBuilderOperation(generation, (builder) => ({
            ...builder, findings: structuredClone(findings || []),
          }))
        } catch (error) {
          this._finishBuilderOperation(generation, (builder) => ({
            ...builder, findings: [errorFinding(error, 'validate')],
          }))
        }
      },
      savePipelineRecipe: async () => {
        if (!this._invoke) return
        const recipe = this._recipePayload()
        const generation = this._beginBuilderOperation()
        try {
          const result = await this._invoke('save_pipeline_recipe', { recipe })
          this._finishBuilderOperation(generation, (builder) => ({
            ...builder, saveResult: structuredClone(result),
            findings: structuredClone(result.findings || []),
            savedRecipe: result.saved ? structuredClone(recipe) : builder.savedRecipe,
          }))
        } catch (error) {
          this._finishBuilderOperation(generation, (builder) => ({
            ...builder,
            saveResult: { saved: false, catalogRefreshed: false, path: '', findings: [], error: errorMessage(error) },
          }))
        }
      },
    }
    return this._actions
  }
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

function errorFinding(error, step) {
  return { severity: 'error', step, field: '', message: errorMessage(error) }
}

function emptySetupStatus() {
  const cli = { found: false, path: '', hint: '' }
  return {
    projectRoot: '', auditDbPath: '', auditDbSource: 'none',
    pythonCli: { found: false, path: '', hint: '' }, musubiCli: cli,
    agentCli: cli, llmConfigPath: '', llmConfigured: false, pathHint: '',
  }
}

function emptyDriverStatus() {
  return {
    running: false, chatId: '', surface: 'orchestrator', pipelineName: '',
    terminalStatus: '', task: '', startedAt: null, stdoutTail: '', stderrTail: '',
  }
}
