// Native DataSource for the Tauri desktop shell. Backend domain snapshots are
// merged with local Orchestrator composer and Pipeline Studio builder state.
import { classifyChatCommand, pipelineNameFromCommand } from './chatCommands.js'
import { approvalScope } from '../model/approvalRequest.js'
import {
  createPipelineDraft, addStage, moveStage, removeStage, updateStage, updateRecipe,
  setStageSpawns, isDirty, requestTransition, confirmTransition, cancelTransition,
} from '../model/pipelineBuilder.js'

const DOMAIN_KEYS = [
  'subagents', 'events', 'policy', 'audit', 'chat', 'pipeChat',
  'agentTurns', 'agentCycles', 'runtimeLogEvents', 'totalSpawned', 'totalDone', 'allowCount',
  'denyCount', 'activeProfile', 'profiles', 'paused', 't', 'runtimeSource',
  'setupStatus', 'driverStatus', 'orchestratorChatId',
  'viewedOrchestratorChatId', 'pipelineChatId', 'orchestratorSessions',
  'sessionFolderGrants',
  'pipelineCatalog', 'pipelineRuns',
  'pipelineBuilderCatalog',
  // Backend-owned: the persisted workspace could not be honoured at startup.
  // Kept separate from the client-local `workspaceError` the picker sets, so
  // a poll returning "no problem" cannot wipe a transient picker message.
  'workspaceBlockedReason',
]

