import SimulationSource from '../sim/SimulationSource.js'

// Pick the DataSource for this environment:
//  - inside the Tauri shell → native TauriSource
//  - ?source=tauri | ?source=sim → explicit override
//  - otherwise → in-browser simulation
//
// Detection: Tauri v2 leaves `app.withGlobalTauri` at its default `false`, so
// `window.__TAURI__` is NOT injected in a packaged window. The always-present
// marker is `window.__TAURI_INTERNALS__`; check it first (and `isTauri`) so a
// normal desktop launch picks TauriSource — and honours MUSUBI_DB — without
// needing `?source=tauri`.
export function createSource(props) {
  const q = typeof location !== 'undefined' ? new URLSearchParams(location.search) : new URLSearchParams()
  const mode = props.source || q.get('source')
  const inTauri = typeof window !== 'undefined'
    && (!!window.__TAURI_INTERNALS__ || !!window.__TAURI__ || !!window.isTauri)
  if (mode === 'sim') return new SimulationSource(props)
  if (mode === 'tauri' || inTauri) {
    // Lazy so the simulation bundle never pulls in the Tauri source.
    return new LazyTauri(props)
  }
  return new SimulationSource(props)
}

// Wraps TauriSource behind a dynamic import while presenting the sync
// DataSource surface the hook expects. Falls back to simulation if the native
// module can't load (e.g. opened in a plain browser with ?source=tauri).
class LazyTauri {
  constructor(props) {
    this.props = props
    this.subs = new Set()
    this.inner = null
    this.state = new SimulationSource(props).state // placeholder until loaded
  }
  subscribe(cb) { this.subs.add(cb); return () => this.subs.delete(cb) }
  get state() { return this._state }
  set state(v) { this._state = v }
  async start() {
    try {
      const { default: TauriSource } = await import('./TauriSource.js')
      this.inner = new TauriSource(this.props)
    } catch (e) {
      console.error('[musubi] Tauri source unavailable, using simulation:', e)
      this.inner = new SimulationSource(this.props)
    }
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
