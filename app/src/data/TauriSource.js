// Native DataSource for the Tauri desktop shell. Domain data comes from the
// Rust core (which reads Musubi's audit.db / llm.toml); navigation state stays
// client-side. Implements the same contract as SimulationSource.
//
//   get_state            invoke → initial domain snapshot
//   state://update       event  → domain snapshot on every change (Rust poller)
//   action               invoke({ kind, args }) → mutating actions (chat, run…)
import { pipePresets } from '../sim/data.js'

// Domain keys owned by the backend; everything else (view, selected, draft,
// auditFilter, pipeChatOpen) is local UI state.
const DOMAIN_KEYS = [
  'subagents', 'events', 'policy', 'audit', 'chat',
  'totalSpawned', 'totalDone', 'allowCount', 'denyCount', 'activeProfile',
  'pipeSteps', 'pipeName', 'pipeRunning', 'pipeCur', 'pipeProg', 'pipeDoneFlag', 'paused', 't',
]

export default class TauriSource {
  constructor(props) {
    this.props = props || {}
    this.subs = new Set()
    this._unlisten = null
    this.state = {
      view: this.props.startView || 'orchestrator',
      selected: null, paused: false, t: 0, auditFilter: 'all', draft: '', pipeChatOpen: false,
      subagents: [], events: [], policy: [], audit: [], chat: [],
      totalSpawned: 0, totalDone: 0, allowCount: 0, denyCount: 0,
      activeProfile: 'anthropic.default',
      pipeSteps: [], pipeName: 'feature-dev', pipeRunning: false, pipeCur: -1, pipeProg: 0, pipeDoneFlag: false,
    }
  }

  subscribe(cb) { this.subs.add(cb); return () => this.subs.delete(cb) }
  _notify() { for (const cb of this.subs) cb() }
  _setLocal(patch) { this.state = { ...this.state, ...patch }; this._notify() }
  _mergeDomain(dom) {
    if (!dom || typeof dom !== 'object') return
    const patch = {}
    for (const k of DOMAIN_KEYS) if (k in dom) patch[k] = dom[k]
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
      selectAgent: (h) => this._setLocal({ view: 'orchestrator', selected: h }),
      clearSelect: local({ selected: null }),
      setAuditFilter: (f) => this._setLocal({ auditFilter: f }),
      openPipeChat: local({ pipeChatOpen: true }),
      closePipeChat: local({ pipeChatOpen: false }),
      onDraft: (e) => this._setLocal({ draft: e.target.value }),
      onDraftKey: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.actions.sendChat() } },
      // backend-mutating actions
      togglePause: () => this._action('toggle_pause'),
      selectProfile: (n) => this._action('select_profile', [n]),
      sendChat: () => {
        const d = (this.state.draft || '').trim()
        if (!d) return
        this._setLocal({ draft: '' })
        this._action('send_chat', [d])
      },
      addPipe: (r) => this._action('add_pipe', [r]),
      removePipe: (u) => this._action('remove_pipe', [u]),
      movePipe: (u, dir) => this._action('move_pipe', [u, dir]),
      clearPipe: () => this._action('clear_pipe'),
      loadPreset: (n) => {
        // optimistic name so the chip updates instantly; backend confirms steps
        const p = pipePresets.find((x) => x.name === n)
        if (p) this._setLocal({ pipeName: n })
        this._action('load_preset', [n])
      },
      runPipe: () => this._action('run_pipe'),
      stopPipe: () => this._action('stop_pipe'),
      resetPipe: () => this._action('reset_pipe'),
    }
    return this._actions
  }
}
