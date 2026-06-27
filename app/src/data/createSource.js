export function createSource(props) {
  const inTauri = typeof window !== 'undefined'
    && (!!window.__TAURI_INTERNALS__ || !!window.__TAURI__ || !!window.isTauri)
  if (!inTauri) {
    throw new Error('Musubi Console must run inside the Tauri desktop shell. Use `npm run tauri:dev` from app/.')
  }
  return new LazyTauri(props)
}

class LazyTauri {
  constructor(props) {
    this.props = props
    this.subs = new Set()
    this.inner = null
    this.state = {
      view: props.startView || 'orchestrator',
      selected: null, paused: false, t: 0, auditFilter: 'all', draft: '', pipeChatOpen: false,
      subagents: [], events: [], policy: [], audit: [], chat: [],
      totalSpawned: 0, totalDone: 0, allowCount: 0, denyCount: 0,
      activeProfile: 'anthropic.default',
      pipeSteps: [], pipeName: 'feature-dev', pipeRunning: false, pipeCur: -1, pipeProg: 0, pipeDoneFlag: false,
    }
  }
  subscribe(cb) { this.subs.add(cb); return () => this.subs.delete(cb) }
  get state() { return this._state }
  set state(v) { this._state = v }
  async start() {
    const { default: TauriSource } = await import('./TauriSource.js')
    this.inner = new TauriSource(this.props)
    this.inner.subscribe(() => { this._state = this.inner.state; for (const cb of this.subs) cb() })
    this._state = this.inner.state
    await this.inner.start()
    for (const cb of this.subs) cb()
  }
  stop() { this.inner && this.inner.stop && this.inner.stop() }
  get actions() {
    // Proxy so views can bind actions before the inner source resolves.
    return new Proxy({}, { get: (_, k) => (...a) => this.inner && this.inner.actions[k] && this.inner.actions[k](...a) })
  }
}
