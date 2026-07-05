// Native DataSource for the Tauri desktop shell. Domain data comes from the
// Rust core (which reads Musubi's audit.db / llm.toml); navigation state stays
// client-side.
//
//   get_state            invoke → initial domain snapshot
//   state://update       event  → domain snapshot on every change (Rust poller)
//   action               invoke({ kind, args }) → backend mutating actions (chat, profile)
import { pipePresets } from '../model/data.js'
import { classifyChatCommand } from './chatCommands.js'

// Domain keys owned by the backend; everything else (view, selected, draft,
// auditFilter, pipeChatOpen, and the whole pipe* composer) is local UI state.
// The Pipeline studio is a client-side preset composer/inspector, so pipeSteps
// and pipeName are NOT backend-owned — otherwise the snapshot poll would clobber
// every add/move/preset change the user makes.
const DOMAIN_KEYS = [
  'subagents', 'events', 'policy', 'audit', 'chat',
  'agentTurns',
  'totalSpawned', 'totalDone', 'allowCount', 'denyCount', 'activeProfile', 'profiles',
  'paused', 't',
  'runtimeSource', 'setupStatus', 'driverStatus',
]

export default class TauriSource {
  constructor(props) {
    this.props = props || {}
    this.subs = new Set()
    this._unlisten = null
    this._pipeUid = 0
    this.state = {
      view: this.props.startView || 'orchestrator',
      selected: null, selectedSession: null, paused: false, t: 0, auditFilter: 'all', draft: '', pipeChatOpen: false,
      processOpen: false, logWindowOpen: false,
      subagents: [], agentTurns: [], events: [], policy: [], audit: [], chat: [],
      totalSpawned: 0, totalDone: 0, allowCount: 0, denyCount: 0,
      activeProfile: 'anthropic.default', profiles: [],
      pipeName: 'feature-dev', pipeSteps: this._stepsFromPreset('feature-dev'),
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
  _stepsFromPreset(name) {
    const preset = pipePresets.find((p) => p.name === name)
    const roles = preset ? preset.roles : []
    return roles.map((role) => ({ uid: this._nextPipeUid(), role, status: 'idle', handle: null }))
  }
  _mergeDomain(dom) {
    if (!dom || typeof dom !== 'object') return
    const patch = {}
    for (const k of DOMAIN_KEYS) if (k in dom) patch[k] = dom[k]
    // The Orchestrator's "Parent runs" mirror the append-only audit (HI #8);
    // clearing the driver chat clears the conversation only, never the run
    // history, so subagents/agentTurns are always shown straight from the DB.
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
      // Choose a whole session from the Parent runs list (works for driver-only
      // runs too). Clears any per-worker selection so the session wins.
      selectSession: (id) => this._setLocal({ view: 'orchestrator', selectedSession: id, selected: null }),
      clearSelect: local({ selected: null, selectedSession: null }),
      setAuditFilter: (f) => this._setLocal({ auditFilter: f }),
      openPipeChat: local({ pipeChatOpen: true }),
      closePipeChat: local({ pipeChatOpen: false }),
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
        this._action('clear_driver_chat')
      },
      onDraft: (e) => this._setLocal({ draft: e.target.value }),
      onDraftKey: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          if (!this.state.driverStatus?.running) this.actions.sendChat()
        }
      },
      // backend-mutating actions
      cancelAgent: () => this._action('cancel_agent'),
      selectProfile: (n) => this._action('select_profile', [n]),
      sendChat: () => {
        const d = (this.state.draft || '').trim()
        if (!d) return
        this._setLocal({ draft: '' })
        const command = classifyChatCommand(d)
        if (command.kind === 'openPipelinePicker') {
          this._setLocal({ view: 'pipeline', pipeChatOpen: false })
          this._action('pipeline_hint', [d])
          return
        }
        this._action('send_chat', [d])
      },
      openArtifact: (path) => this._action('open_artifact', [path]),
      // Pipeline studio composer — pure client-side UI state (see DOMAIN_KEYS).
      addPipe: (role) => this._setLocal({
        pipeSteps: [...this.state.pipeSteps, { uid: this._nextPipeUid(), role, status: 'idle', handle: null }],
      }),
      removePipe: (uid) => this._setLocal({
        pipeSteps: this.state.pipeSteps.filter((st) => st.uid !== uid),
      }),
      movePipe: (uid, dir) => {
        const steps = [...this.state.pipeSteps]
        const i = steps.findIndex((st) => st.uid === uid)
        const j = i + dir
        if (i < 0 || j < 0 || j >= steps.length) return
        const moved = steps.splice(i, 1)[0]
        steps.splice(j, 0, moved)
        this._setLocal({ pipeSteps: steps })
      },
      clearPipe: () => this._setLocal({ pipeSteps: [] }),
      loadPreset: (name) => this._setLocal({ pipeName: name, pipeSteps: this._stepsFromPreset(name) }),
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
  return { running: false, task: '', startedAt: null, stdoutTail: '', stderrTail: '' }
}
