// Pure presentation layer. Maps a domain state object + an actions object into
// the flat view-model the React views render. Colours are derived from
// role/status here, so the backend only needs to supply domain fields.
import {
  statusMeta, pipeCatalog, pipePresets, policyRoleDefs, profileDefs, skillDefs,
  hueFor, modelColorFor,
} from './data.js'
import { roleChip, navStyle, auditBtn } from './styleHelpers.js'
import { fmtClock } from './format.js'

export function buildViewModel(s, act) {
  const sm = statusMeta
  const shown = s.subagents.slice(-3)
  const slots = [{ cx: 189, cy: 300 }, { cx: 500, cy: 300 }, { cx: 811, cy: 300 }]
  const subagents = shown.map((a, i) => {
    const m = sm[a.status]
    const hue = hueFor(a.role)
    const sel = a.handle === s.selected
    const sl = slots[i] || slots[2]
    const cardStyle = 'position:absolute;left:' + sl.cx + 'px;top:' + sl.cy + 'px;transform:translate(-50%,0);width:218px;z-index:2;background:#19212f;border:1px solid ' + (sel ? '#ff9b3d' : (a.status === 'running' ? hue + '55' : 'rgba(255,255,255,0.08)')) + ';border-radius:12px;padding:14px 15px;cursor:pointer;transition:border-color .15s, box-shadow .15s;' + (sel ? 'box-shadow:0 0 0 1px #ff9b3d, 0 0 26px rgba(255,155,61,0.14);' : 'box-shadow:0 8px 24px rgba(0,0,0,0.4);')
    const pct = Math.round(a.turns / a.max * 100)
    return {
      role: a.role, handle: a.handle, brief: a.brief, statusLabel: m.label, statusColor: m.color,
      model: a.model, profile: a.profile, modelColor: modelColorFor(a.role),
      orderLabel: '#' + a.id,
      orderBadge: 'display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:18px;padding:0 5px;border-radius:5px;font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#cfcfd4;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12)',
      roleChipStyle: roleChip(a.role, hue), cardStyle,
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + m.color + ';' + (a.status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      barFillStyle: 'height:100%;width:' + pct + '%;background:' + m.color + ';border-radius:2px;transition:width .4s ease',
      turnsLabel: a.turns + '/' + a.max, toolCount: a.tools.length,
      wallLabel: a.status === 'running' ? fmtClock(a.wall) : '—',
      onSelect: () => act.selectAgent(a.handle),
    }
  })

  const selAgent = s.subagents.find((a) => a.handle === s.selected)
  let detail = null
  if (selAgent) {
    const m = sm[selAgent.status]
    const fw = selAgent.role === 'reviewer-aux'
    detail = {
      role: selAgent.role, handle: selAgent.handle, brief: selAgent.brief, parent: selAgent.parent,
      model: selAgent.model, profile: selAgent.profile, modelColor: modelColorFor(selAgent.role),
      statusLabel: m.label, statusColor: m.color, tools: selAgent.tools,
      roleChipStyle: roleChip(selAgent.role, hueFor(selAgent.role)),
      dotStyle: 'width:7px;height:7px;border-radius:50%;background:' + m.color + ';' + (selAgent.status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      turnsLabel: selAgent.turns + '/' + selAgent.max, wallFull: fmtClock(selAgent.wall) + ' / 5:00', toolsUsed: selAgent.turns,
      firewallStyle: fw
        ? 'font-size:11px;color:#9ed8b4;line-height:1.5;padding:11px 13px;background:rgba(158,216,180,0.07);border:1px solid rgba(158,216,180,0.25);border-radius:8px'
        : 'font-size:11px;color:#7a7a82;line-height:1.5;padding:11px 13px;background:#19212f;border:1px solid rgba(255,255,255,0.06);border-radius:8px',
      firewallNote: fw
        ? 'Firewalled brief — this reviewer sees code only. Any tool outside its surface is denied fail-closed (HI #3).'
        : 'Restricted tool surface. Out-of-surface calls hit the PreToolUse gate and are denied fail-closed.',
    }
  }

  const policy = s.policy.map((d) => ({
    ts: d.ts, verdict: d.verdict, tool: d.tool, role: d.role, reason: d.reason,
    roleChipStyle: roleChip(d.role, hueFor(d.role)),
    verdictStyle: d.verdict === 'ALLOW'
      ? 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;padding:2px 8px;border-radius:5px;color:#54c79a;background:rgba(84,199,154,0.12);border:1px solid rgba(84,199,154,0.3);flex-shrink:0;width:48px;text-align:center'
      : 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;padding:2px 8px;border-radius:5px;color:#e86a5f;background:rgba(232,106,95,0.12);border:1px solid rgba(232,106,95,0.32);flex-shrink:0;width:48px;text-align:center',
  }))

  const policyRoles = policyRoleDefs.map((r) => ({ ...r, chipStyle: roleChip(r.role, r.hue) }))

  let auditView = s.audit
  if (s.auditFilter === 'spawned') auditView = s.audit.filter((r) => r.event === 'spawned')
  else if (s.auditFilter === 'completed') auditView = s.audit.filter((r) => r.event === 'completed')
  auditView = auditView.map((r) => ({
    id: '#' + r.id, ts: r.ts, event: r.event, role: r.role, handle: r.handle, detail: r.detail,
    statusLabel: r.event === 'spawned' ? '—' : r.status,
    statusColor: r.event === 'spawned' ? '#5a5a62' : (sm[r.status]?.color || '#9b9ba2'),
    roleChipStyle: roleChip(r.role, hueFor(r.role)),
    eventStyle: r.event === 'spawned'
      ? 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:#8a8a92;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);padding:2px 7px;border-radius:5px;justify-self:start'
      : 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:#ff9b3d;background:rgba(255,155,61,0.1);border:1px solid rgba(255,155,61,0.3);padding:2px 7px;border-radius:5px;justify-self:start',
  }))

  const profiles = profileDefs.map((p) => {
    const active = p.name === s.activeProfile
    return {
      name: p.name, family: p.family, model: p.model, transport: p.transport, endpoint: p.endpoint, keyEnv: p.keyEnv,
      cardStyle: 'background:#141b27;border:1px solid ' + (active ? 'rgba(255,155,61,0.45)' : 'rgba(255,255,255,0.07)') + ';border-radius:12px;padding:16px 18px;' + (active ? 'box-shadow:0 0 0 1px rgba(255,155,61,0.25);' : ''),
      familyStyle: roleChip(p.family, p.fc),
      statusLabel: active ? 'ACTIVE' : 'configured',
      statusStyle: active
        ? 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#ff9b3d;background:rgba(255,155,61,0.12);border:1px solid rgba(255,155,61,0.35);padding:2px 9px;border-radius:5px'
        : 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:#6a6a72;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);padding:2px 9px;border-radius:5px',
      btnLabel: active ? 'selected' : 'select profile',
      btnStyle: 'width:100%;font-family:\'IBM Plex Mono\',monospace;font-size:11px;padding:8px;border-radius:8px;cursor:' + (active ? 'default' : 'pointer') + ';' + (active ? 'background:transparent;border:1px solid rgba(255,255,255,0.06);color:#5a5a62' : 'background:#232c3c;border:1px solid rgba(255,255,255,0.12);color:#e9e9ea'),
      onSelect: () => act.selectProfile(p.name),
    }
  })
  const activeDef = profileDefs.find((p) => p.name === s.activeProfile) || profileDefs[0]

  const skills = skillDefs.map((sk) => ({
    name: sk.name, appliesTo: sk.appliesTo, desc: sk.desc, mode: sk.mode,
    modeStyle: sk.mode === 'PUSHED'
      ? 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#ff9b3d;background:rgba(255,155,61,0.1);border:1px solid rgba(255,155,61,0.3);padding:2px 8px;border-radius:5px'
      : 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#8ab4d8;background:rgba(138,180,216,0.1);border:1px solid rgba(138,180,216,0.3);padding:2px 8px;border-radius:5px',
  }))

  // ── pipeline studio view-model ──
  const stColor = { idle: '#6a6a72', queued: '#e3b341', running: '#ff9b3d', done: '#54c79a' }
  const pipeRun = s.pipeRunning
  const editable = !pipeRun && !s.pipeDoneFlag
  const pipeStepsVM = s.pipeSteps.map((st, i) => {
    const cat = pipeCatalog.find((c) => c.role === st.role) || { tools: [], max: 0, hue: '#8a8a92' }
    const col = stColor[st.status]
    const prog = st.status === 'done' ? 100 : (st.status === 'running' ? s.pipeProg : 0)
    return {
      uid: st.uid, role: st.role, desc: cat.desc, handle: st.handle || '—',
      orderLabel: String(i + 1).padStart(2, '0'),
      orderBadge: 'display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:20px;padding:0 6px;border-radius:6px;font-family:\'IBM Plex Mono\',monospace;font-size:10.5px;font-weight:600;color:' + (st.status === 'idle' ? '#9b9ba2' : col) + ';background:' + (st.status === 'idle' ? 'rgba(255,255,255,0.06)' : col + '1f') + ';border:1px solid ' + (st.status === 'idle' ? 'rgba(255,255,255,0.12)' : col + '55'),
      roleChipStyle: roleChip(st.role, cat.hue),
      toolsLabel: cat.tools.length + ' tools', maxLabel: 'max ' + cat.max + ' turns',
      statusLabel: st.status, statusColor: col,
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + col + ';' + (st.status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      barFillStyle: 'height:100%;width:' + prog + '%;background:' + col + ';border-radius:3px;transition:width .4s ease',
      cardStyle: 'position:relative;width:208px;flex-shrink:0;background:#141b27;border:1px solid ' + (st.status === 'running' ? 'rgba(255,155,61,0.55)' : (st.status === 'done' ? 'rgba(84,199,154,0.42)' : 'rgba(255,255,255,0.08)')) + ';border-radius:12px;padding:14px 15px;' + (st.status === 'running' ? 'box-shadow:0 0 22px rgba(255,155,61,0.13);' : ''),
      showControls: editable, showHandle: (st.status === 'running' || st.status === 'done'),
      onUp: () => act.movePipe(st.uid, -1), onDown: () => act.movePipe(st.uid, 1), onRemove: () => act.removePipe(st.uid),
      showConnector: i < s.pipeSteps.length - 1,
      connStyle: 'color:' + (st.status === 'done' ? '#54c79a' : '#3a4250'),
    }
  })
  const pipeCatalogVM = pipeCatalog.map((c) => ({
    role: c.role, desc: c.desc, roleChipStyle: roleChip(c.role, c.hue), toolsLabel: c.tools.length + ' tools',
    cardStyle: 'text-align:left;width:100%;background:#141b27;border:1px solid rgba(255,255,255,0.07);border-radius:10px;padding:11px 13px;cursor:' + (pipeRun ? 'default' : 'pointer') + ';opacity:' + (pipeRun ? '0.45' : '1') + ';transition:border-color .14s',
    onAdd: pipeRun ? (() => {}) : (() => act.addPipe(c.role)),
  }))
  const pipePresetsVM = pipePresets.map((p) => ({
    name: p.name, countLabel: p.roles.length + ' agents',
    btnStyle: 'display:flex;align-items:center;justify-content:space-between;width:100%;font-family:\'IBM Plex Mono\',monospace;font-size:11px;padding:8px 11px;border-radius:8px;cursor:' + (pipeRun ? 'default' : 'pointer') + ';background:' + (s.pipeName === p.name ? 'rgba(255,155,61,0.1)' : '#19212f') + ';border:1px solid ' + (s.pipeName === p.name ? 'rgba(255,155,61,0.4)' : 'rgba(255,255,255,0.08)') + ';color:' + (s.pipeName === p.name ? '#ff9b3d' : '#9b9ba2') + ';opacity:' + (pipeRun ? '0.5' : '1'),
    onLoad: pipeRun ? (() => {}) : (() => act.loadPreset(p.name)),
  }))
  let runLabel, runAction
  if (pipeRun) { runLabel = '■ Stop'; runAction = () => act.stopPipe() }
  else if (s.pipeDoneFlag) { runLabel = '↻ Reset'; runAction = () => act.resetPipe() }
  else { runLabel = '▶ Run pipeline'; runAction = () => act.runPipe() }
  const runDisabled = !pipeRun && !s.pipeDoneFlag && s.pipeSteps.length === 0
  const runStyle = 'display:inline-flex;align-items:center;gap:8px;font-family:\'IBM Plex Mono\',monospace;font-size:12px;padding:9px 16px;border-radius:9px;cursor:' + (runDisabled ? 'not-allowed' : 'pointer') + ';border:1px solid ' + (pipeRun ? 'rgba(232,106,95,0.5)' : 'rgba(255,155,61,0.5)') + ';background:' + (pipeRun ? 'rgba(232,106,95,0.14)' : 'rgba(255,155,61,0.14)') + ';color:' + (pipeRun ? '#e86a5f' : '#ff9b3d') + ';opacity:' + (runDisabled ? '0.4' : '1')
  let pipeStatusText
  if (pipeRun) pipeStatusText = 'running · step ' + (s.pipeCur + 1) + ' / ' + s.pipeSteps.length + ' · each handoff tied to the audit'
  else if (s.pipeDoneFlag) pipeStatusText = 'complete · ' + s.pipeSteps.length + ' agents ran in order · tied to the audit'
  else pipeStatusText = s.pipeSteps.length ? (s.pipeSteps.length + ' agents staged · run in order, fail-closed at each handoff') : 'empty · add agents to compose a pipeline'

  const chatView = s.chat.map((msg) => {
    if (msg.role === 'you') {
      return {
        text: msg.text, showMeta: false, meta: '', metaStyle: '',
        rowStyle: 'display:flex;justify-content:flex-end;padding:4px 16px',
        bubbleStyle: 'max-width:82%;background:rgba(255,155,61,0.14);border:1px solid rgba(255,155,61,0.32);color:#fde9d6;padding:8px 12px;border-radius:13px 13px 4px 13px;font-size:12.5px;line-height:1.45',
      }
    }
    if (msg.role === 'driver') {
      return {
        text: msg.text, showMeta: true, meta: 'driver · the knot · ' + (msg.ts || ''),
        metaStyle: 'font-size:9.5px;color:#6a6a72;font-family:\'IBM Plex Mono\',monospace;padding-left:3px',
        rowStyle: 'display:flex;flex-direction:column;align-items:flex-start;gap:3px;padding:4px 16px',
        bubbleStyle: 'max-width:86%;background:#19212f;border:1px solid rgba(255,255,255,0.07);color:#d4d4d8;padding:8px 12px;border-radius:13px 13px 13px 4px;font-size:12.5px;line-height:1.45',
      }
    }
    const red = msg.tone === 'deny'
    return {
      text: msg.text, showMeta: false, meta: '', metaStyle: '',
      rowStyle: 'display:flex;justify-content:center;padding:5px 16px',
      bubbleStyle: 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:' + (red ? '#e86a5f' : '#7a7a82') + ';background:' + (red ? 'rgba(232,106,95,0.08)' : 'rgba(255,255,255,0.03)') + ';border:1px solid ' + (red ? 'rgba(232,106,95,0.25)' : 'rgba(255,255,255,0.07)') + ';padding:4px 11px;border-radius:20px;letter-spacing:0.02em;text-align:center',
    }
  })

  const sourceLabels = {
    'musubi-db': 'MUSUBI_DB audit.db',
    'musubi-root': 'MUSUBI_ROOT audit.db',
    workspace: 'workspace audit.db',
    package: 'package audit.db',
    none: 'no audit DB',
    demo: 'demo data',
  }
  const setup = s.setupStatus || {}
  const setupRows = [
    { label: 'Project root', value: setup.projectRoot || 'not detected', ok: !!setup.projectRoot },
    { label: 'Audit DB', value: setup.auditDbPath || 'not configured', ok: !['demo', 'none'].includes(setup.auditDbSource) },
    { label: 'Python', value: setup.pythonCli?.path || setup.pythonCli?.hint || 'not found', ok: !!setup.pythonCli?.found },
    { label: 'musubi CLI', value: setup.musubiCli?.path || setup.musubiCli?.hint || 'not found', ok: !!setup.musubiCli?.found },
    { label: 'agent CLI', value: setup.agentCli?.path || setup.agentCli?.hint || 'not found', ok: !!setup.agentCli?.found },
    { label: 'LLM config', value: setup.llmConfigPath || 'not configured', ok: !!setup.llmConfigured },
  ].map((row) => ({
    ...row,
    badge: row.ok ? 'OK' : 'CHECK',
    badgeStyle: 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:' + (row.ok ? '#54c79a' : '#e3b341') + ';background:' + (row.ok ? 'rgba(84,199,154,0.12)' : 'rgba(227,179,65,0.12)') + ';border:1px solid ' + (row.ok ? 'rgba(84,199,154,0.32)' : 'rgba(227,179,65,0.32)') + ';padding:2px 8px;border-radius:5px;flex-shrink:0',
  }))

  return {
    isOrch: s.view === 'orchestrator', isPipeline: s.view === 'pipeline', isPolicy: s.view === 'policy', isAudit: s.view === 'audit', isModels: s.view === 'models', isSkills: s.view === 'skills', isSettings: s.view === 'settings',
    view: s.view,
    runtimeSourceLabel: sourceLabels[s.runtimeSource] || 'audit.db',
    orchNav: navStyle(s.view === 'orchestrator'), pipeNav: navStyle(s.view === 'pipeline'), polNav: navStyle(s.view === 'policy'), audNav: navStyle(s.view === 'audit'), modNav: navStyle(s.view === 'models'), sklNav: navStyle(s.view === 'skills'), settingsNav: navStyle(s.view === 'settings'),
    selOrch: () => act.setView('orchestrator'), selPipe: () => act.setView('pipeline'), selPolicy: () => act.setView('policy'), selAudit: () => act.setView('audit'), selModels: () => act.setView('models'), selSkills: () => act.setView('skills'), selSettings: () => act.setView('settings'),
    pipeStepsView: pipeStepsVM, pipeCatalog: pipeCatalogVM, pipePresets: pipePresetsVM, pipeName: s.pipeName, pipeEmpty: s.pipeSteps.length === 0, pipeHasSteps: s.pipeSteps.length > 0, runLabel, runAction, runStyle, pipeStatusText, onClearPipe: () => act.clearPipe(),
    pipeChatOpen: s.pipeChatOpen, openPipeChat: () => act.openPipeChat(), closePipeChat: () => act.closePipeChat(),
    pipeDriverStyle: 'width:144px;flex-shrink:0;align-self:center;background:#19212f;border:1px solid ' + (s.pipeChatOpen ? '#ff9b3d' : 'rgba(255,155,61,0.4)') + ';border-radius:12px;padding:14px;text-align:center;cursor:pointer;transition:border-color .15s;' + (s.pipeChatOpen ? 'box-shadow:0 0 0 1px #ff9b3d, 0 0 22px rgba(255,155,61,0.14);' : ''),
    activeModel: activeDef.model, activeProfileName: s.activeProfile,
    runningCount: s.subagents.filter((a) => a.status === 'running').length, totalDone: s.totalDone, totalSpawned: s.totalSpawned, driverCycle: s.t || 0,
    togglePause: () => act.togglePause(), pauseLabel: s.paused ? '▶ Resume' : '∥ Pause',
    driverStyle: 'position:absolute;left:500px;top:0;transform:translate(-50%,0);z-index:3;background:#19212f;border:1px solid rgba(255,155,61,0.4);border-radius:14px;padding:16px 24px;min-width:296px;text-align:center;' + (!s.paused ? 'animation:glow 3s ease-in-out infinite;' : 'box-shadow:0 10px 34px rgba(0,0,0,0.5);'),
    driverDotStyle: 'width:8px;height:8px;border-radius:50%;background:#ff9b3d;' + (!s.paused ? 'animation:pulse 1.6s ease-in-out infinite;' : ''),
    subagents, webShown: shown,
    hasDetail: !!detail, showFeed: !detail, detail, clearSelect: () => act.clearSelect(),
    events: s.events, chat: chatView, draft: s.draft, onDraft: act.onDraft, onDraftKey: act.onDraftKey, onSend: act.sendChat,
    policy, policyRoles, allowCount: s.allowCount, denyCount: s.denyCount,
    auditView, auditCountLabel: auditView.length + ' rows · immutable',
    setAuditAll: () => act.setAuditFilter('all'), setAuditSpawn: () => act.setAuditFilter('spawned'), setAuditDone: () => act.setAuditFilter('completed'),
    auditFAll: auditBtn(s.auditFilter === 'all'), auditFSpawn: auditBtn(s.auditFilter === 'spawned'), auditFDone: auditBtn(s.auditFilter === 'completed'),
    profiles, skills, setupRows, setupPathHint: setup.pathHint || '',
  }
}
