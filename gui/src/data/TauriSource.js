// Native DataSource for the Tauri desktop shell. Domain data comes from the
// Rust core (which reads Musubi's audit.db / llm.toml); navigation state stays
// client-side.
//
//   get_state            invoke → initial domain snapshot
//   state://update       event  → domain snapshot on every change (Rust poller)
//   action               invoke({ kind, args }) → backend mutating actions (chat, profile)
import { classifyChatCommand } from './chatCommands.js'

// Domain keys owned by the backend; everything else (view, selected, draft,
// auditFilter and the whole pipe* composer) is local UI state. The Pipeline
// studio keeps draft pipeSteps/pipeName client-side so snapshot polling cannot clobber
// every add/move/preset change the user makes.
const DOMAIN_KEYS = [
  'subagents', 'events', 'policy', 'audit', 'chat', 'pipeChat',
  'agentTurns', 'agentCycles',
  'totalSpawned', 'totalDone', 'allowCount', 'denyCount', 'activeProfile', 'profiles',
  'paused', 't',
  'runtimeSource', 'setupStatus', 'driverStatus',
  'orchestratorChatId', 'viewedOrchestratorChatId', 'pipelineChatId', 'orchestratorSessions', 'pipelineCatalog', 'pipelineRuns',
]

export default class TauriSource {
  constructor(props) {
    this.props = props || {}
    this.subs = new Set()
    this._unlisten = null
    this._pipeUid = 0
    this.state = {
      view: this.props.startView || 'orchestrator',
      selected: null, selectedSession: null, selectedPipeSession: null, paused: false, t: 0, auditFilter: 'all', draft: '', pipeDraft: '',
      processOpen: false, logWindowOpen: false,
      subagents: [], agentTurns: [], agentCycles: [], orchestratorSessions: [], pipelineRuns: [], events: [], policy: [], audit: [], chat: [], pipeChat: [],
      orchestratorChatId: '', viewedOrchestratorChatId: '', pipelineChatId: '', pipelineCatalog: [],
      totalSpawned: 0, totalDone: 0, allowCount: 0, denyCount: 0,
      activeProfile: 'anthropic.default', profiles: [],
      pipeName: '', pipeSteps: [], pipeModified: false,
      pipeRunning: false, pipeCur: -1, pipeProg: 0, pipeDoneFlag: false,
      runtimeSource: 'none',
      driverStatus: emptyDriverStatus(),
      setupStatus: emptySetupStatus(),
    }
  }

  subscribe(cb) { this.subs.add(cb); return () => this.subs.delete(cb) }
  _notify() { for (const cb of this.subs) cb() }
  _setLocal(patch) { this.state = { ...this.state, ...patch }; this._notify() }
  _nextPipeUid() { this._pipeUid += 1; return this._pipeUid }
  // Build studio stage rows from a preset's role list. The composer is purely
  // client-side; roles carry their display metadata from `pipeCatalog`.
  _stepsFromRoles(roles) {
    return (roles || []).map((role) => ({ uid: this._nextPipeUid(), role, status: 'idle', handle: null }))
  }
  _mergeDomain(dom) {
    if (!dom || typeof dom !== 'object') return
    const patch = {}
    for (const k of DOMAIN_KEYS) if (k in dom) patch[k] = dom[k]
    const catalogChanged = Array.isArray(dom.pipelineCatalog)
      && JSON.stringify(dom.pipelineCatalog) !== JSON.stringify(this.state.pipelineCatalog)
    if (Array.isArray(dom.pipelineCatalog) && !this.state.pipeModified && (catalogChanged || !this.state.pipeName)) {
      const selected = dom.pipelineCatalog.find((entry) => entry.name === this.state.pipeName)
        || dom.pipelineCatalog[0]
      if (selected) {
        patch.pipeName = selected.name
        patch.pipeSteps = this._stepsFromRoles(selected.stages)
        patch.pipeModified = false
      }
    }
    // The Orchestrator session index mirrors durable chat plus append-only
    // worker audit (HI #8). Clearing one visible chat never clears another
    // session's summaries, agent turns, or subagent ancestry.
    this.state = { ...this.state, ...patch }
    this._notify()
  }

  async start() {
    const { invoke } = await import('@tauri-apps/api/core')
    const { listen } = await import('@tauri-apps/api/event')
    this._invoke = invoke
    try {
      this._mergeDomain(await invoke('get_state'))
    } catch (e) {
      console.error('[musubi] get_state failed:', e)
    }
    this._unlisten = await listen('state://update', (ev) => this._mergeDomain(ev.payload))
  }
  stop() { if (this._unlisten) { this._unlisten(); this._unlisten = null } }

  _action(kind, args) {
    if (!this._invoke) return
    this._invoke('action', { kind, args: args || [] }).catch((e) => console.error('[musubi] action ' + kind + ' failed:', e))
  }

