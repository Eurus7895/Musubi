// In-browser simulation DataSource — the default for `npm run dev` and any
// build running outside Tauri. Generates governed spawns, policy decisions and
// audit rows on an interval. Ported from the Claude Design prototype's engine.
//
// DataSource contract (also implemented by TauriSource):
//   .state                 domain + UI state object consumed by buildViewModel
//   .actions               { setView, selectAgent, sendChat, runPipe, … }
//   .subscribe(cb) → off    notified on every state change
//   .start() / .stop()      begin / end live updates
import {
  roleMeta, statusMeta, roleOrder, C, pipePresets, pipeCatalog,
} from './data.js'

export default class SimulationSource {
  constructor(props) {
    this.props = props || {}
    this.subs = new Set()
    this.seq = 0; this.aid = 0; this.eid = 0; this.pid = 0; this.puid = 0
    this.base = Date.now() - 95000
    this.timer = null
    this.state = this.seed()
  }

  // ── DataSource plumbing ──
  subscribe(cb) { this.subs.add(cb); return () => this.subs.delete(cb) }
  _notify() { for (const cb of this.subs) cb() }
  setState(update, cb) {
    const patch = typeof update === 'function' ? update(this.state) : update
    this.state = { ...this.state, ...(patch || {}) }
    this._notify()
    if (cb) cb()
  }
  start() {
    const sv = this.props.startView
    if (sv && sv !== this.state.view) this.setState({ view: sv })
    if (this.props.live === false) this.setState({ paused: true })
    const speed = { Calm: 1700, Normal: 1100, Brisk: 700 }[this.props.simSpeed] || 1100
    this.timer = setInterval(() => { this.tick(); this.pipeAdvance() }, speed)
  }
  stop() { if (this.timer) clearInterval(this.timer) }

  get actions() {
    if (this._actions) return this._actions
    this._actions = {
      setView: (v) => this.setState({ view: v }),
      selectAgent: (h) => this.setState({ view: 'orchestrator', selected: h }),
      clearSelect: () => this.setState({ selected: null }),
      togglePause: () => this.setState((st) => ({ paused: !st.paused })),
      onDraft: this.onDraft, onDraftKey: this.onDraftKey, sendChat: this.sendChat,
      setAuditFilter: (f) => this.setState({ auditFilter: f }),
      selectProfile: (n) => this.setState({ activeProfile: n }),
      addPipe: (r) => this.addPipe(r), removePipe: (u) => this.removePipe(u), movePipe: (u, d) => this.movePipe(u, d),
      clearPipe: () => this.clearPipe(), loadPreset: (n) => this.loadPreset(n),
      runPipe: () => this.runPipe(), stopPipe: () => this.stopPipe(), resetPipe: () => this.resetPipe(),
      openPipeChat: () => this.setState({ pipeChatOpen: true }), closePipeChat: () => this.setState({ pipeChatOpen: false }),
    }
    return this._actions
  }

  // ── seed ──
  seed() {
    this.base = Date.now() - 95000
    const A = [
      this.mkAgent('explorer', -82, { turns: 4 }),
      this.mkAgent('investigator', -64, { turns: 6 }),
      this.mkAgent('reviewer-aux', -40, { turns: 2 }),
      this.mkAgent('explorer', -18, { turns: 1 }),
    ]
    const events = [], policy = [], audit = []
    A.forEach((a) => {
      audit.push(this.mkAudit('spawned', a, a.born))
      events.push({ id: ++this.eid, ts: this.fmtTs(a.born), glyph: '↳', color: a.hue, text: 'spawned ' + a.role + ' · ' + a.handle, sub: a.tools.length + ' tools · max ' + a.max + ' turns' })
    })
    policy.push(this.mkDecision('ALLOW', 'musubi_read_file', A[0], 'in surface'))
    policy.push(this.mkDecision('ALLOW', 'musubi_run_command', A[1], 'in surface'))
    policy.push(this.mkDecision('DENY', 'musubi_write_file', A[2], 'outside firewall surface — code-only (HI #3)'))
    policy.push(this.mkDecision('ALLOW', 'musubi_read_file', A[2], 'in surface'))
    events.reverse(); policy.reverse(); audit.reverse()
    const chat = [
      { role: 'you', ts: this.fmtTs(0), text: 'Audit why run_command is denied for the reviewer. Tie everything to policy.' },
      { role: 'driver', ts: this.fmtTs(0), text: 'On it. I reach the model through one inject point and spawn governed threads — each turn-capped, firewalled, and bound into the audit.' },
      { role: 'system', tone: 'spawn', text: 'tied explorer · investigator · reviewer-aux into the audit' },
      { role: 'driver', ts: this.fmtTs(0), text: 'reviewer-aux runs code-only (HI #3). run_command sits outside its surface, so the PreToolUse gate denies it fail-closed — by design, not a bug.' },
    ]
    return {
      view: 'orchestrator', selected: null, paused: false, t: 0,
      subagents: A, events, policy, audit, chat, draft: '',
      auditFilter: 'all', activeProfile: 'anthropic.default',
      totalSpawned: A.length, totalDone: 0, allowCount: 3, denyCount: 1,
      pipeSteps: this.mkPipeSteps(['explorer', 'planner', 'coder', 'reviewer']),
      pipeName: 'feature-dev', pipeRunning: false, pipeCur: -1, pipeProg: 0, pipeDoneFlag: false, pipeChatOpen: false,
    }
  }

