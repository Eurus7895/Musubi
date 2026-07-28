// Pure presentation layer. Maps a domain state object + an actions object into
// the flat view-model the React views render. Colours are derived from
// role/status here, so the backend only needs to supply domain fields.
import {
  statusMeta, policyRoleDefs, profileDefs, skillDefs,
  hueFor, modelColorFor,
} from './data.js'
import { roleChip, navStyle, auditBtn } from './styleHelpers.js'
import { fmtClock } from './format.js'
import { createPipelineDraft, isDirty } from './pipelineBuilder.js'
import { approvalScope, pendingApproval } from './approvalRequest.js'

function statusForRun(run) {
  const steps = run.steps || []
  if (run.live) return 'running'
  if (run.status === 'success') return 'done'
  if (run.status === 'aborted') return 'failed'
  if (run.status === 'escalated') return 'escalated'
  if (run.status === 'budget_halted') return 'budget_halted'
  if (run.status === 'running') return 'running'
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

function belongsToSurface(item, surface, currentChatId = '') {
  const chatId = item?.chatId || item?.chat_id || ''
  if (currentChatId) return chatId === currentChatId
  return surface === 'pipeline' ? isPipelineChatId(chatId) : !isPipelineChatId(chatId)
}

function driverBelongsToSession(status, surface, currentChatId) {
  return status?.surface === surface
    && !!currentChatId
    && status?.chatId === currentChatId
}

function groupRuns(subagents, agentTurns = [], driverStatus = {}, surface = 'orchestrator', currentChatId = '') {
  const byId = new Map()
  // Track each run's real recency (max member epoch) so worker sessions
  // (subagent_audit) and driver-only turns (agent_turns) — which arrive as two
  // separate lists — sort by actual time, not by load order. lastIndex is the
  // stable fallback when epochs are missing/equal.
  const bump = (run, epoch) => {
    const n = Number(epoch)
    if (Number.isFinite(n) && n > run.recency) run.recency = n
  }
  agentTurns.filter((turn) => belongsToSurface(turn, surface, currentChatId)).forEach((turn, index) => {
    const id = turn.parentSession || `driver-turn-${turn.id || index + 1}`
    if (!byId.has(id)) byId.set(id, { id, lastIndex: index, steps: [], recency: 0 })
    const run = byId.get(id)
    run.turn = turn
    run.lastIndex = Math.max(run.lastIndex, index)
    bump(run, turn.startedAt)
  })
  subagents.filter((agent) => belongsToSurface(agent, surface, currentChatId)).forEach((agent, index) => {
    const id = agent.parentSession || agent.parent || 'driver'
    if (!byId.has(id)) byId.set(id, { id, lastIndex: index, steps: [], recency: 0 })
    const run = byId.get(id)
    run.lastIndex = Math.max(run.lastIndex, agentTurns.length + index)
    run.steps.push(agent)
    bump(run, agent.spawnEpoch)
  })
  if (driverStatus?.running && driverBelongsToSession(driverStatus, surface, currentChatId) && !Array.from(byId.values()).some((run) => statusForRun(run) === 'running')) {
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

function groupOrchestratorSessions(sessions, subagents, agentTurns, driverStatus) {
  return sessions.map((session, index) => {
    const chatId = session.chatId || ''
    const turns = agentTurns
      .filter((turn) => turn.chatId === chatId)
      .sort((a, b) => Number(a.startedAt || 0) - Number(b.startedAt || 0))
    const live = !!driverStatus?.running
      && driverStatus.surface === 'orchestrator'
      && driverStatus.chatId === chatId
    const sessionWorkers = subagents.filter((agent) => agent.chatId === chatId)
    const latestTurn = turns[turns.length - 1]
    const liveParent = live
      ? sessionWorkers.find((agent) => agent.status === 'running')?.parentSession
        || sessionWorkers[sessionWorkers.length - 1]?.parentSession
        || `live-${driverStatus.startedAt || 'root'}`
      : ''
    const rootTurn = live
      ? {
          parentSession: liveParent,
          request: driverStatus.task || session.lastRequest || session.title || 'Running root task',
          startedAt: driverStatus.startedAt,
          cycles: 0,
          tokensInEstimate: 0,
          tokensOutEstimate: 0,
          live: true,
        }
      : latestTurn
    const steps = rootTurn?.parentSession
      ? sessionWorkers.filter((agent) => agent.parentSession === rootTurn.parentSession)
      : []
    return {
      id: chatId,
      session,
      turn: rootTurn,
      rootTurn,
      steps,
      live,
      task: rootTurn?.request || session.lastRequest || session.title || '',
      recency: live ? Number.MAX_SAFE_INTEGER : Number(rootTurn?.startedAt || 0),
      lastIndex: sessions.length - index,
    }
  })
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

// Takes the set of parent-session ids belonging to one conversation, not a
// single id. A chat accumulates one parent session per root turn, so scoping
// to the latest one and calling the result "this session" omitted every
// earlier request's tokens, tools, and latency.
function economicsForSession(agentCycles, sessionIds) {
  const wanted = sessionIds instanceof Set ? sessionIds : new Set([sessionIds].filter(Boolean))
  const rows = (agentCycles || []).filter((row) => wanted.has(row.sessionId))
  const tools = new Map()
  const summary = {
    cycles: rows.length,
    inputTokens: 0,
    cachedInputTokens: 0,
    outputTokens: 0,
    lmMs: 0,
    tokenSource: rows.length && rows.every((row) => row.tokenSource === 'provider')
      ? 'provider'
      : 'estimated',
    tools: [],
  }
  rows.forEach((row) => {
    const input = Math.max(0, Number(row.tokensIn) || 0)
    summary.inputTokens += input
    summary.cachedInputTokens += Math.max(0, Math.min(input, Number(row.cachedInputTokens) || 0))
    summary.outputTokens += Math.max(0, Number(row.tokensOut) || 0)
    summary.lmMs += Math.max(0, Number(row.lmMs) || 0)
    ;(row.toolNames || []).forEach((name) => {
      if (name) tools.set(name, (tools.get(name) || 0) + 1)
    })
  })
  summary.tools = Array.from(tools, ([name, count]) => ({ name, count }))
  return summary
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

// "1 workers" had no plural handling anywhere in the console.
function plural(count, noun) {
  const n = Number(count) || 0
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

// The rail groups sessions by what the operator would do about them, not by
// recency alone: one is running, one is blocked on you, the rest are history.
const RAIL_BUCKETS = [
  { key: 'active', label: 'Active' },
  { key: 'needsYou', label: 'Needs you' },
  { key: 'earlier', label: 'Earlier' },
]

function railBucketFor(status) {
  if (status === 'running') return 'active'
  if (status === 'escalated' || status === 'failed' || status === 'budget_halted') return 'needsYou'
  return 'earlier'
}

// "Newest first" was asserted in a subtitle while no card carried a time, so
// duplicate titles were indistinguishable. Every card gets a clock now.
function clockLabel(epochSeconds, now = Date.now()) {
  const seconds = Number(epochSeconds)
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const at = new Date(seconds * 1000)
  const sameDay = new Date(now).toDateString() === at.toDateString()
  const time = String(at.getHours()).padStart(2, '0') + ':' + String(at.getMinutes()).padStart(2, '0')
  return sameDay ? time : `${at.getMonth() + 1}/${at.getDate()} ${time}`
}

// The banner names the act in language, not in tool-call syntax. The exact
// call still appears below it in mono; this line is what you read at a glance.
function actPhrase(row) {
  const message = String(row?.message || '').toLowerCase()
  const category = String(row?.category || '').toLowerCase()
  if (category === 'policy') return 'waiting on a policy check'
  if (category === 'model') return 'thinking'
  if (category === 'skills') return 'loading a skill'
  if (/\b(glob|grep|list_dir|read_file|read)\b/.test(message)) return 'reading your workspace'
  if (/\b(write|edit|patch|create)\b/.test(message)) return 'writing files'
  if (/\b(spawn|summon)\w*/.test(message)) return 'summoning workers'
  if (/\b(test|lint|typecheck|validate)\w*/.test(message)) return 'running checks'
  if (category === 'tools') return 'using a tool'
  return 'working'
}

// Coarse ages only — a finished row does not need second precision.
function ageLabel(epochSeconds, now = Date.now()) {
  const seconds = Number(epochSeconds)
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const delta = Math.max(0, Math.round(now / 1000 - seconds))
  if (delta < 60) return 'just now'
  if (delta < 3600) return Math.floor(delta / 60) + 'm ago'
  if (delta < 86400) return Math.floor(delta / 3600) + 'h ago'
  return Math.floor(delta / 86400) + 'd ago'
}

function clipEvidence(value, max = 240) {
  const text = String(value || '')
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
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
        role: msg.role,
        text: msg.text, formatted: false, showMeta: false, meta: '', metaStyle: '',
        rowStyle: 'display:flex;justify-content:flex-end;padding:4px 16px',
        bubbleStyle: 'max-width:82%;background:rgba(255,155,61,0.14);border:1px solid rgba(255,155,61,0.32);color:#fde9d6;padding:8px 12px;border-radius:13px 13px 4px 13px;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere',
      }
    }
    if (msg.role === 'driver') {
      return {
        role: msg.role,
        text: msg.text, formatted: true, showMeta: true, meta: 'driver · the knot · ' + formatChatTimestamp(msg.ts),
        metaStyle: 'font-size:9.5px;color:#6a6a72;font-family:\'IBM Plex Mono\',monospace;padding-left:3px',
        rowStyle: 'display:flex;flex-direction:column;align-items:flex-start;gap:3px;padding:4px 16px',
        bubbleStyle: 'max-width:86%;background:#19212f;border:1px solid rgba(255,255,255,0.07);color:#d4d4d8;padding:8px 12px;border-radius:13px 13px 13px 4px;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere',
      }
    }
    const red = msg.tone === 'deny'
    return {
      role: msg.role,
      text: msg.text, formatted: false, showMeta: false, meta: '', metaStyle: '',
      rowStyle: 'display:flex;justify-content:center;padding:5px 16px',
      bubbleStyle: 'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:' + (red ? '#e86a5f' : '#7a7a82') + ';background:' + (red ? 'rgba(232,106,95,0.08)' : 'rgba(255,255,255,0.03)') + ';border:1px solid ' + (red ? 'rgba(232,106,95,0.25)' : 'rgba(255,255,255,0.07)') + ';padding:4px 11px;border-radius:20px;letter-spacing:0.02em;text-align:center',
    }
  })
}

export function buildViewModel(s, act) {
  const pipelineBuilderState = s.pipelineBuilder || {
    step: 'catalog', draft: createPipelineDraft(), savedRecipe: createPipelineDraft(),
    selectedStageIndex: null, findings: [], saveResult: null,
    loading: false, pendingTransition: null,
  }
  const pipelineOptions = (s.pipelineCatalog || []).map((entry) => ({
    name: entry.name,
    description: entry.description || '',
    stages: entry.stages || [],
    runnable: !!entry.runnable,
    blockedReason: entry.blockedReason || '',
    selected: entry.name === s.selectedPipeline,
    onSelect: () => act.selectPipeline?.(entry.name),
  }))
  const builderCatalog = s.pipelineBuilderCatalog || { presets: [], agents: [] }
  const libraryQuery = String(
    pipelineBuilderState.libraryQuery
      ?? pipelineBuilderState.librarySearch
      ?? pipelineBuilderState.search
      ?? '',
  ).trim().toLowerCase()
  const searchable = (item, fields) => !libraryQuery || fields
    .some((field) => String(item?.[field] || '').toLowerCase().includes(libraryQuery))
  const by = (field) => (a, b) => String(a?.[field] || '').localeCompare(String(b?.[field] || ''))
  const libraryPresets = (builderCatalog.presets || [])
    .filter((item) => searchable(item, ['id', 'description', 'agent', 'stage', 'blockedReason']))
    .map((item) => ({ ...item, blocked: !item.runnable }))
    .sort(by('id'))
  const allLibraryAgents = (builderCatalog.agents || [])
    .map((item) => ({ ...item, blocked: !item.runnable }))
    .sort(by('name'))
  const libraryAgents = allLibraryAgents
    .filter((item) => searchable(item, ['name', 'displayLabel', 'blockedReason']))
  const spawnRoleNames = new Set(
    (builderCatalog.agents || [])
      .filter((item) => item.runnable)
      .flatMap((item) => item.spawnAllowlist || []),
  )
  const librarySpawnRoles = allLibraryAgents
    .filter((item) => item.runnable && spawnRoleNames.has(item.name))
    .filter((item) => searchable(item, ['name', 'displayLabel']))
  const selectedPipelineEntry = pipelineOptions.find((entry) => entry.selected)
  const orchestratorPipelineBlocked = s.runMode === 'pipeline' && !selectedPipelineEntry?.runnable
  const sm = statusMeta
  const allSubagents = s.subagents || []
  const allAgentTurns = s.agentTurns || []
  const orchestratorChatId = s.orchestratorChatId || ''
  const orchestratorSessions = s.orchestratorSessions || []
  const hasSessionIndex = orchestratorSessions.length > 0
  const orchSubagents = allSubagents.filter((a) => belongsToSurface(a, 'orchestrator', hasSessionIndex ? '' : orchestratorChatId))
  const orchAgentTurns = allAgentTurns.filter((t) => belongsToSurface(t, 'orchestrator', hasSessionIndex ? '' : orchestratorChatId))
  const workerOrder = new Map(orchSubagents.map((a, i) => [a.handle, i + 1]))
  const selectedAgent = orchSubagents.find((a) => a.handle === s.selected)
  const latestAgent = orchSubagents[orchSubagents.length - 1]
  const latestTurn = orchAgentTurns[orchAgentTurns.length - 1]
  const driverStatusForRuns = s.driverStatus || {}
  const driverSurface = driverStatusForRuns.surface || 'orchestrator'
  const driverRunning = !!driverStatusForRuns.running
  const driverBelongsToOrchestrator = driverBelongsToSession(driverStatusForRuns, 'orchestrator', orchestratorChatId)
  const orchestratorOwnsDriver = driverRunning && driverBelongsToOrchestrator
  const viewingHistoricalSession = Boolean(
    s.selectedSession
    && orchestratorChatId
    && s.selectedSession !== orchestratorChatId
  )
  const historicalSessionBlocked = viewingHistoricalSession && driverRunning
  const historicalDisabledText = historicalSessionBlocked
    ? 'Viewing historical session (read-only) while another run is active.'
    : ''
  const runsRaw = hasSessionIndex
    ? groupOrchestratorSessions(orchestratorSessions, orchSubagents, orchAgentTurns, driverStatusForRuns)
    : groupRuns(orchSubagents, orchAgentTurns, driverStatusForRuns, 'orchestrator', orchestratorChatId)
  const runningRun = runsRaw.find((run) => statusForRun(run) === 'running')
  // A session the user explicitly clicked (honoured only while it still exists).
  const chosenSession = s.selectedSession && runsRaw.some((run) => run.id === s.selectedSession)
    ? s.selectedSession
    : null
  const activeSessionId = hasSessionIndex
    ? (chosenSession
      || (runsRaw.some((run) => run.id === orchestratorChatId) ? orchestratorChatId : '')
      || (!orchestratorChatId ? (chosenSession || runningRun?.id || runsRaw[0]?.id || '') : ''))
    : (selectedAgent?.parentSession || chosenSession || runningRun?.id || latestTurn?.parentSession || latestAgent?.parentSession || runsRaw[0]?.id || '')
  const activeRunRaw = runsRaw.find((run) => run.id === activeSessionId)
  const activePipelineRun = (s.pipelineRuns || [])
    .filter((run) => run.chatId === activeSessionId
      && !isPipelineChatId(run.chatId)
      && (!activeRunRaw?.rootTurn?.startedAt || Number(run.startedAt || 0) >= Number(activeRunRaw.rootTurn.startedAt)))
    .sort((a, b) => Number(b.startedAt || 0) - Number(a.startedAt || 0))[0]
  const activeSessionAgents = Array.from(new Map([
    ...(activeRunRaw?.steps || []),
    ...(activePipelineRun?.stages || []),
  ].map((agent) => [agent.handle, agent])).values())
  const runningInSession = activeSessionAgents.find((a) => a.status === 'running')
  const currentSessionAgent = runningInSession || activeSessionAgents[activeSessionAgents.length - 1]
  const processTextForRuns = driverBelongsToOrchestrator
    ? [driverStatusForRuns.stderrTail, driverStatusForRuns.stdoutTail].filter(Boolean).join('\n')
    : ''
  const activeRunStatus = activeRunRaw ? statusForRun(activeRunRaw) : 'abandoned'
  // Always list EVERY session (newest first); the main panel focuses the
  // active/chosen one. Chronological run number: oldest is R01, newest highest.
  const runNumberById = new Map()
  runsRaw.forEach((run, i) => runNumberById.set(run.id, runsRaw.length - i))
  const nowMs = Date.now()
  const runs = runsRaw.map((run) => {
    const status = statusForRun(run)
    const m = sm[status] || sm.abandoned
    const current = run.steps.find((a) => a.status === 'running') || run.steps[run.steps.length - 1]
    const selected = run.id === activeSessionId
    const session = run.session
    const startedAt = Number(run.rootTurn?.startedAt || run.turn?.startedAt || 0)
    const workerCount = hasSessionIndex ? Number(session?.workers || 0) : run.steps.length
    return {
      id: run.id,
      startedAt,
      bucket: railBucketFor(status),
      selected,
      // A running card shows elapsed; a finished one shows when it ran.
      clock: clockLabel(startedAt, nowMs),
      age: ageLabel(startedAt, nowMs),
      workersLabel: plural(workerCount, 'worker'),
      turnsLabel: plural(hasSessionIndex ? Number(session?.rootTurns || 0) : run.steps.length, 'turn'),
      title: hasSessionIndex
        ? (session?.title || ('Session ' + String(run.id).slice(-8)))
        : ('Session ' + String(run.id || 'driver').slice(0, 12)),
      subtitle: hasSessionIndex
        ? `${Number(session?.rootTurns || 0)} root turns · ${Number(session?.workers || 0)} workers`
        : (run.steps.length ? run.steps.length + ' workers' : 'driver-only turn'),
      workerCount,
      status,
      statusLabel: m.label,
      statusColor: m.color,
      // Escalation is the state that needs you — say so on the card.
      stateLabel: status === 'escalated' && current?.max
        ? `escalated at ${Number(current.turns || 0)}/${Number(current.max)}`
        : m.label,
      currentBrief: hasSessionIndex
        ? (session?.lastRequest || run.task || current?.brief || '')
        : (current?.brief || run.task || 'Driver handled this turn without spawning workers.'),
      orderLabel: (hasSessionIndex ? 'S' : 'R') + String(runNumberById.get(run.id) || 1).padStart(2, '0'),
      onSelect: () => act.selectSession(run.id),
    }
  })
  // Presentation order inside the rail is bucket first, recency second; runsRaw
  // is already newest-first so the filter preserves it.
  const railGroups = RAIL_BUCKETS
    .map((bucket) => ({ ...bucket, runs: runs.filter((run) => run.bucket === bucket.key) }))
    .filter((bucket) => bucket.runs.length)
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
  const workerSessionSteps = activeSessionAgents.map((a, i) => {
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
      orderLabel: (hasSessionIndex ? 'A' : 'W') + String(hasSessionIndex ? i + 2 : (workerOrder.get(a.handle) || i + 1)).padStart(2, '0'),
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
  const rootTurn = hasSessionIndex ? activeRunRaw?.rootTurn : null
  const rootStatus = activeRunRaw?.live ? 'running' : (activeRunRaw ? statusForRun(activeRunRaw) : 'abandoned')
  const rootMeta = sm[rootStatus] || sm.abandoned
  const rootBrief = rootTurn?.request || activeRunRaw?.session?.lastRequest || activeRunRaw?.task || ''
  const rootTokens = Number(rootTurn?.tokensInEstimate || 0) + Number(rootTurn?.tokensOutEstimate || 0)
  const rootStep = rootTurn
    ? {
        handle: 'root:' + String(rootTurn.parentSession || activeSessionId).slice(-12),
        role: 'root',
        brief: rootBrief,
        status: rootStatus,
        statusLabel: rootMeta.label,
        statusColor: rootMeta.color,
        isCurrent: !!activeRunRaw?.live,
        stopHint: '',
        attemptLabel: '',
        orderLabel: 'A01',
        roleChipStyle: roleChip('root', hueFor('root')),
        dotStyle: 'width:6px;height:6px;border-radius:50%;background:' + rootMeta.color + ';' + (rootStatus === 'running' ? 'animation:pulse 1.4s ease-in-out infinite;' : ''),
        barFillStyle: 'height:100%;width:' + (rootStatus === 'running' ? '55' : '100') + '%;background:' + rootMeta.color + ';border-radius:3px;transition:width .4s ease',
        cardStyle: 'position:relative;width:218px;flex-shrink:0;background:#182130;border:1px solid ' + (activeRunRaw?.live ? rootMeta.color : 'rgba(255,155,61,0.35)') + ';border-radius:10px;padding:13px 14px;' + (activeRunRaw?.live ? 'box-shadow:0 0 0 1px ' + rootMeta.color + '55,0 0 24px ' + rootMeta.color + '22;' : ''),
        turnsLabel: Number(rootTurn.cycles || 0) + ' cycles',
        toolsLabel: rootTokens + ' tokens',
        showConnector: workerSessionSteps.length > 0,
        onSelect: () => act.clearSelect(),
      }
    : null
  const sessionSteps = rootStep ? [rootStep, ...workerSessionSteps] : workerSessionSteps

  const activeEvidenceSessions = new Set([
    rootTurn?.parentSession,
    activeRunRaw?.turn?.parentSession,
    activePipelineRun?.sessionId,
  ].filter(Boolean))
  const activeWorkerHandles = new Set(activeSessionAgents.map((agent) => agent.handle))
  const toolEvidence = (s.toolEvidence || []).filter((row) => (
    (row.chatId && row.chatId === activeSessionId)
    || activeEvidenceSessions.has(row.sessionId)
  ))
  const skillsByWorker = {}
  toolEvidence.forEach((row) => {
    if (row.category !== 'skills' || row.status !== 'ok' || !row.skillId || !row.workerId) return
    if (!skillsByWorker[row.workerId]) skillsByWorker[row.workerId] = []
    if (!skillsByWorker[row.workerId].includes(row.skillId)) skillsByWorker[row.workerId].push(row.skillId)
  })
  // Root-selected skills pushed into a worker at spawn (option 3) have no
  // musubi_get_skill tool-call, so they never reach `toolEvidence`. Fold the
  // spawn-row `pushedSkill` into the same per-worker skill map + log stream so
  // the node badge, "Skills used" panel, and audited-activity list show them
  // exactly like a pulled skill.
  activeSessionAgents.forEach((agent) => {
    if (!agent.pushedSkill || !agent.handle) return
    if (!skillsByWorker[agent.handle]) skillsByWorker[agent.handle] = []
    if (!skillsByWorker[agent.handle].includes(agent.pushedSkill)) {
      skillsByWorker[agent.handle].push(agent.pushedSkill)
    }
  })
  const runtimeLogs = []
  toolEvidence.forEach((row) => runtimeLogs.push({
    id: `tool-${row.id}`,
    auditId: row.id,
    ts: row.ts || '',
    workerId: row.workerId || '',
    role: clipEvidence(row.role || 'worker', 60),
    category: row.category === 'skills' ? 'skills' : 'tools',
    name: clipEvidence(row.category === 'skills' && row.skillId ? row.skillId : row.tool, 100),
    status: clipEvidence(String(row.status || 'unknown').toLowerCase(), 40),
    detail: clipEvidence(row.detail || ''),
  }))
  activeSessionAgents.forEach((agent) => {
    if (!agent.pushedSkill || !agent.handle) return
    runtimeLogs.push({
      id: `pushed-skill-${agent.handle}`,
      auditId: agent.id ?? null,
      ts: '',
      workerId: agent.handle,
      role: clipEvidence(agent.role || 'worker', 60),
      category: 'skills',
      name: clipEvidence(agent.pushedSkill, 100),
      status: 'pushed',
      detail: 'pushed at spawn',
    })
  })
  ;(s.agentCycles || [])
    .filter((row) => activeEvidenceSessions.has(row.sessionId))
    .forEach((row) => {
      const workerId = row.workerId || 'root'
      runtimeLogs.push({
        id: `model-${row.sessionId}-${workerId}-${row.cycleIdx}`,
        auditId: null,
        ts: '',
        workerId,
        role: row.stage || (workerId === 'root' ? 'root' : 'worker'),
        category: 'model',
        name: `cycle ${Number(row.cycleIdx || 0) + 1}`,
        status: String(row.cycleStatus || 'ok').toLowerCase(),
        detail: `${Number(row.tokensIn || 0)} in · ${Number(row.tokensOut || 0)} out · ${Number(row.lmMs || 0)} ms`,
      })
      ;(row.toolNames || []).forEach((tool, index) => {
        const backedByToolLedger = toolEvidence.some((entry) => entry.workerId === workerId && entry.tool === tool)
        if (backedByToolLedger) return
        runtimeLogs.push({
          id: `cycle-tool-${row.sessionId}-${workerId}-${row.cycleIdx}-${index}`,
          auditId: null,
          ts: '',
          workerId,
          role: row.stage || 'worker',
          category: tool === 'musubi_get_skill' ? 'skills' : 'tools',
          name: tool,
          status: String(row.cycleStatus || 'ok').toLowerCase(),
          detail: '',
        })
      })
    })
  ;(s.policy || [])
    .filter((row) => activeWorkerHandles.has(row.handle) || (!row.handle && ['agent', 'driver'].includes(row.role)))
    .forEach((row) => runtimeLogs.push({
      id: `policy-${row.id}`,
      auditId: row.id,
      ts: row.ts || '',
      workerId: row.handle || 'root',
      role: clipEvidence(row.role || 'worker', 60),
      category: 'policy',
      name: clipEvidence(row.tool, 100),
      status: clipEvidence(String(row.verdict || 'unknown').toLowerCase(), 40),
      detail: clipEvidence(row.reason || ''),
    }))
  // Per-worker token totals for the runtime graph. Worker cycles record under
  // the parent session_id with worker_id = handle (root cycles use 'root'), so
  // one pass over the active-session cycles gives each node its own usage.
  const tokensByWorker = {}
  ;(s.agentCycles || [])
    .filter((row) => activeEvidenceSessions.has(row.sessionId))
    .forEach((row) => {
      const workerId = row.workerId || 'root'
      tokensByWorker[workerId] = (tokensByWorker[workerId] || 0)
        + Math.max(0, Number(row.tokensIn) || 0)
        + Math.max(0, Number(row.tokensOut) || 0)
    })
  const runtimeNodes = []
  if (activeRunRaw || rootTurn) {
    runtimeNodes.push({
      id: 'root',
      parentId: null,
      kind: 'root',
      role: 'driver',
      label: 'Driver · the knot',
      brief: rootBrief,
      status: rootStatus,
      statusLabel: rootMeta.label,
      turns: Number(rootTurn?.cycles || 0),
      maxTurns: null,
      tools: runtimeLogs.filter((row) => row.workerId === 'root' && row.category === 'tools').length,
      skills: skillsByWorker.root || [],
      tokens: tokensByWorker.root || 0,
    })
  }
  activeSessionAgents.forEach((agent) => {
    const exactParent = activeWorkerHandles.has(agent.parentAgent) ? agent.parentAgent : 'root'
    const meta = sm[agent.status] || sm.abandoned
    runtimeNodes.push({
      id: agent.handle,
      parentId: exactParent,
      kind: 'worker',
      role: agent.role || 'worker',
      label: prettyRole(agent.role),
      brief: agent.brief || '',
      status: agent.status,
      statusLabel: meta.label,
      turns: Number(agent.turns || 0),
      maxTurns: Number(agent.max || 0),
      tools: runtimeLogs.filter((row) => row.workerId === agent.handle && row.category === 'tools').length,
      skills: skillsByWorker[agent.handle] || [],
      tokens: tokensByWorker[agent.handle] || 0,
    })
  })
  if (runtimeLogs.some((row) => !row.workerId)) {
    runtimeNodes.push({
      id: 'unassigned',
      parentId: runtimeNodes.some((node) => node.id === 'root') ? 'root' : null,
      kind: 'evidence',
      role: 'unassigned',
      label: 'Unassigned evidence',
      brief: '',
      status: 'unknown',
      statusLabel: 'unassigned',
      turns: 0,
      maxTurns: null,
      tools: runtimeLogs.filter((row) => !row.workerId && row.category === 'tools').length,
      skills: [],
    })
    runtimeLogs.forEach((row) => { if (!row.workerId) row.workerId = 'unassigned' })
  }
  const runtimeGraph = {
    mode: activePipelineRun ? 'pipeline' : 'direct',
    pipelineName: activePipelineRun?.pipelineName || '',
    nodes: runtimeNodes,
    edges: runtimeNodes.filter((node) => node.parentId).map((node) => ({
      from: node.parentId,
      to: node.id,
      relation: 'summoned',
    })),
  }

  // New Console launches carry a durable request_id through the host, root,
  // workers, and append-only runtime ledger. Project every request in the
  // selected chat instead of replacing the graph with only the latest turn.
  const sessionTurns = orchAgentTurns
    .filter((turn) => turn.chatId === activeSessionId)
    .sort((a, b) => Number(a.startedAt || 0) - Number(b.startedAt || 0))
  const sessionLedger = (s.runtimeLogEvents || [])
    .filter((row) => row.chatId === activeSessionId)
    .sort((a, b) => Number(a.id || 0) - Number(b.id || 0))
  const hasRequestIdentity = sessionLedger.length > 0 || sessionTurns.some((turn) => turn.requestId)
  let projectedRuntimeLogs = runtimeLogs
  let projectedRuntimeGraph = runtimeGraph
  if (hasRequestIdentity) {
    const requestMap = new Map()
    const ensureRequest = (requestId) => {
      if (!requestMap.has(requestId)) {
        requestMap.set(requestId, {
          requestId,
          turn: null,
          events: [],
          agents: [],
        })
      }
      return requestMap.get(requestId)
    }
    sessionTurns.forEach((turn) => {
      const requestId = turn.requestId || `legacy-${turn.parentSession || turn.id}`
      ensureRequest(requestId).turn = turn
    })
    sessionLedger.forEach((event) => {
      if (event.requestId) ensureRequest(event.requestId).events.push(event)
    })

    const sessionParentIds = new Set(sessionTurns.map((turn) => turn.parentSession).filter(Boolean))
    const sessionAgents = Array.from(new Map([
      ...orchSubagents.filter((agent) => (
        agent.chatId === activeSessionId || sessionParentIds.has(agent.parentSession)
      )),
      ...(s.pipelineRuns || [])
        .filter((run) => run.chatId === activeSessionId)
        .flatMap((run) => run.stages || []),
    ].map((agent) => [agent.handle, agent])).values())

    // Order requests oldest-first. The two available keys are not comparable:
    // `agent_turns.started_at` is epoch seconds (~1.79e9) while
    // `runtime_log_events.id` is an AUTOINCREMENT rowid (hundreds). Taking
    // whichever exists and sorting the mixture ranked every turn-less request
    // before every turn-bearing one.
    //
    // That is not an edge case: `_record_agent_turn` is called with
    // `ended_at=time.time()` (agent/run.py), so the row is written when the
    // turn *ends*. The in-flight request therefore never has a turn, always
    // fell back to the small rowid, and was always sorted as the oldest thing
    // in the session — mislabelled R01 and given the head of the continuation
    // chain, on every run.
    //
    // So rank by tier first. A turn row exists if and only if the turn
    // finished, which makes "has no turn" a reliable marker for "still
    // running", and a running request is by definition the newest. Within a
    // tier the keys are homogeneous: epoch seconds for finished requests,
    // ledger rowid for in-flight ones.
    // Sort on the ledger's own rowid, which every request in the new Console
    // has (the host writes a launch line before the process starts) and which
    // is monotonic across the whole session. Legacy requests predating the
    // ledger have only a turn, so they form an earlier block ordered by epoch;
    // the two keys are never compared against each other.
    //
    // The running request is pinned last by identity, not inferred from a
    // missing turn row. `agent_turns` is written with `ended_at=time.time()`
    // so an in-flight request has none — but so does a request whose process
    // failed to spawn, and treating those alike would park a dead request at
    // the head of the timeline forever. The driver already reports which
    // request it owns.
    const liveRequestId = driverRunning ? String(driverStatusForRuns.requestId || '') : ''
    const requestTier = (request) => {
      if (liveRequestId && request.requestId === liveRequestId) return 2
      return request.events.length ? 1 : 0
    }
    const requestKey = (request) => (request.events.length
      ? Number(request.events[0].id || 0)
      : Number(request.turn?.startedAt || 0))
    const requestEntries = Array.from(requestMap.values())
      .sort((a, b) => (requestTier(a) - requestTier(b)) || (requestKey(a) - requestKey(b)))
    requestEntries.forEach((request) => {
      const exactHandles = new Set(request.events.map((event) => event.agentHandle).filter(Boolean))
      request.agents = sessionAgents.filter((agent) => (
        exactHandles.has(agent.handle)
        || (request.turn?.parentSession && agent.parentSession === request.turn.parentSession)
      ))
    })
    // Pipeline stages use their pipeline session as parent_session. Attach the
    // pipeline envelope to the closest preceding root request in this chat.
    ;(s.pipelineRuns || [])
      .filter((run) => run.chatId === activeSessionId)
      .forEach((run) => {
        const owner = [...requestEntries]
          .reverse()
          .find((request) => Number(request.turn?.startedAt || 0) <= Number(run.startedAt || 0))
        if (!owner) return
        ;(run.stages || []).forEach((agent) => {
          if (!owner.agents.some((entry) => entry.handle === agent.handle)) owner.agents.push(agent)
        })
      })

    projectedRuntimeLogs = sessionLedger.map((row) => ({
      id: `runtime-${row.id}`,
      auditId: null,
      requestId: row.requestId,
      seq: Number(row.seq || 0),
      ts: row.ts || '',
      source: row.source || 'root',
      stream: row.stream || 'stderr',
      agentHandle: row.agentHandle || '',
      workerId: row.agentHandle || `request:${row.requestId}`,
      role: clipEvidence(row.role || (row.agentHandle ? 'worker' : 'root'), 60),
      category: clipEvidence(row.category || 'output', 40),
      name: clipEvidence(row.category || row.stream || 'output', 100),
      status: clipEvidence(row.stream || row.source || 'output', 40),
      message: row.message || '',
      detail: row.message || '',
    }))

    const allNodes = []
    const requestGroups = []
    requestEntries.forEach((request, index) => {
      const requestNodeId = `request:${request.requestId}`
      const requestLogs = projectedRuntimeLogs.filter((row) => row.requestId === request.requestId)
      const running = driverStatusForRuns.requestId === request.requestId && driverRunning
      const failedAgent = request.agents.find((agent) => ['failed', 'escalated', 'abandoned'].includes(agent.status))
      const status = running ? 'running' : (failedAgent?.status || 'done')
      const meta = sm[status] || sm.abandoned
      const requestNode = {
        id: requestNodeId,
        requestId: request.requestId,
        parentId: index > 0 ? `request:${requestEntries[index - 1].requestId}` : null,
        kind: 'request',
        role: 'request',
        label: `Request ${String(index + 1).padStart(2, '0')}`,
        title: request.turn?.request || request.events.find((event) => event.source === 'host')?.message || 'Runtime request',
        brief: request.turn?.request || '',
        status,
        statusLabel: meta.label,
        turns: Number(request.turn?.cycles || 0),
        maxTurns: null,
        tools: requestLogs.filter((row) => row.category === 'tools').length,
        skills: [],
        tokens: Number(request.turn?.tokensInEstimate || 0) + Number(request.turn?.tokensOutEstimate || 0),
        logCount: requestLogs.length,
      }
      allNodes.push(requestNode)
      const agentNodes = request.agents.map((agent) => {
        const agentMeta = sm[agent.status] || sm.abandoned
        const exactParent = request.agents.some((candidate) => candidate.handle === agent.parentAgent)
          ? agent.parentAgent
          : requestNodeId
        const agentLogs = projectedRuntimeLogs.filter((row) => row.agentHandle === agent.handle)
        return {
          id: agent.handle,
          requestId: request.requestId,
          parentId: exactParent,
          kind: 'agent',
          role: agent.role || 'worker',
          label: prettyRole(agent.role),
          title: `${prettyRole(agent.role)} · ${agent.handle}`,
          brief: agent.brief || '',
          status: agent.status,
          statusLabel: agentMeta.label,
          turns: Number(agent.turns || 0),
          maxTurns: Number(agent.max || 0),
          tools: agentLogs.filter((row) => row.category === 'tools').length,
          skills: skillsByWorker[agent.handle] || [],
          tokens: (s.agentCycles || [])
            .filter((cycle) => cycle.workerId === agent.handle)
            .reduce((sum, cycle) => sum + Number(cycle.tokensIn || 0) + Number(cycle.tokensOut || 0), 0),
          logCount: agentLogs.length,
        }
      })
      allNodes.push(...agentNodes)
      requestGroups.push({ ...requestNode, agents: agentNodes })
    })
    projectedRuntimeGraph = {
      mode: activePipelineRun ? 'pipeline' : 'direct',
      pipelineName: activePipelineRun?.pipelineName || '',
      requests: requestGroups,
      nodes: allNodes,
      edges: allNodes.filter((node) => node.parentId).map((node) => ({
        from: node.parentId,
        to: node.id,
        relation: node.kind === 'request' ? 'continued' : 'summoned',
      })),
    }
  }

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
  // Every parent session this conversation has opened — one per root turn —
  // so the panel headed "This session" actually covers the session.
  const activeEconomicsSessionIds = new Set([
    ...orchAgentTurns.filter((turn) => turn.chatId === activeSessionId).map((turn) => turn.parentSession),
    ...activeSessionAgents.map((agent) => agent.parentSession),
    activeRunRaw?.rootTurn?.parentSession,
    activeRunRaw?.turn?.parentSession,
    activeSessionId,
  ].filter(Boolean))
  const driverSummary = {
    title: 'Run summary',
    countLine: statusCountLine(activeSessionAgents),
    focusLine: focusLineForRun(activeSessionAgents, currentSessionAgent),
    alertLine: runSummary(activeRunStatus, processTextForRuns, activeRunRaw),
    metaLine: (activeDef.model || 'unconfigured') + ' - ' + (s.activeProfile || 'no profile'),
    economics: economicsForSession(s.agentCycles || [], activeEconomicsSessionIds),
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

  const chatView = buildChatView(s.chat || [])

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
  const hasDriverLog = !!driverProcessLog
  const orchestratorHasDriverLog = driverBelongsToOrchestrator && hasDriverLog
  const activeSurfaceLabel = driverSurface === 'pipeline' ? 'Pipeline' : 'Orchestrator'
  const orchestratorBlockedByPipeline = driverRunning && !orchestratorOwnsDriver
  const composerBlocked = orchestratorOwnsDriver || orchestratorBlockedByPipeline
    || historicalSessionBlocked || orchestratorPipelineBlocked

  // The Approve button is the composer with one keystroke removed, so it obeys
  // exactly the composer's conditions: no approving into a busy driver, into a
  // pipeline's run, or from a historical session you are only reading.
  const approvalRequest = composerBlocked ? null : pendingApproval(s.chat || [])
  const approval = approvalRequest
    && approvalScope(s, approvalRequest.token) !== s.dismissedApproval
    ? {
      token: approvalRequest.token,
      summary: approvalRequest.summary,
      onApprove: () => act.approveDestructive(approvalRequest.token),
      onReject: () => act.dismissApproval(approvalRequest.token),
    }
    : null

  // "What is the agent doing right now?" answered in one object: who is acting,
  // what the act is, how long it has been going, and how to stop it. The banner
  // that renders this is the largest element on the screen; everything that has
  // already happened collapses to one line beneath it.
  // The banner reports the run the DRIVER owns, not the session you happen to
  // be reading. Those are the same thing until you click a historical session
  // mid-run — at which point sourcing the actor and the act from the viewed
  // session presents a finished run's last log line as live activity, which is
  // the one thing this banner must never do.
  const driverChatId = String(driverStatus.chatId || '')
  const driverRequestId = String(driverStatus.requestId || '')
  const driverSessionAgents = allSubagents.filter((agent) => agent.chatId === driverChatId)
  const driverRunningAgent = driverSessionAgents.find((agent) => agent.status === 'running')
  const nowActor = driverRunningAgent
    ? prettyRole(driverRunningAgent.role)
    : (orchestratorOwnsDriver ? 'Driver' : '')
  // Scoped to the driver's own request, so a quiet historical session cannot
  // lend its last line to a run happening elsewhere.
  const lastRuntimeLine = [...(s.runtimeLogEvents || [])]
    .filter((row) => (driverRequestId
      ? row.requestId === driverRequestId
      : row.chatId === driverChatId))
    .sort((a, b) => Number(a.id || 0) - Number(b.id || 0))
    .reverse()
    .find((row) => (row.message || '').trim())
  const nowTurns = Number(driverRunningAgent?.turns || 0)
  const nowMaxTurns = Number(driverRunningAgent?.max || 0)
  const driverRunIsPipeline = !!driverStatus.pipelineName
  const nowRun = orchestratorOwnsDriver
    ? {
      running: true,
      actor: nowActor,
      // Reads as a sentence: "Planner is reading your workspace".
      headline: `${nowActor} is ${actPhrase(lastRuntimeLine)}`,
      task: driverStatus.task || '',
      act: lastRuntimeLine?.message || driverStatusText || 'working…',
      actRole: lastRuntimeLine?.role || '',
      // Epoch seconds; the banner ticks the elapsed clock locally.
      startedAt: Number(driverStatus.startedAt || 0),
      turns: nowTurns,
      maxTurns: nowMaxTurns,
      turnLabel: nowMaxTurns ? `Turn ${nowTurns} of ${nowMaxTurns}` : '',
      progress: nowMaxTurns ? Math.min(100, Math.round((nowTurns / nowMaxTurns) * 100)) : 0,
      modeLabel: driverRunIsPipeline ? (driverStatus.pipelineName || 'pipeline') : 'direct',
      // True while you are reading some other session; the banner says so and
      // offers the way back rather than pretending you are watching this run.
      viewingElsewhere: !!driverChatId && driverChatId !== activeSessionId,
      onOpenRunningSession: () => act.selectSession(driverChatId),
    }
    : { running: false, headline: 'Nothing is running', act: '', startedAt: 0, progress: 0, viewingElsewhere: false }

  // The trust strip proved nothing while its four pills were hard-coded
  // strings: unchanging green teaches the eye to ignore green. Same four
  // invariants, but each one now moves, so a deny is visible when it lands.
  //
  // Both counters below have to come from uncapped aggregates. `s.audit` is a
  // display list the Rust side ends with `audit.truncate(120)`, so reading its
  // length pins the number at 120 forever — a counter that stops moving is the
  // decoration this strip was meant to stop being. total_spawned/total_done
  // are incremented across the whole unbounded `subagent_audit` scan, and by
  // HI #8 every spawn and completion writes exactly one row, so their sum is
  // the ledger's real size.
  const auditRows = Number(s.totalSpawned || 0) + Number(s.totalDone || 0)
  // HI #3 firewalls the evaluator: `_STAGE_PERMISSIONS["reviewer"]` plus the
  // runner's last-stage brief. `reviewer-aux` is a haiku helper from the
  // exploration split, not an evaluator, so counting it overstated the claim
  // while pipeline evaluators — which live in pipelineRuns[].stages, never in
  // s.subagents — were missed entirely.
  const evaluatorHandles = new Set([
    ...allSubagents.filter((agent) => agent.role === 'reviewer'),
    ...(s.pipelineRuns || []).flatMap((run) => (run.stages || []).slice(-1)),
  ].map((agent) => agent.handle).filter(Boolean))
  const trustCounters = [
    { key: 'policy', label: 'policy', value: `${s.allowCount || 0} allow / ${s.denyCount || 0} deny`, ok: !Number(s.denyCount || 0) },
    { key: 'audit', label: 'audit', value: `${auditRows} rows appended`, ok: true },
    { key: 'firewall', label: 'firewall', value: `${evaluatorHandles.size} evaluator isolated`, ok: true },
    { key: 'substrate', label: 'substrate', value: '0 LM calls', ok: true },
  ]

  return {
    nowRun,
    sessionsHidden: !!s.sessionsHidden,
    onToggleSessions: act.toggleSessions,
    trustCounters,
    railGroups,
    onStopRun: act.cancelAgent,
    runMode: s.runMode === 'pipeline' ? 'pipeline' : 'direct',
    selectedPipeline: s.selectedPipeline || '',
    selectedPipelineRunnable: !!selectedPipelineEntry?.runnable,
    pipelineOptions,
    onSetRunMode: act.setRunMode,
    runtimeGraph: projectedRuntimeGraph,
    runtimeLogs: projectedRuntimeLogs,
    skillsByWorker,
    onSelectRuntimeNode: act.selectAgent,
    onOpenAuditEvidence: () => act.setView('audit'),
    pipelineBuilder: {
      ...pipelineBuilderState,
      library: {
        query: libraryQuery,
        presets: libraryPresets,
        agents: libraryAgents,
        spawnRoles: librarySpawnRoles,
      },
      dirty: isDirty(pipelineBuilderState.draft, pipelineBuilderState.savedRecipe),
      selectedStage: pipelineBuilderState.draft?.stages?.[pipelineBuilderState.selectedStageIndex] || null,
      actions: {
        onNew: act.newPipelineRecipe,
        onClose: act.closePipelineRecipe,
        onSelectStep: act.selectPipelineBuilderStep,
        onSelectStage: act.selectPipelineStage,
        onAddStage: act.addPipelineStage,
        onMoveStage: act.movePipelineStage,
        onRemoveStage: act.removePipelineStage,
        onUpdateStage: act.updatePipelineStage,
        onUpdateRecipe: act.updatePipelineRecipe,
        onAddSpawn: act.addPipelineSpawn,
        onRemoveSpawn: act.removePipelineSpawn,
        onLoad: act.loadPipelineRecipe,
        onValidate: act.validatePipelineRecipe,
        onSave: act.savePipelineRecipe,
        onConfirmTransition: act.confirmPipelineTransition,
        onCancelTransition: act.cancelPipelineTransition,
      },
    },
    isOrch: s.view === 'orchestrator', isPipeline: s.view === 'pipeline', isPolicy: s.view === 'policy', isAudit: s.view === 'audit', isModels: s.view === 'models', isSkills: s.view === 'skills', isSettings: s.view === 'settings',    view: s.view,
    runtimeSourceLabel: sourceLabels[s.runtimeSource] || 'audit.db',
    orchNav: navStyle(s.view === 'orchestrator'), pipeNav: navStyle(s.view === 'pipeline'), polNav: navStyle(s.view === 'policy'), audNav: navStyle(s.view === 'audit'), modNav: navStyle(s.view === 'models'), sklNav: navStyle(s.view === 'skills'), settingsNav: navStyle(s.view === 'settings'),
    // On another view the Orchestrator button navigates. Once you are already
    // there it toggles the sessions rail, which is the pane directly beside
    // it — the control that hides a pane now sits next to that pane instead of
    // in the opposite corner of the window.
    selOrch: () => (s.view === 'orchestrator' ? act.toggleSessions() : act.setView('orchestrator')),
    orchNavTitle: s.view === 'orchestrator'
      ? (s.sessionsHidden ? 'Show sessions' : 'Hide sessions')
      : 'Orchestrator',
    selPipe: () => act.setView('pipeline'), selPolicy: () => act.setView('policy'), selAudit: () => act.setView('audit'), selModels: () => act.setView('models'), selSkills: () => act.setView('skills'), selSettings: () => act.setView('settings'),
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
    viewingHistoricalSession,
    activeRunSteps: sessionSteps,
    selectedStepDetail: detail,
    driverSummary,
    runStatusSummary: runSummary(activeRunStatus, processTextForRuns, activeRunRaw),
    sessionSteps,
    sessionTitle: activeSessionId
      ? ('Session ' + (hasSessionIndex ? activeSessionId.slice(-8) : activeSessionId.slice(0, 12)))
      : 'Session history',
    sessionSubtitle: hasSessionIndex
      ? (activeRunRaw?.rootTurn
        ? `latest root turn · ${plural(activeSessionAgents.length, 'summoned worker')}`
        : 'no agent activity yet')
      : (sessionSteps.length
        ? (plural(sessionSteps.length, 'worker') + ' · full history for this parent run')
        : (activeRunRaw?.turn ? 'driver-only turn — no workers spawned' : 'no workers in this session yet')),
    hasDetail: !!detail, showFeed: !detail, detail, clearSelect: () => act.clearSelect(),
    clearNodeSelect: () => act.clearNodeSelect(),
    driverBusy: orchestratorOwnsDriver, driverTask: (orchestratorOwnsDriver || orchestratorHasDriverLog) ? (driverStatus.task || '') : '', driverStatusText: orchestratorBlockedByPipeline ? `${activeSurfaceLabel} run is active.` : (driverBelongsToOrchestrator ? driverStatusText : ''),
    driverProcessOpen: orchestratorOwnsDriver && !!s.processOpen, driverProcessLog: orchestratorHasDriverLog ? driverProcessLog : '', hasDriverLog: orchestratorHasDriverLog, onToggleProcess: act.toggleProcess,
    logWindowOpen: orchestratorHasDriverLog && !!s.logWindowOpen, onOpenLog: act.openProcessLog, onCloseLog: act.closeProcessLog,
    onNewSession: act.newSession,
    clearDriverDisabled: !!driverStatus.running,
    events: s.events, chat: chatView, draft: s.draft, onDraft: act.onDraft, onDraftKey: act.onDraftKey,
    // Send stays send. It used to swap glyph and colour in place while busy,
    // so the only destructive control in the app sat exactly where the safe
    // one had been — a misclick waiting to happen. Stopping a run is now a
    // labelled button in the Now banner, where you are already looking.
    onSend: act.sendChat,
    sendTitle: orchestratorBlockedByPipeline
      ? `${activeSurfaceLabel} run is active`
      : (orchestratorOwnsDriver
        ? 'Agent is running — stop it from the banner to send'
        : (orchestratorPipelineBlocked ? 'Select a runnable pipeline' : 'Send')),
    sendMode: 'send',
    sendDisabled: composerBlocked,
    approval,
    inputDisabled: orchestratorBlockedByPipeline || historicalSessionBlocked,
    disabledText: historicalDisabledText || (orchestratorBlockedByPipeline ? `${activeSurfaceLabel} run is active...` : (orchestratorPipelineBlocked ? 'Select a runnable pipeline before sending.' : '')),
    onOpenArtifact: (path) => act.openArtifact(path, 'orchestrator'),
    policy, policyRoles, allowCount: s.allowCount, denyCount: s.denyCount,
    auditView, auditCountLabel: auditView.length + ' rows · immutable',
    setAuditAll: () => act.setAuditFilter('all'), setAuditSpawn: () => act.setAuditFilter('spawned'), setAuditDone: () => act.setAuditFilter('completed'),
    auditFAll: auditBtn(s.auditFilter === 'all'), auditFSpawn: auditBtn(s.auditFilter === 'spawned'), auditFDone: auditBtn(s.auditFilter === 'completed'),
    profiles, skills, setupRows, setupPathHint: setup.pathHint || '',
    workspaceRoot: setup.projectRoot || '',
    // A blocked boundary outranks a transient picker message: while it is set
    // the agent will not launch at all, so that is what the operator must see.
    workspaceError: s.workspaceBlockedReason || s.workspaceError || '',
    workspaceSwitching: !!s.workspaceSwitching,
    workspaceSwitchDisabled: !!driverStatus.running || !!s.workspaceSwitching,
    onChooseWorkspace: act.chooseWorkspace,
  }
}
