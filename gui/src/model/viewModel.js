// Pure presentation layer. Maps a domain state object + an actions object into
// the flat view-model the React views render. Colours are derived from
// role/status here, so the backend only needs to supply domain fields.
import {
  statusMeta, pipeCatalog, pipePresets, policyRoleDefs, profileDefs, skillDefs,
  hueFor, modelColorFor,
} from './data.js'
import { roleChip, navStyle, auditBtn } from './styleHelpers.js'
import { fmtClock } from './format.js'

function statusForRun(run) {
  const steps = run.steps || []
  if (run.live) return 'running'
  if (steps.some((a) => a.status === 'running')) return 'running'
  if (steps.some((a) => a.status === 'failed')) return 'failed'
  if (steps.some((a) => a.status === 'escalated')) return 'escalated'
  if (steps.length && steps.every((a) => a.status === 'abandoned')) return 'abandoned'
  if (steps.length && steps.every((a) => a.status === 'done')) return 'done'
  if (run.turn) return 'done'
  return steps[steps.length - 1]?.status || 'abandoned'
}

function isPipelineChatId(chatId) {
  return String(chatId || '').startsWith('gui-pipeline-')
}

function belongsToSurface(item, surface) {
  const chatId = item?.chatId || item?.chat_id || ''
  return surface === 'pipeline' ? isPipelineChatId(chatId) : !isPipelineChatId(chatId)
}

function groupRuns(subagents, agentTurns = [], driverStatus = {}, surface = 'orchestrator') {
  const byId = new Map()
  // Track each run's real recency (max member epoch) so worker sessions
  // (subagent_audit) and driver-only turns (agent_turns) — which arrive as two
  // separate lists — sort by actual time, not by load order. lastIndex is the
  // stable fallback when epochs are missing/equal.
  const bump = (run, epoch) => {
    const n = Number(epoch)
    if (Number.isFinite(n) && n > run.recency) run.recency = n
  }
  agentTurns.filter((turn) => belongsToSurface(turn, surface)).forEach((turn, index) => {
    const id = turn.parentSession || `driver-turn-${turn.id || index + 1}`
    if (!byId.has(id)) byId.set(id, { id, lastIndex: index, steps: [], recency: 0 })
    const run = byId.get(id)
    run.turn = turn
    run.lastIndex = Math.max(run.lastIndex, index)
    bump(run, turn.startedAt)
  })
  subagents.filter((agent) => belongsToSurface(agent, surface)).forEach((agent, index) => {
    const id = agent.parentSession || agent.parent || 'driver'
    if (!byId.has(id)) byId.set(id, { id, lastIndex: index, steps: [], recency: 0 })
    const run = byId.get(id)
    run.lastIndex = Math.max(run.lastIndex, agentTurns.length + index)
    run.steps.push(agent)
    bump(run, agent.spawnEpoch)
  })
  const runningSurface = driverStatus?.surface || 'orchestrator'
  if (driverStatus?.running && runningSurface === surface && !Array.from(byId.values()).some((run) => statusForRun(run) === 'running')) {
    const id = `driver-running-${driverStatus.startedAt || 'now'}`
    byId.set(id, {
      id,
      lastIndex: agentTurns.length + subagents.length,
      steps: [],
      live: true,
      task: driverStatus.task || 'Running driver turn',
      // The live run is happening now — always the newest.
      recency: Number.MAX_SAFE_INTEGER,
    })
  }
  return Array.from(byId.values()).sort((a, b) => (b.recency - a.recency) || (b.lastIndex - a.lastIndex))
}

function stopHintFor(agent, logText) {
  const log = String(logText || '').toLowerCase()
  if (log.includes('tokenbudgetexhaustederror') || log.includes('token budget halt') || log.includes('token budget exhausted')) {
    return 'Budget halted before the next model call.'
  }
  if (agent?.status === 'escalated' && Number(agent?.turns || 0) >= Number(agent?.max || 0) && Number(agent?.max || 0) > 0) {
    return 'Turn cap reached before a final answer.'
  }
  if (agent?.status === 'failed') return 'Worker failed. Open the process log or audit row for details.'
  if (agent?.status === 'abandoned') return 'Worker was abandoned before completion.'
  return ''
}

function runSummary(status, logText, run) {
  const log = String(logText || '').toLowerCase()
  if (log.includes('tokenbudgetexhaustederror') || log.includes('token budget halt') || log.includes('token budget exhausted')) {
    return 'Budget halted before the next model call.'
  }
  if (run?.turn && !(run.steps || []).length) return 'Driver turn completed without spawning workers.'
  if (status === 'running') return 'A worker in this run is still active.'
  if (status === 'done') return 'Run completed.'
  if (status === 'failed') return 'Run failed.'
  if (status === 'escalated') return 'Run escalated for operator attention.'
  if (status === 'abandoned') return 'Run is no longer active.'
  return 'No worker activity for this run yet.'
}