  // ── chat ──
  onDraft = (e) => { this.setState({ draft: e.target.value }) }
  onDraftKey = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendChat() } }
  sendChat = () => {
    const d = (this.state.draft || '').trim()
    if (!d) return
    this.setState((st) => {
      const chat = st.chat.slice()
      chat.push({ role: 'you', ts: this.fmtTs(st.t), text: d })
      chat.push({ role: 'driver', ts: this.fmtTs(st.t), text: 'Acknowledged — re-checking the policy surface and re-tying threads to the audit for: “' + d.slice(0, 72) + '”' })
      return { chat: chat.slice(-60), draft: '' }
    })
  }

  // ── utils ──
  rhex() { let s = ''; for (let i = 0; i < 8; i++) s += Math.floor(Math.random() * 16).toString(16); return s }
  pick(a) { return a[Math.floor(Math.random() * a.length)] }
  fmtTs(t) {
    const n = Number.isFinite(t) ? t : 0
    const d = new Date(this.base + n * 1100)
    const s = d.toTimeString()
    return s.startsWith('Invalid') ? new Date(this.base).toTimeString().slice(0, 8) : s.slice(0, 8)
  }
  fmtClock(s) { const m = Math.floor(s / 60); return m + ':' + String(s % 60).padStart(2, '0') }

  // ── factories ──
  mkAgent(role, bornOffset, over) {
    const m = roleMeta[role]
    return Object.assign({
      id: ++this.seq, handle: this.rhex(), role, hue: m.hue, brief: this.pick(m.briefs),
      status: 'running', final: null, turns: 0, max: m.max, tools: m.tools, wall: 300,
      model: m.model, profile: m.profile, modelColor: m.modelColor,
      born: (this.state ? this.state.t : 0) + (bornOffset ? 0 : 0), doneAt: null, parent: 'driver · agent-loop',
    }, over || {})
  }
  mkAudit(event, a, t) {
    return {
      id: ++this.aid, ts: this.fmtTs(t == null ? this.state.t : t), event, role: a.role, hue: a.hue, handle: a.handle,
      detail: event === 'spawned' ? ('allowed_tools=[' + a.tools.length + '] max_turns=' + a.max) : ('turns=' + a.turns + ' tools_used=' + a.turns + (a.status === 'done' ? '' : ' err')),
      status: event === 'spawned' ? null : a.status,
    }
  }
  mkDecision(verdict, tool, a, reason) {
    return { id: ++this.pid, ts: this.fmtTs(this.state ? this.state.t : 0), verdict, tool, role: a.role, hue: a.hue, handle: a.handle, reason }
  }

  // ── tick ──
  tick() {
    if (this.state.paused) return
    this.setState((s) => {
      const t = s.t + 1
      let subs = s.subagents.map((a) => ({ ...a }))
      let events = s.events.slice(), policy = s.policy.slice(), audit = s.audit.slice(), chat = s.chat.slice()
      let { totalSpawned, totalDone, allowCount, denyCount } = s

      subs.forEach((a) => {
        if (a.status !== 'running') return
        a.turns = Math.min(a.max, a.turns + 1)
        a.wall = Math.max(0, a.wall - (10 + (a.id % 6)))
        if (t % 2 === 0) {
          const tool = a.tools[a.turns % a.tools.length]
          policy.unshift(this.mkDecisionAt('ALLOW', tool, a, 'in surface', t)); allowCount++
        }
        if (a.turns >= a.max || a.wall <= 0) {
          a.status = a.wall <= 0 ? 'escalated' : (a.id % 9 === 0 ? 'failed' : 'done')
          a.final = a.status; a.doneAt = t
          if (a.status === 'done') totalDone++
          const sm = statusMeta[a.status]
          events.unshift({ id: ++this.eid, ts: this.fmtTs(t), glyph: a.status === 'done' ? '✓' : (a.status === 'escalated' ? '▲' : '✕'), color: sm.color, text: a.handle + ' ' + a.status + ' · ' + a.turns + ' turns', sub: a.brief })
          audit.unshift(this.mkAuditAt('completed', a, t))
          chat.push({ role: 'system', tone: a.status === 'done' ? 'spawn' : 'deny', text: a.handle + ' ' + a.status + ' · ' + a.turns + ' turns' })
        }
      })

      // periodic fail-closed deny on a reviewer-aux (firewall surface)
      if (t % 6 === 0) {
        const r = subs.find((a) => a.status === 'running' && a.role === 'reviewer-aux') || subs.find((a) => a.status === 'running')
        if (r) {
          const denied = r.role === 'reviewer-aux' ? 'musubi_write_file' : 'musubi_spawn_subagent'
          const reason = r.role === 'reviewer-aux' ? 'outside firewall surface — code-only (HI #3)' : 'unknown (agent, tool) — fail-closed'
          policy.unshift(this.mkDecisionAt('DENY', denied, r, reason, t)); denyCount++
          events.unshift({ id: ++this.eid, ts: this.fmtTs(t), glyph: '⛔', color: C.red, text: 'DENY ' + denied, sub: r.role + ' · ' + r.handle + ' · fail-closed' })
          chat.push({ role: 'system', tone: 'deny', text: 'DENY ' + denied + ' · ' + r.role + ' · fail-closed' })
        }
      }

      // age out finished agents
      subs = subs.filter((a) => a.status === 'running' || (t - (a.doneAt || t)) < 3)

      // spawn to keep the cohort alive
      const running = subs.filter((a) => a.status === 'running').length
      if (running < 3 && subs.length < 4 && t % 2 === 1) {
        const role = roleOrder[totalSpawned % roleOrder.length]
        const na = this.mkAgentAt(role, t)
        subs.push(na); totalSpawned++
        events.unshift({ id: ++this.eid, ts: this.fmtTs(t), glyph: '↳', color: na.hue, text: 'spawned ' + na.role + ' · ' + na.handle, sub: na.tools.length + ' tools · max ' + na.max + ' turns' })
        audit.unshift(this.mkAuditAt('spawned', na, t))
        policy.unshift(this.mkDecisionAt('ALLOW', 'musubi_spawn_subagent', { role: 'driver', hue: C.amber, handle: 'driver' }, 'in policy', t)); allowCount++
        chat.push({ role: 'system', tone: 'spawn', text: 'tied ' + na.role + ' · ' + na.handle + ' into the audit' })
      }

      if (events.length > 60) events = events.slice(0, 60)
      if (policy.length > 50) policy = policy.slice(0, 50)
      if (audit.length > 120) audit = audit.slice(0, 120)
      if (chat.length > 60) chat = chat.slice(-60)
      let selected = s.selected
      if (selected && !subs.find((a) => a.handle === selected)) selected = null

      return { t, subagents: subs, events, policy, audit, chat, totalSpawned, totalDone, allowCount, denyCount, selected }
    })
  }
  mkAgentAt(role, t) {
    const m = roleMeta[role]
    return { id: ++this.seq, handle: this.rhex(), role, hue: m.hue, brief: this.pick(m.briefs), status: 'running', final: null, turns: 0, max: m.max, tools: m.tools, wall: 300, model: m.model, profile: m.profile, modelColor: m.modelColor, born: t, doneAt: null, parent: 'driver · agent-loop' }
  }
  mkAuditAt(event, a, t) {
    return { id: ++this.aid, ts: this.fmtTs(t), event, role: a.role, hue: a.hue, handle: a.handle, detail: event === 'spawned' ? ('allowed_tools=[' + a.tools.length + '] max_turns=' + a.max) : ('turns=' + a.turns + ' tools_used=' + a.turns + (a.status === 'done' ? '' : ' err')), status: event === 'spawned' ? null : a.status }
  }
  mkDecisionAt(verdict, tool, a, reason, t) { return { id: ++this.pid, ts: this.fmtTs(t), verdict, tool, role: a.role, hue: a.hue || '#8a8a92', handle: a.handle, reason } }

  // ── pipeline studio ──
  mkPipeSteps(roles) { return roles.map((r) => ({ uid: ++this.puid, role: r, status: 'idle', handle: null })) }
  addPipe(role) { if (this.state.pipeRunning) return; this.setState((s) => ({ pipeSteps: [...s.pipeSteps, { uid: ++this.puid, role, status: 'idle', handle: null }], pipeName: 'custom', pipeDoneFlag: false })) }
  removePipe(uid) { if (this.state.pipeRunning) return; this.setState((s) => ({ pipeSteps: s.pipeSteps.filter((x) => x.uid !== uid), pipeName: 'custom', pipeDoneFlag: false })) }
  movePipe(uid, dir) {
    if (this.state.pipeRunning) return
    this.setState((s) => {
      const a = s.pipeSteps.slice()
      const i = a.findIndex((x) => x.uid === uid)
      const j = i + dir
      if (i < 0 || j < 0 || j >= a.length) return {}
      const t = a[i]; a[i] = a[j]; a[j] = t
      return { pipeSteps: a, pipeName: 'custom', pipeDoneFlag: false }
    })
  }
  clearPipe() { if (this.state.pipeRunning) return; this.setState({ pipeSteps: [], pipeName: 'custom', pipeDoneFlag: false, pipeCur: -1, pipeProg: 0 }) }
  loadPreset(name) { if (this.state.pipeRunning) return; const p = pipePresets.find((x) => x.name === name); if (!p) return; this.setState({ pipeSteps: this.mkPipeSteps(p.roles), pipeName: name, pipeDoneFlag: false, pipeCur: -1, pipeProg: 0 }) }
  runPipe() { this.setState((s) => { if (!s.pipeSteps.length) return {}; const steps = s.pipeSteps.map((x, i) => ({ ...x, status: i === 0 ? 'running' : 'queued', handle: i === 0 ? this.rhex() : null })); return { pipeRunning: true, pipeCur: 0, pipeProg: 0, pipeDoneFlag: false, pipeSteps: steps } }) }
  stopPipe() { this.setState((s) => ({ pipeRunning: false, pipeCur: -1, pipeProg: 0, pipeDoneFlag: false, pipeSteps: s.pipeSteps.map((x) => ({ ...x, status: 'idle', handle: null })) })) }
  resetPipe() { this.setState((s) => ({ pipeRunning: false, pipeCur: -1, pipeProg: 0, pipeDoneFlag: false, pipeSteps: s.pipeSteps.map((x) => ({ ...x, status: 'idle', handle: null })) })) }
  pipeAdvance() {
    if (!this.state.pipeRunning) return
    this.setState((s) => {
      if (!s.pipeRunning || s.pipeCur < 0) return {}
      const steps = s.pipeSteps.map((x) => ({ ...x }))
      const prog = s.pipeProg + 34
      if (prog >= 100) {
        steps[s.pipeCur].status = 'done'
        const next = s.pipeCur + 1
        if (next < steps.length) { steps[next].status = 'running'; steps[next].handle = this.rhex(); return { pipeSteps: steps, pipeCur: next, pipeProg: 0 } }
        return { pipeSteps: steps, pipeCur: -1, pipeProg: 0, pipeRunning: false, pipeDoneFlag: true }
      }
      return { pipeSteps: steps, pipeProg: prog }
    })
  }
}