  get actions() {
    if (this._actions) return this._actions
    const local = (patch) => () => this._setLocal(patch)
    this._actions = {
      // client-side navigation / UI
      setView: (v) => this._setLocal({ view: v }),
      selectAgent: (h) => this._setLocal({ view: 'orchestrator', selected: h, selectedSession: null }),
      // Choose a durable chat session (including driver-only sessions). Clear
      // any per-worker selection and let the backend swap the exact chat ID.
      selectSession: (id) => {
        this._setLocal({
          view: 'orchestrator',
          selectedSession: id,
          selected: null,
          chat: [],
          draft: '',
          processOpen: false,
          logWindowOpen: false,
        })
        this._action('select_session', [id])
      },
      selectPipeSession: (id) => this._setLocal({ view: 'pipeline', selectedPipeSession: id }),
      clearSelect: local({ selected: null, selectedSession: null }),
      setAuditFilter: (f) => this._setLocal({ auditFilter: f }),
      toggleProcess: () => this._setLocal({ processOpen: !this.state.processOpen }),
      openProcessLog: () => this._setLocal({ logWindowOpen: true }),
      closeProcessLog: () => this._setLocal({ logWindowOpen: false }),
      clearDriverChat: () => {
        if (this.state.driverStatus?.running) return
        // Clear the conversation only. The Orchestrator run history
        // (subagents / agentTurns) is the append-only audit and stays put.
        this._setLocal({
          chat: [],
          selected: null,
          draft: '',
          processOpen: false,
          logWindowOpen: false,
          driverStatus: emptyDriverStatus(),
        })
        this._action('clear_driver_chat', ['orchestrator'])
      },
      clearPipeDriverChat: () => {
        if (this.state.driverStatus?.running) return
        this._setLocal({
          pipeChat: [],
          pipeDraft: '',
          selectedPipeSession: null,
          processOpen: false,
          logWindowOpen: false,
          driverStatus: emptyDriverStatus(),
        })
        this._action('clear_driver_chat', ['pipeline'])
      },
      // New session: re-mint the surface's chat_id so the agent replays no
      // prior history (unlike clear, which only wipes the visible chat). Old
      // turns stay under the previous id.
      newSession: () => {
        if (this.state.driverStatus?.running) return
        this._setLocal({
          chat: [],
          orchestratorChatId: '__pending_orchestrator_session__',
          selected: null,
          draft: '',
          processOpen: false,
          logWindowOpen: false,
          driverStatus: emptyDriverStatus(),
        })
        this._action('new_session', ['orchestrator'])
      },
      newPipeSession: () => {
        if (this.state.driverStatus?.running) return
        this._setLocal({
          pipeChat: [],
          pipelineChatId: '__pending_pipeline_session__',
          pipeDraft: '',
          selectedPipeSession: null,
          processOpen: false,
          logWindowOpen: false,
          driverStatus: emptyDriverStatus(),
        })
        this._action('new_session', ['pipeline'])
      },
      onDraft: (e) => this._setLocal({ draft: e.target.value }),
      onDraftKey: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          if (!this.state.driverStatus?.running) this.actions.sendChat()
        }
      },
      onPipeDraft: (e) => this._setLocal({ pipeDraft: e.target.value }),
      onPipeDraftKey: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          if (!this.state.driverStatus?.running) this.actions.sendPipelineTask()
        }
      },
      // backend-mutating actions
      cancelAgent: () => this._action('cancel_agent'),
      selectProfile: (n) => this._action('select_profile', [n]),
      sendChat: () => {
        const d = (this.state.draft || '').trim()
        if (!d) return
        const requestedChatId = this.state.selectedSession || ''
        const command = classifyChatCommand(d)
        if (command.kind === 'openPipelinePicker') {
          this._setLocal({ draft: '', view: 'pipeline' })
          this._action('pipeline_hint', [d, requestedChatId])
          return
        }
        // Sending a new request focuses the run it starts: drop any manually
        // chosen session so the new running run reclaims the main panel.
        this._setLocal({ draft: '', selectedSession: null, selected: null })
        this._action('send_chat', [d, requestedChatId])
      },
      sendPipelineTask: () => {
        const d = (this.state.pipeDraft || '').trim()
        if (!d) return
        const entry = (this.state.pipelineCatalog || []).find((item) => item.name === this.state.pipeName)
        if (this.state.pipeModified || !entry?.runnable) return
        this._setLocal({ pipeDraft: '', selectedPipeSession: null })
        this._action('send_pipeline_task', [d, this.state.pipeName])
      },
      openArtifact: (path, surface = 'orchestrator') => this._action('open_artifact', [path, surface]),
      // Pipeline studio composer — pure client-side UI state (see DOMAIN_KEYS).
      addPipe: (role) => this._setLocal({
        pipeSteps: [...this.state.pipeSteps, { uid: this._nextPipeUid(), role, status: 'idle', handle: null }],
        pipeModified: true,
      }),
      removePipe: (uid) => this._setLocal({
        pipeSteps: this.state.pipeSteps.filter((st) => st.uid !== uid),
        pipeModified: true,
      }),
      movePipe: (uid, dir) => {
        const steps = [...this.state.pipeSteps]
        const i = steps.findIndex((st) => st.uid === uid)
        const j = i + dir
        if (i < 0 || j < 0 || j >= steps.length) return
        const moved = steps.splice(i, 1)[0]
        steps.splice(j, 0, moved)
        this._setLocal({ pipeSteps: steps, pipeModified: true })
      },
      loadPreset: (name) => {
        const entry = (this.state.pipelineCatalog || []).find((item) => item.name === name)
        if (!entry) return
        this._setLocal({ pipeName: name, pipeSteps: this._stepsFromRoles(entry.stages), pipeModified: false })
      },
    }
    return this._actions
  }
}

function emptySetupStatus() {
  const cli = { found: false, path: '', hint: '' }
  return {
    projectRoot: '',
    auditDbPath: '',
    auditDbSource: 'none',
    pythonCli: { found: false, path: '', hint: '' },
    musubiCli: cli,
    agentCli: cli,
    llmConfigPath: '',
    llmConfigured: false,
    pathHint: '',
  }
}

function emptyDriverStatus() {
  return { running: false, chatId: '', surface: 'orchestrator', pipelineName: '', terminalStatus: '', task: '', startedAt: null, stdoutTail: '', stderrTail: '' }
}