export default class TauriSource {
  constructor(props) {
    this.props = props || {}
    this.subs = new Set()
    this._unlisten = null
    this._builderGeneration = 0
    this._pendingBuilderOperations = 0
    this._pendingNewSessionFrom = null
    this._pendingPipelineResumeSession = null
    const emptyDraft = createPipelineDraft()
    this.state = {
      view: this.props.startView || 'orchestrator',
      selected: null, selectedSession: null, paused: false, t: 0,
      auditFilter: 'all', draft: '', processOpen: false, logWindowOpen: false,
      sessionsHidden: false,
      subagents: [], agentTurns: [], agentCycles: [], runtimeLogEvents: [], orchestratorSessions: [],
      pipelineRuns: [], events: [], policy: [], audit: [], chat: [], pipeChat: [],
      orchestratorChatId: '', viewedOrchestratorChatId: '', pipelineChatId: '',
      pipelineCatalog: [], runMode: 'direct', selectedPipeline: '', tokenBudget: '',
      pipelineBuilderCatalog: { presets: [], agents: [] },
      totalSpawned: 0, totalDone: 0, allowCount: 0, denyCount: 0,
      activeProfile: 'anthropic.default', profiles: [], runtimeSource: 'none',
      driverStatus: emptyDriverStatus(), setupStatus: emptySetupStatus(),
      sessionFolderGrants: [], folderGrantError: '', folderGrantBusy: false,
      pipelineResumeBusy: false, pipelineResumeError: '',
      workspaceBlockedReason: '',
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
    if (
      this._pendingPipelineResumeSession
      && Array.isArray(dom.pipelineRuns)
    ) {
      const pendingRun = dom.pipelineRuns.find(
        (run) => run.sessionId === this._pendingPipelineResumeSession,
      )
      if (!pendingRun?.pauseReason) {
        this._pendingPipelineResumeSession = null
        patch.pipelineResumeBusy = false
        patch.pipelineResumeError = ''
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

  // Every user message leaves by this door — the composer, the Enter key, and
  // the destructive-approval button alike. Sharing the route is the point: an
  // approval that took a shortcut would be a consent path the CLI does not
  // have, and the whole gate rests on approval being an ordinary user turn.
  _submitChat(raw) {
    const text = String(raw || '').trim()
    if (!text) return
    const requestedChatId = this.state.selectedSession
      || (this.state.orchestratorChatId === '__pending_orchestrator_session__'
        ? ''
        : this.state.viewedOrchestratorChatId || '')
    const command = classifyChatCommand(text)
    // The classifier returns a *candidate* name — any single token in the
    // name position — so the catalog is what decides whether the user
    // named a recipe. Resolving before the branch is the whole guard:
    // "use the pipeline runner" parses as 'runner', and taking the
    // pipeline branch on an unknown name cleared the composer and dropped
    // the message with nothing sent and no error shown.
    const namedPipeline = (this.state.pipelineCatalog || [])
      .find((entry) => entry.name === pipelineNameFromCommand(text))?.name || ''
    if (command.kind === 'openPipelinePicker' || namedPipeline) {
      const selected = namedPipeline || this.state.selectedPipeline
      this._setLocal({ draft: '', runMode: 'pipeline', selectedPipeline: selected || '' })
      return
    }
    const mode = this.state.runMode === 'pipeline' ? 'pipeline' : 'direct'
    const pipelineName = mode === 'pipeline' ? this.state.selectedPipeline : ''
    if (mode === 'pipeline') {
      const entry = (this.state.pipelineCatalog || []).find((item) => item.name === pipelineName)
      if (!entry?.runnable) return
    }
    // The offer is spent once anything is sent: the next turn rewrites the
    // chat's pending grants, so a stale button must not survive the send.
    this._setLocal({ draft: '', selectedSession: null, selected: null, dismissedApproval: '' })
    this._action('send_chat', [
      text,
      requestedChatId,
      mode,
      pipelineName,
      this.state.tokenBudget,
    ])
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
      deleteSession: (id) => {
        const chatId = String(id || '')
        if (!chatId) return
        const driver = this.state.driverStatus || {}
        if (driver.running && driver.surface === 'orchestrator' && driver.chatId === chatId) return
        if (this.state.selectedSession === chatId) this._setLocal({ selectedSession: null, selected: null })
        this._action('delete_session', [chatId])
      },
      cleanSessions: () => {
        if (this.state.driverStatus?.running) return
        this._setLocal({ selectedSession: null, selected: null })
        this._action('clean_sessions', [])
      },
      resumePipeline: async (sessionId, action, userHint = '', extraBudget = 0) => {
        if (
          !this._invoke
          || this.state.driverStatus?.running
          || this.state.pipelineResumeBusy
        ) return
        this._pendingPipelineResumeSession = sessionId
        this._setLocal({ pipelineResumeBusy: true, pipelineResumeError: '' })
        try {
          await this._invoke('action', {
            kind: 'resume_pipeline',
            args: [sessionId, action, userHint, extraBudget],
          })
        } catch (error) {
          this._pendingPipelineResumeSession = null
          this._setLocal({
            pipelineResumeBusy: false,
            pipelineResumeError: String(error),
          })
        }
      },
      clearSelect: local({ selected: null, selectedSession: null }),
      // Drops the node selection only. `clearSelect` also drops
      // selectedSession, which snaps the operator out of whatever historical
      // session they were reading — wrong when the intent is just "no node".
      clearNodeSelect: local({ selected: null }),
      setAuditFilter: (auditFilter) => this._setLocal({ auditFilter }),
      toggleProcess: () => this._setLocal({ processOpen: !this.state.processOpen }),
      // Owned here rather than inside Orchestrator so the activity bar — which
      // sits beside the rail it shows and hides — can drive it.
      toggleSessions: () => this._setLocal({ sessionsHidden: !this.state.sessionsHidden }),
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
    setTokenBudget: (value) => {
      const tokenBudget = String(value ?? '').trim()
      this._setLocal({ tokenBudget })
    },
      selectPipeline: (name) => {
        if (!(this.state.pipelineCatalog || []).some((entry) => entry.name === name)) return
        this._setLocal({ runMode: 'pipeline', selectedPipeline: name })
      },
      cancelAgent: () => this._action('cancel_agent'),
      selectProfile: (name) => this._action('select_profile', [name]),
      addSessionFolder: async () => {
        if (this.state.driverStatus?.running || this.state.folderGrantBusy) return
        try {
          const selected = await this._invoke('choose_workspace')
          if (!selected) return
          const chatId = this.state.selectedSession
            || this.state.viewedOrchestratorChatId
            || this.state.orchestratorChatId
          this._setLocal({ folderGrantError: '', folderGrantBusy: true })
          await this._invoke('action', {
            kind: 'add_session_folder',
            args: [selected, chatId],
          })
          this._setLocal({ folderGrantBusy: false })
        } catch (error) {
          this._setLocal({ folderGrantError: String(error), folderGrantBusy: false })
        }
      },
      renameSessionFolder: async (grantId, alias) => {
        if (this.state.driverStatus?.running || this.state.folderGrantBusy) return
        const chatId = this.state.selectedSession
          || this.state.viewedOrchestratorChatId
          || this.state.orchestratorChatId
        try {
          this._setLocal({ folderGrantError: '', folderGrantBusy: true })
          await this._invoke('action', {
            kind: 'rename_session_folder',
            args: [chatId, grantId, alias],
          })
          this._setLocal({ folderGrantBusy: false })
        } catch (error) {
          this._setLocal({ folderGrantError: String(error), folderGrantBusy: false })
        }
      },
      removeSessionFolder: async (grantId) => {
        if (this.state.driverStatus?.running || this.state.folderGrantBusy) return
        const chatId = this.state.selectedSession
          || this.state.viewedOrchestratorChatId
          || this.state.orchestratorChatId
        try {
          this._setLocal({ folderGrantError: '', folderGrantBusy: true })
          await this._invoke('action', {
            kind: 'remove_session_folder',
            args: [chatId, grantId],
          })
          this._setLocal({ folderGrantBusy: false })
        } catch (error) {
          this._setLocal({ folderGrantError: String(error), folderGrantBusy: false })
        }
      },
      sendChat: () => this._submitChat(this.state.draft),
      // Approval is a user message, not a control channel. It goes through the
      // same submit as typing, so the backend sees one kind of consent and the
      // GUI gains no authority the CLI lacks — the token still has to match
      // one the harness minted, and a stale one simply grants nothing.
      approveDestructive: (token) => this._submitChat(token),
      // Refusing needs no message: the gate already stopped the call and the
      // turn already ended. Rejecting is declining to grant, so it only clears
      // the offer from the screen.
      // Scoped to the conversation it was rejected in. A token is a hash of the
      // destruction key set, so deleting the same path in a different chat
      // mints the SAME token — comparing tokens alone would silently hide a
      // brand-new offer because an unrelated chat had declined one.
      dismissApproval: (token) => this._setLocal({
        dismissedApproval: approvalScope(this.state, String(token || '')),
      }),
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
        const stageObject = stage && typeof stage === 'object' ? stage : null
        const identifiesPreset = typeof stage === 'string'
          || !!stageObject && (
            Object.hasOwn(stageObject, 'id')
            || Object.hasOwn(stageObject, 'preset')
            || stageObject.kind === 'preset'
          )
        if (identifiesPreset) {
          const presetId = String(typeof stage === 'string' ? stage : stageObject.id ?? stageObject.preset ?? '')
          const preset = (catalog.presets || []).find((item) => item.id === presetId && item.runnable)
          if (!preset) return
          this._setBuilder({ draft: addStage(this.state.pipelineBuilder.draft, { preset: preset.id }, index) })
          return
        }
        if (!stageObject || (stageObject.kind && stageObject.kind !== 'agent')) return
        const agentName = String(stage?.name || stage?.agent || '')
        const agent = (catalog.agents || []).find((item) => item.name === agentName && item.runnable)
        if (!agent) return
        this._setBuilder({
          draft: addStage(this.state.pipelineBuilder.draft, { agent: agent.name, stage: agent.step || agent.name }, index),
        })
      },
      movePipelineStage: (fromIndex, toIndex) => this._setBuilder({ draft: moveStage(this.state.pipelineBuilder.draft, fromIndex, toIndex) }),
      removePipelineStage: (index) => this._setBuilder({
        draft: removeStage(this.state.pipelineBuilder.draft, index),
        selectedStageIndex: this.state.pipelineBuilder.selectedStageIndex === index ? null : this.state.pipelineBuilder.selectedStageIndex,
      }),
      updatePipelineStage: (index, patch) => this._setBuilder({ draft: updateStage(this.state.pipelineBuilder.draft, index, patch) }),
      updatePipelineRecipe: (patch) => this._setBuilder({
        draft: updateRecipe(this.state.pipelineBuilder.draft, patch),
      }),
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
      // A clone is a local rename, not a write. It mints an unused name and
      // clears savedRecipe, so the draft reads Unsaved and the existing Save
      // path — the only validated way onto disk — creates the new directory.
      // Nothing is written until you press Save, and the recipe cloned from is
      // never the save target.
      clonePipelineRecipe: () => {
        const draft = this.state.pipelineBuilder?.draft
        if (!draft?.name) return
        const taken = new Set((this.state.pipelineCatalog || []).map((entry) => entry.name))
        const stem = String(draft.name).replace(/-copy(-\d+)?$/, '')
        let name = `${stem}-copy`
        for (let suffix = 2; taken.has(name); suffix += 1) name = `${stem}-copy-${suffix}`
        this._setBuilder({
          draft: { ...structuredClone(draft), name },
          savedRecipe: createPipelineDraft(),
          findings: [], saveResult: null, selectedStageIndex: null,
        })
      },
      deletePipelineRecipe: async (name) => {
        if (!this._invoke || !name) return
        const generation = this._beginBuilderOperation()
        try {
          const result = await this._invoke('delete_pipeline_recipe', { name })
          this._finishBuilderOperation(generation, (builder) => {
            if (!result?.deleted) {
              return { ...builder, findings: [{ severity: 'error', step: 'delete', field: '', message: result?.error || 'delete failed' }] }
            }
            // Deleting the recipe currently open leaves the editor holding a
            // draft with nowhere to save to, so reset it to a blank one.
            const open = builder.savedRecipe?.name === name
            return {
              ...builder, findings: [], saveResult: null,
              ...(open ? { draft: createPipelineDraft(), savedRecipe: createPipelineDraft(), selectedStageIndex: null } : {}),
            }
          })
        } catch (error) {
          this._finishBuilderOperation(generation, (builder) => ({
            ...builder, findings: [errorFinding(error, 'delete')],
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