// Seed-cost line for a run's driver turn: how much prior conversation was
// replayed. Empty for a stateless/fresh-session turn (replayTokens 0).
function replayLineForRun(run) {
  const turn = run?.turn
  const tokens = Number(turn?.replayTokens || 0)
  if (!turn || tokens <= 0) return ''
  const msgs = Number(turn.replayMessages || 0)
  const k = tokens >= 1000
    ? (tokens / 1000).toFixed(tokens >= 10000 ? 0 : 1) + 'k'
    : String(tokens)
  return `replayed ${msgs} msg${msgs === 1 ? '' : 's'} · ${k} seed tok`
}

function statusCountLine(steps) {
  if (!steps.length) return 'driver-only turn'
  const counts = steps.reduce((acc, step) => {
    acc[step.status] = (acc[step.status] || 0) + 1
    return acc
  }, {})
  const parts = ['done', 'running', 'escalated', 'failed', 'abandoned']
    .filter((status) => counts[status])
    .map((status) => counts[status] + ' ' + status)
  return steps.length + ' steps' + (parts.length ? ' - ' + parts.join(' - ') : '')
}

function prettyRole(role) {
  const text = String(role || 'worker')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function retryLineForRun(steps) {
  const byRole = new Map()
  steps.forEach((step) => {
    const role = step.role || 'worker'
    if (!byRole.has(role)) byRole.set(role, [])
    byRole.get(role).push(step)
  })
  for (const [role, attempts] of byRole.entries()) {
    if (attempts.length < 2) continue
    const counts = attempts.reduce((acc, step) => {
      acc[step.status] = (acc[step.status] || 0) + 1
      return acc
    }, {})
    const parts = ['done', 'running', 'escalated', 'failed', 'abandoned']
      .filter((status) => counts[status])
      .map((status) => counts[status] + ' ' + status)
    return prettyRole(role) + ' retried: ' + parts.join(', ')
  }
  return ''
}

function focusLineForRun(steps, current) {
  if (!steps.length) return 'Driver handled this turn directly'
  const focus = current || steps[steps.length - 1]
  if (focus.status === 'running') return 'Current: ' + focus.role + ' - ' + focus.turns + '/' + focus.max + ' turns'
  const retryLine = retryLineForRun(steps)
  if (retryLine) return retryLine
  if (focus.status === 'done' && steps.every((step) => step.status === 'done')) return 'All steps completed'
  return 'Blocked at ' + focus.role
}

export function formatChatTimestamp(ts, locale = undefined, timeZone = undefined) {
  const raw = String(ts || '')
  const match = raw.match(/^epoch:(-?\d+(?:\.\d+)?)$/)
  if (!match) return raw
  const millis = Number(match[1]) * 1000
  if (!Number.isFinite(millis)) return raw
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    ...(timeZone ? { timeZone } : {}),
  }).format(new Date(millis))
}

function buildChatView(messages = []) {
  return messages.map((msg) => {
    if (msg.role === 'you') {
      return {
        text: msg.text, formatted: false, showMeta: false, meta: '', metaStyle: '',
        rowStyle: 'display:flex;justify-content:flex-end;padding:4px 16px',
        bubbleStyle: 'max-width:82%;background:rgba(255,155,61,0.14);border:1px solid rgba(255,155,61,0.32);color:#fde9d6;padding:8px 12px;border-radius:13px 13px 4px 13px;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere',
      }
    }
    if (msg.role === 'driver') {
      return {
        text: msg.text, formatted: true, showMeta: true, meta: 'driver · the knot · ' + formatChatTimestamp(msg.ts),
        metaStyle: 'font-size:9.5px;color:#6a6a72;font-family:\'IBM Plex Mono\',monospace;padding-left:3px',
        rowStyle: 'display:flex;flex-direction:column;align-items:flex-start;gap:3px;padding:4px 16px',
        bubbleStyle: 'max-width:86%;background:#19212f;border:1px solid rgba(255,255,255,0.07);color:#d4d4d8;padding:8px 12px;border-radius:13px 13px 13px 4px;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere',
      }
    }
    const red = msg.tone === 'deny'
    return {
      text: msg.text, formatted: false, showMeta: false, meta: '', metaStyle: '',
      rowStyle: 'display:flex;justify-content:center;padding:5px 16px',
      bubbleStyle: 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:' + (red ? '#e86a5f' : '#7a7a82') + ';background:' + (red ? 'rgba(232,106,95,0.08)' : 'rgba(255,255,255,0.03)') + ';border:1px solid ' + (red ? 'rgba(232,106,95,0.25)' : 'rgba(255,255,255,0.07)') + ';padding:4px 11px;border-radius:20px;letter-spacing:0.02em;text-align:center',
    }
  })
}

export function buildViewModel(s, act) {
  const sm = statusMeta
  const allSubagents = s.subagents || []
  const allAgentTurns = s.agentTurns || []
  const orchSubagents = allSubagents.filter((a) => belongsToSurface(a, 'orchestrator'))
  const orchAgentTurns = allAgentTurns.filter((t) => belongsToSurface(t, 'orchestrator'))
  const pipeSubagents = allSubagents.filter((a) => belongsToSurface(a, 'pipeline'))
  const pipeAgentTurns = allAgentTurns.filter((t) => belongsToSurface(t, 'pipeline'))
  const workerOrder = new Map(orchSubagents.map((a, i) => [a.handle, i + 1]))
  const selectedAgent = orchSubagents.find((a) => a.handle === s.selected)
  const latestAgent = orchSubagents[orchSubagents.length - 1]
  const latestTurn = orchAgentTurns[orchAgentTurns.length - 1]
  const driverStatusForRuns = s.driverStatus || {}
  const driverSurface = driverStatusForRuns.surface || 'orchestrator'
  const driverRunning = !!driverStatusForRuns.running
  const orchestratorOwnsDriver = driverRunning && driverSurface === 'orchestrator'
  const pipelineOwnsDriver = driverRunning && driverSurface === 'pipeline'
  const runsRaw = groupRuns(orchSubagents, orchAgentTurns, driverStatusForRuns, 'orchestrator')
  const pipeRunsRaw = groupRuns(pipeSubagents, pipeAgentTurns, driverStatusForRuns, 'pipeline')
  const pipeWorkerOrder = new Map(pipeSubagents.map((a, i) => [a.handle, i + 1]))
  const runningRun = runsRaw.find((run) => statusForRun(run) === 'running')
  const runningPipeRun = pipeRunsRaw.find((run) => statusForRun(run) === 'running')
  // A session the user explicitly clicked (honoured only while it still exists).
  const chosenSession = s.selectedSession && runsRaw.some((run) => run.id === s.selectedSession)
    ? s.selectedSession
    : null
  const activeSessionId = selectedAgent?.parentSession || chosenSession || runningRun?.id || latestTurn?.parentSession || latestAgent?.parentSession || runsRaw[0]?.id || ''
  const activeRunRaw = runsRaw.find((run) => run.id === activeSessionId)
  const activeSessionAgents = activeRunRaw?.steps || []
  const runningInSession = activeSessionAgents.find((a) => a.status === 'running')
  const currentSessionAgent = runningInSession || activeSessionAgents[activeSessionAgents.length - 1]
  const processTextForRuns = driverSurface === 'orchestrator'
    ? [driverStatusForRuns.stderrTail, driverStatusForRuns.stdoutTail].filter(Boolean).join('\n')
    : ''
  const processTextForPipeRuns = driverSurface === 'pipeline'
    ? [driverStatusForRuns.stderrTail, driverStatusForRuns.stdoutTail].filter(Boolean).join('\n')
    : ''
  const activeRunStatus = activeRunRaw ? statusForRun(activeRunRaw) : 'abandoned'
  const chosenPipeSession = s.selectedPipeSession && pipeRunsRaw.some((run) => run.id === s.selectedPipeSession)
    ? s.selectedPipeSession
    : null
  const latestPipeAgent = pipeSubagents[pipeSubagents.length - 1]
  const latestPipeTurn = pipeAgentTurns[pipeAgentTurns.length - 1]
  const activePipeSessionId = chosenPipeSession || runningPipeRun?.id || latestPipeTurn?.parentSession || latestPipeAgent?.parentSession || pipeRunsRaw[0]?.id || ''
  const activePipeRunRaw = pipeRunsRaw.find((run) => run.id === activePipeSessionId)
  const activePipeSessionAgents = activePipeRunRaw?.steps || []
  const runningInPipeSession = activePipeSessionAgents.find((a) => a.status === 'running')
  const currentPipeSessionAgent = runningInPipeSession || activePipeSessionAgents[activePipeSessionAgents.length - 1]
  const activePipeRunStatus = activePipeRunRaw ? statusForRun(activePipeRunRaw) : 'abandoned'
  // Always list EVERY session (newest first); the main panel focuses the
  // active/chosen one. Chronological run number: oldest is R01, newest highest.
  const runNumberById = new Map()
  runsRaw.forEach((run, i) => runNumberById.set(run.id, runsRaw.length - i))
  const runs = runsRaw.map((run) => {
    const status = statusForRun(run)
    const m = sm[status] || sm.abandoned
    const current = run.steps.find((a) => a.status === 'running') || run.steps[run.steps.length - 1]
    const selected = run.id === activeSessionId
    return {
      id: run.id,
      title: 'Session ' + String(run.id || 'driver').slice(0, 12),
      subtitle: run.steps.length ? run.steps.length + ' workers' : 'driver-only turn',
      workerCount: run.steps.length,
      status,
      statusLabel: m.label,
      statusColor: m.color,
      currentBrief: current?.brief || run.task || 'Driver handled this turn without spawning workers.',
      orderLabel: 'R' + String(runNumberById.get(run.id) || 1).padStart(2, '0'),
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + m.color + ';' + (status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      cardStyle: 'width:100%;text-align:left;background:' + (selected ? '#1b2536' : '#111721') + ';border:1px solid ' + (selected ? '#ff9b3d' : 'rgba(255,255,255,0.08)') + ';border-radius:10px;padding:11px 12px;cursor:pointer;transition:border-color .15s,box-shadow .15s;' + (selected ? 'box-shadow:0 0 0 1px #ff9b3d, 0 0 18px rgba(255,155,61,0.16);' : ''),
      onSelect: () => act.selectSession(run.id),
    }
  })
  const pipeRunNumberById = new Map()
  pipeRunsRaw.forEach((run, i) => pipeRunNumberById.set(run.id, pipeRunsRaw.length - i))
  const pipeRuns = pipeRunsRaw.map((run) => {
    const status = statusForRun(run)
    const m = sm[status] || sm.abandoned
    const current = run.steps.find((a) => a.status === 'running') || run.steps[run.steps.length - 1]
    const selected = run.id === activePipeSessionId
    return {
      id: run.id,
      orderLabel: 'R' + String(pipeRunNumberById.get(run.id) || 1).padStart(2, '0'),
      title: 'Session ' + String(run.id || 'driver').slice(0, 12),
      subtitle: run.steps.length ? run.steps.length + ' workers' : 'driver-only turn',
      workerCount: run.steps.length,
      status,
      statusLabel: m.label,
      statusColor: m.color,
      currentBrief: current?.brief || run.task || 'Pipeline driver handled this turn without spawning workers.',
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + m.color + ';' + (status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      cardStyle: 'width:100%;text-align:left;background:' + (selected ? '#1b2536' : '#111721') + ';border:1px solid ' + (selected ? '#ff9b3d' : 'rgba(255,255,255,0.08)') + ';border-radius:10px;padding:10px 11px;cursor:pointer;transition:border-color .15s,box-shadow .15s;' + (selected ? 'box-shadow:0 0 0 1px #ff9b3d, 0 0 16px rgba(255,155,61,0.14);' : ''),
      onSelect: () => act.selectPipeSession(run.id),
    }
  })
  const slots = [{ cx: 189, cy: 300 }, { cx: 500, cy: 300 }, { cx: 811, cy: 300 }]
  const subagents = orchSubagents.slice(-3).map((a, i) => {
    const m = sm[a.status]
    const hue = hueFor(a.role)
    const sel = a.handle === s.selected
    const sl = slots[i] || slots[2]
    const cardStyle = 'position:absolute;left:' + sl.cx + 'px;top:' + sl.cy + 'px;transform:translate(-50%,0);width:218px;z-index:2;background:#19212f;border:1px solid ' + (sel ? '#ff9b3d' : (a.status === 'running' ? hue + '55' : 'rgba(255,255,255,0.08)')) + ';border-radius:12px;padding:14px 15px;cursor:pointer;transition:border-color .15s, box-shadow .15s;' + (sel ? 'box-shadow:0 0 0 1px #ff9b3d, 0 0 26px rgba(255,155,61,0.14);' : 'box-shadow:0 8px 24px rgba(0,0,0,0.4);')
    const pct = Math.round(a.turns / a.max * 100)
    return {
      role: a.role, handle: a.handle, brief: a.brief, statusLabel: m.label, statusColor: m.color,
      model: a.model, profile: a.profile, modelColor: modelColorFor(a.role),
      orderLabel: 'W' + String(workerOrder.get(a.handle) || i + 1).padStart(2, '0'),
      auditId: '#' + a.id,
      parentLabel: a.parent || 'driver',
      orderBadge: 'display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:18px;padding:0 5px;border-radius:5px;font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#cfcfd4;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12)',
      roleChipStyle: roleChip(a.role, hue), cardStyle,
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + m.color + ';' + (a.status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      barFillStyle: 'height:100%;width:' + pct + '%;background:' + m.color + ';border-radius:2px;transition:width .4s ease',
      turnsLabel: a.turns + '/' + a.max, toolCount: a.tools.length,
      wallLabel: a.status === 'running' ? fmtClock(a.wall) : '—',
      onSelect: () => act.selectAgent(a.handle),
    }
  })

  const roleTotals = activeSessionAgents.reduce((acc, agent) => {
    const role = agent.role || 'worker'
    acc.set(role, (acc.get(role) || 0) + 1)
    return acc
  }, new Map())
  const roleSeen = new Map()
  const sessionSteps = activeSessionAgents.map((a, i) => {
    const m = sm[a.status] || sm.abandoned
    const hue = hueFor(a.role)
    const isCurrent = a.handle === currentSessionAgent?.handle
    const stopHint = stopHintFor(a, processTextForRuns)
    const pct = a.max ? Math.min(100, Math.round(a.turns / a.max * 100)) : 0
    const role = a.role || 'worker'
    const totalAttempts = roleTotals.get(role) || 1
    const attempt = (roleSeen.get(role) || 0) + 1
    roleSeen.set(role, attempt)
    return {
      handle: a.handle,
      role: a.role,
      brief: a.brief,
      status: a.status,
      statusLabel: m.label,
      statusColor: m.color,
      isCurrent,
      stopHint,
      attemptLabel: totalAttempts > 1 ? 'attempt ' + attempt + '/' + totalAttempts : '',
      orderLabel: 'W' + String(workerOrder.get(a.handle) || i + 1).padStart(2, '0'),
      roleChipStyle: roleChip(a.role, hue),
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + m.color + ';' + (a.status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      barFillStyle: 'height:100%;width:' + pct + '%;background:' + m.color + ';border-radius:3px;transition:width .4s ease',
      cardStyle: 'position:relative;width:218px;flex-shrink:0;background:#141b27;border:1px solid ' + (isCurrent ? m.color : 'rgba(255,255,255,0.08)') + ';border-radius:10px;padding:13px 14px;cursor:pointer;' + (isCurrent ? 'box-shadow:0 0 0 1px ' + m.color + '55,0 0 24px ' + m.color + '22;' : ''),
      turnsLabel: a.turns + '/' + a.max,
      toolsLabel: a.tools.length + ' tools',
      showConnector: i < activeSessionAgents.length - 1,
      onSelect: () => act.selectAgent(a.handle),
    }
  })

  const pipeRoleTotals = activePipeSessionAgents.reduce((acc, agent) => {
    const role = agent.role || 'worker'
    acc.set(role, (acc.get(role) || 0) + 1)
    return acc
  }, new Map())
  const pipeRoleSeen = new Map()
  const pipeRunSteps = activePipeSessionAgents.map((a, i) => {
    const m = sm[a.status] || sm.abandoned
    const hue = hueFor(a.role)
    const isCurrent = a.handle === currentPipeSessionAgent?.handle
    const stopHint = stopHintFor(a, processTextForPipeRuns)
    const pct = a.max ? Math.min(100, Math.round(a.turns / a.max * 100)) : 0
    const role = a.role || 'worker'
    const totalAttempts = pipeRoleTotals.get(role) || 1
    const attempt = (pipeRoleSeen.get(role) || 0) + 1
    pipeRoleSeen.set(role, attempt)
    return {
      handle: a.handle,
      role: a.role,
      brief: a.brief,
      status: a.status,
      statusLabel: m.label,
      statusColor: m.color,
      isCurrent,
      stopHint,
      attemptLabel: totalAttempts > 1 ? 'attempt ' + attempt + '/' + totalAttempts : '',
      orderLabel: 'W' + String(pipeWorkerOrder.get(a.handle) || i + 1).padStart(2, '0'),
      roleChipStyle: roleChip(a.role, hue),
      dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + m.color + ';' + (a.status === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
      barFillStyle: 'height:100%;width:' + pct + '%;background:' + m.color + ';border-radius:3px;transition:width .4s ease',
      cardStyle: 'position:relative;width:210px;flex-shrink:0;background:#141b27;border:1px solid ' + (isCurrent ? m.color : 'rgba(255,255,255,0.08)') + ';border-radius:10px;padding:12px 13px;' + (isCurrent ? 'box-shadow:0 0 0 1px ' + m.color + '55,0 0 22px ' + m.color + '22;' : ''),
      turnsLabel: a.turns + '/' + a.max,
      toolsLabel: a.tools.length + ' tools',
      showConnector: i < activePipeSessionAgents.length - 1,
    }
  })

  const selAgent = orchSubagents.find((a) => a.handle === s.selected)
  let detail = null
  if (selAgent) {
    const m = sm[selAgent.status]
    const fw = selAgent.role === 'reviewer-aux'
    detail = {
      role: selAgent.role, handle: selAgent.handle, brief: selAgent.brief, parent: selAgent.parent,
      auditId: '#' + selAgent.id,
      workerLabel: 'W' + String(workerOrder.get(selAgent.handle) || 1).padStart(2, '0'),
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

  const profilePalette = {
    anthropic: '#ff9b3d',
    deepseek: '#86c7c0',
    openai: '#9ed8b4',
    ollama: '#8ab4d8',
    azure: '#d8b48a',
  }
  const runtimeProfiles = Array.isArray(s.profiles) && s.profiles.length ? s.profiles : profileDefs
  const profileList = runtimeProfiles.map((p) => ({
    ...p,
    fc: p.fc || profilePalette[p.family] || '#8a8a92',
    model: p.model || p.name,
    transport: p.transport || 'profile',
    endpoint: p.endpoint || '',
    keyEnv: p.keyEnv || '',
  }))
  const activeDef = profileList.find((p) => p.name === s.activeProfile) || {
    name: s.activeProfile || 'unconfigured',
    family: (s.activeProfile || 'custom').split('.')[0],
    model: s.activeProfile || 'unconfigured',
    transport: 'profile',
    endpoint: '',
    keyEnv: '',
    fc: '#ff9b3d',
  }
  const driverSummary = {
    title: 'Run summary',
    countLine: statusCountLine(activeSessionAgents),
    focusLine: focusLineForRun(activeSessionAgents, currentSessionAgent),
    alertLine: runSummary(activeRunStatus, processTextForRuns, activeRunRaw),
    metaLine: (activeDef.model || 'unconfigured') + ' - ' + (s.activeProfile || 'no profile'),
    replayLine: replayLineForRun(activeRunRaw),
  }
  const pipeRunSummary = {
    title: 'Studio run',
    countLine: statusCountLine(activePipeSessionAgents),
    focusLine: focusLineForRun(activePipeSessionAgents, currentPipeSessionAgent),
    alertLine: runSummary(activePipeRunStatus, processTextForPipeRuns, activePipeRunRaw),
    metaLine: (activeDef.model || 'unconfigured') + ' - ' + (s.activeProfile || 'no profile'),
    replayLine: replayLineForRun(activePipeRunRaw),
  }
  const profiles = profileList.map((p) => {
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

  const skills = skillDefs.map((sk) => ({
    name: sk.name, appliesTo: sk.appliesTo, desc: sk.desc, mode: sk.mode,
    modeStyle: sk.mode === 'PUSHED'
      ? 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#ff9b3d;background:rgba(255,155,61,0.1);border:1px solid rgba(255,155,61,0.3);padding:2px 8px;border-radius:5px'
      : 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;font-weight:600;color:#8ab4d8;background:rgba(138,180,216,0.1);border:1px solid rgba(138,180,216,0.3);padding:2px 8px;border-radius:5px',
  }))

  // ── pipeline studio view-model ──
  // The studio is a preset composer / inspector; pipelines are launched by
  // asking the driver in chat (root agent → musubi_spawn_pipeline), reusing the
  // Orchestrator session input, so there is no in-studio Run control.
  const stColor = { idle: '#6a6a72', queued: '#e3b341', running: '#ff9b3d', done: '#54c79a' }
  const editable = true
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
    cardStyle: 'text-align:left;width:100%;background:#141b27;border:1px solid rgba(255,255,255,0.07);border-radius:10px;padding:11px 13px;cursor:pointer;transition:border-color .14s',
    onAdd: () => act.addPipe(c.role),
  }))
  const pipePresetsVM = pipePresets.map((p) => ({
    name: p.name, countLabel: p.roles.length + ' agents',
    selected: !!s.pipeName && s.pipeName === p.name,
    btnStyle: 'display:flex;align-items:center;justify-content:space-between;width:100%;font-family:\'IBM Plex Mono\',monospace;font-size:11px;padding:8px 11px;border-radius:8px;cursor:pointer;background:' + (s.pipeName === p.name ? 'rgba(255,155,61,0.1)' : '#19212f') + ';border:1px solid ' + (s.pipeName === p.name ? 'rgba(255,155,61,0.4)' : 'rgba(255,255,255,0.08)') + ';color:' + (s.pipeName === p.name ? '#ff9b3d' : '#9b9ba2'),
    onLoad: () => act.loadPreset(p.name),
  }))
  const pipeStatusText = 'compose the ' + s.pipeName + ' recipe · run it by asking the driver in chat; stage workers appear in the Orchestrator & Audit'

  const pipeNameLabel = s.pipeName || 'choose preset'
  const pipeStatusTextForDisplay = s.pipeName
    ? 'compose the ' + s.pipeName + ' recipe - run it by asking the driver in chat; stage workers appear in the Orchestrator & Audit'
    : 'Choose a pipeline preset before running. Bare "pipeline" opens this picker and does not call the model.'

  const chatView = s.chat.map((msg) => {
    if (msg.role === 'you') {
      return {
        text: msg.text, formatted: false, showMeta: false, meta: '', metaStyle: '',
        rowStyle: 'display:flex;justify-content:flex-end;padding:4px 16px',
        bubbleStyle: 'max-width:82%;background:rgba(255,155,61,0.14);border:1px solid rgba(255,155,61,0.32);color:#fde9d6;padding:8px 12px;border-radius:13px 13px 4px 13px;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere',
      }
    }
    if (msg.role === 'driver') {
      return {
        text: msg.text, formatted: true, showMeta: true, meta: 'driver · the knot · ' + formatChatTimestamp(msg.ts),
        metaStyle: 'font-size:9.5px;color:#6a6a72;font-family:\'IBM Plex Mono\',monospace;padding-left:3px',
        rowStyle: 'display:flex;flex-direction:column;align-items:flex-start;gap:3px;padding:4px 16px',
        bubbleStyle: 'max-width:86%;background:#19212f;border:1px solid rgba(255,255,255,0.07);color:#d4d4d8;padding:8px 12px;border-radius:13px 13px 13px 4px;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere',
      }
    }
    const red = msg.tone === 'deny'
    return {
      text: msg.text, formatted: false, showMeta: false, meta: '', metaStyle: '',
      rowStyle: 'display:flex;justify-content:center;padding:5px 16px',
      bubbleStyle: 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:' + (red ? '#e86a5f' : '#7a7a82') + ';background:' + (red ? 'rgba(232,106,95,0.08)' : 'rgba(255,255,255,0.03)') + ';border:1px solid ' + (red ? 'rgba(232,106,95,0.25)' : 'rgba(255,255,255,0.07)') + ';padding:4px 11px;border-radius:20px;letter-spacing:0.02em;text-align:center',
    }
  })

  const pipeChatView = buildChatView(s.pipeChat || [])

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
  const driverStatus = s.driverStatus || {}
  const statusTail = (driverStatus.stderrTail || driverStatus.stdoutTail || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const driverStatusText = statusTail.length
    ? statusTail[statusTail.length - 1]
    : (driverStatus.running ? 'working...' : '')
  const driverProcessLog = [
    driverStatus.stderrTail ? 'stderr:\n' + driverStatus.stderrTail.trim() : '',
    driverStatus.stdoutTail ? 'stdout:\n' + driverStatus.stdoutTail.trim() : '',
  ].filter(Boolean).join('\n\n')
  const activeSurfaceLabel = driverSurface === 'pipeline' ? 'Pipeline' : 'Orchestrator'
  const orchestratorBlockedByPipeline = driverRunning && !orchestratorOwnsDriver
  const pipelineBlockedByOrchestrator = driverRunning && !pipelineOwnsDriver
  const pipeChatBody = {
    chat: pipeChatView,
    draft: s.pipeDraft || '',
    onDraft: act.onPipeDraft,
    onDraftKey: act.onPipeDraftKey,
    driverBusy: pipelineOwnsDriver,
    driverTask: pipelineOwnsDriver ? (driverStatus.task || '') : '',
    driverStatusText: pipelineBlockedByOrchestrator ? `${activeSurfaceLabel} run is active.` : driverStatusText,
    driverProcessOpen: pipelineOwnsDriver && !!s.processOpen,
    driverProcessLog: pipelineOwnsDriver ? driverProcessLog : '',
    onToggleProcess: act.toggleProcess,
    logWindowOpen: pipelineOwnsDriver && !!s.logWindowOpen,
    onOpenLog: act.openProcessLog,
    onCloseLog: act.closeProcessLog,
    onClearDriverChat: act.clearPipeDriverChat,
    onNewSession: act.newPipeSession,
    clearDriverDisabled: !!driverStatus.running,
    onSend: pipelineOwnsDriver ? act.cancelAgent : act.sendPipeChat,
    sendTitle: pipelineBlockedByOrchestrator ? `${activeSurfaceLabel} run is active` : (pipelineOwnsDriver ? 'Cancel running pipeline agent' : 'Send'),
    sendMode: pipelineOwnsDriver ? 'cancel' : 'send',
    sendDisabled: pipelineBlockedByOrchestrator,
    inputDisabled: pipelineBlockedByOrchestrator,
    disabledText: pipelineBlockedByOrchestrator ? `${activeSurfaceLabel} run is active...` : '',
    onOpenArtifact: (path) => act.openArtifact(path, 'pipeline'),
  }

  return {
    isOrch: s.view === 'orchestrator', isPipeline: s.view === 'pipeline', isPolicy: s.view === 'policy', isAudit: s.view === 'audit', isModels: s.view === 'models', isSkills: s.view === 'skills', isSettings: s.view === 'settings',
    view: s.view,
    runtimeSourceLabel: sourceLabels[s.runtimeSource] || 'audit.db',
    orchNav: navStyle(s.view === 'orchestrator'), pipeNav: navStyle(s.view === 'pipeline'), polNav: navStyle(s.view === 'policy'), audNav: navStyle(s.view === 'audit'), modNav: navStyle(s.view === 'models'), sklNav: navStyle(s.view === 'skills'), settingsNav: navStyle(s.view === 'settings'),
    selOrch: () => act.setView('orchestrator'), selPipe: () => act.setView('pipeline'), selPolicy: () => act.setView('policy'), selAudit: () => act.setView('audit'), selModels: () => act.setView('models'), selSkills: () => act.setView('skills'), selSettings: () => act.setView('settings'),
    pipeStepsView: pipeStepsVM, pipeCatalog: pipeCatalogVM, pipePresets: pipePresetsVM, pipeName: pipeNameLabel, pipeEmpty: s.pipeSteps.length === 0, pipeHasSteps: s.pipeSteps.length > 0, pipeStatusText: pipeStatusTextForDisplay, onClearPipe: () => act.clearPipe(),
    pipeChatOpen: s.pipeChatOpen, openPipeChat: () => act.openPipeChat(), closePipeChat: () => act.closePipeChat(),
    pipeDriverStyle: 'width:144px;flex-shrink:0;align-self:center;background:#19212f;border:1px solid ' + (s.pipeChatOpen ? '#ff9b3d' : 'rgba(255,155,61,0.4)') + ';border-radius:12px;padding:14px;text-align:center;cursor:pointer;transition:border-color .15s;' + (s.pipeChatOpen ? 'box-shadow:0 0 0 1px #ff9b3d, 0 0 22px rgba(255,155,61,0.14);' : ''),
    activeModel: activeDef.model, activeProfileName: s.activeProfile,
    runningCount: orchSubagents.filter((a) => a.status === 'running').length,
    totalDone: orchSubagents.filter((a) => a.status === 'done').length,
    totalSpawned: orchSubagents.length,
    driverCycle: s.t || 0,
    driverStyle: 'position:absolute;left:500px;top:0;transform:translate(-50%,0);z-index:3;background:#19212f;border:1px solid rgba(255,155,61,0.4);border-radius:14px;padding:16px 24px;min-width:296px;text-align:center;animation:glow 3s ease-in-out infinite;',
    driverDotStyle: 'width:8px;height:8px;border-radius:50%;background:#ff9b3d;animation:pulse 1.6s ease-in-out infinite;',
    subagents: [], webShown: [],
    runs,
    activeRunId: activeSessionId,
    activeRunSteps: sessionSteps,
    selectedStepDetail: detail,
    driverSummary,
    runStatusSummary: runSummary(activeRunStatus, processTextForRuns, activeRunRaw),
    sessionSteps,
    sessionTitle: activeSessionId ? ('Session ' + activeSessionId.slice(0, 12)) : 'Session history',
    sessionSubtitle: sessionSteps.length
      ? (sessionSteps.length + ' workers · full history for this parent run')
      : (activeRunRaw?.turn ? 'driver-only turn - no workers spawned' : 'no workers in this session yet'),
    hasDetail: !!detail, showFeed: !detail, detail, clearSelect: () => act.clearSelect(),
    driverBusy: orchestratorOwnsDriver, driverTask: orchestratorOwnsDriver ? (driverStatus.task || '') : '', driverStatusText: orchestratorBlockedByPipeline ? `${activeSurfaceLabel} run is active.` : driverStatusText,
    driverProcessOpen: orchestratorOwnsDriver && !!s.processOpen, driverProcessLog: orchestratorOwnsDriver ? driverProcessLog : '', onToggleProcess: act.toggleProcess,
    logWindowOpen: orchestratorOwnsDriver && !!s.logWindowOpen, onOpenLog: act.openProcessLog, onCloseLog: act.closeProcessLog,
    onClearDriverChat: act.clearDriverChat,
    onNewSession: act.newSession,
    clearDriverDisabled: !!driverStatus.running,
    events: s.events, chat: chatView, draft: s.draft, onDraft: act.onDraft, onDraftKey: act.onDraftKey,
    onSend: orchestratorOwnsDriver ? act.cancelAgent : act.sendChat,
    sendTitle: orchestratorBlockedByPipeline ? `${activeSurfaceLabel} run is active` : (orchestratorOwnsDriver ? 'Cancel running agent' : 'Send'),
    sendMode: orchestratorOwnsDriver ? 'cancel' : 'send',
    sendDisabled: orchestratorBlockedByPipeline,
    inputDisabled: orchestratorBlockedByPipeline,
    disabledText: orchestratorBlockedByPipeline ? `${activeSurfaceLabel} run is active...` : '',
    onOpenArtifact: (path) => act.openArtifact(path, 'orchestrator'),
    pipeRuns,
    activePipeRunId: activePipeSessionId,
    activePipeRunSteps: pipeRunSteps,
    pipeRunSummary,
    pipeSessionTitle: activePipeSessionId ? ('Session ' + activePipeSessionId.slice(0, 12)) : 'Pipeline run history',
    pipeSessionSubtitle: pipeRunSteps.length
      ? (pipeRunSteps.length + ' workers in this pipeline session')
      : (activePipeRunRaw?.turn ? 'driver-only turn - no workers spawned' : 'no pipeline workers in this session yet'),
    pipeChat: pipeChatView,
    pipeChatBody,
    policy, policyRoles, allowCount: s.allowCount, denyCount: s.denyCount,
    auditView, auditCountLabel: auditView.length + ' rows · immutable',
    setAuditAll: () => act.setAuditFilter('all'), setAuditSpawn: () => act.setAuditFilter('spawned'), setAuditDone: () => act.setAuditFilter('completed'),
    auditFAll: auditBtn(s.auditFilter === 'all'), auditFSpawn: auditBtn(s.auditFilter === 'spawned'), auditFDone: auditBtn(s.auditFilter === 'completed'),
    profiles, skills, setupRows, setupPathHint: setup.pathHint || '',
  }
}
