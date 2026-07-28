import { useEffect, useMemo, useState } from 'react'
import ChatBody from '../components/ChatBody.jsx'
import NewSessionButton from '../components/NewSessionButton.jsx'
import TokenEconomics from '../components/TokenEconomics.jsx'

const REQUEST_LOG_FILTERS = ['All', 'Host', 'Root', 'Workers', 'stdout', 'stderr']
const AGENT_LOG_FILTERS = ['All', 'Model', 'Tools', 'Skills', 'Policy', 'stdout', 'stderr']
// How many log lines the running request shows without a click. Three is what
// fits above the fold beside the banner; more and the timeline stops being one.
const LIVE_LOG_LINES = 3

const numberFormat = new Intl.NumberFormat('en-US')

// Absent is not zero. A sparse run typesets three real numbers and a pile of
// noughts identically, so the eye cannot skip the noughts — render them as an
// em dash the scan slides past.
function metricField(value, suffix) {
  const n = Number(value || 0)
  if (!n) return { value: '—', absent: true }
  return { value: suffix ? `${numberFormat.format(n)} ${suffix}` : numberFormat.format(n), absent: false }
}

function Metric({ value, suffix }) {
  const field = metricField(value, suffix)
  return <span className={field.absent ? 'is-absent' : ''}>{field.value}</span>
}

export default function Orchestrator({ vals }) {
  const [conversationCollapsed, setConversationCollapsed] = useState(false)
  // Lives in the source, not here, so the activity bar can toggle it.
  const sessionsHidden = !!vals.sessionsHidden
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [detailTab, setDetailTab] = useState('overview')
  const [logFilter, setLogFilter] = useState('all')
  const [logQuery, setLogQuery] = useState('')
  // Timeline is the structure; Log is every line this session emitted, across
  // all its requests. Without it the only way to read a log was to drill into
  // one request, which cannot show a run that spans several.
  const [surfaceTab, setSurfaceTab] = useState('timeline')
  const nodes = vals.runtimeGraph?.nodes || []

  useEffect(() => {
    if (selectedNodeId && !nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(null)
    }
  }, [nodes, selectedNodeId])

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || null
  const matchingLogs = useMemo(() => {
    const needle = logQuery.trim().toLowerCase()
    return (vals.runtimeLogs || []).filter((row) => {
      const sameScope = !selectedNode
        || (selectedNode.kind === 'request'
          ? row.requestId === selectedNode.requestId
          : (row.agentHandle || row.workerId) === selectedNode.id)
      const sameCategory = logFilter === 'all'
        || row.category === logFilter
        || row.stream === logFilter
        || (logFilter === 'host' && row.source === 'host')
        || (logFilter === 'root' && row.source === 'root')
        || (logFilter === 'workers' && row.source === 'worker')
      const haystack = `${row.message || row.detail || ''} ${row.category || ''} ${row.stream || ''} ${row.role || ''}`.toLowerCase()
      return sameScope && sameCategory && (!needle || haystack.includes(needle))
    })
  }, [vals.runtimeLogs, selectedNode, logFilter, logQuery])

  const onSelectNode = (node) => {
    setSelectedNodeId(node.id)
    setSurfaceTab('timeline')
    setDetailTab('overview')
    setLogFilter('all')
    setLogQuery('')
    if (node.kind === 'request' || node.id === 'root') vals.clearSelect?.()
    else vals.onSelectRuntimeNode?.(node.id)
  }

  // The session log is unscoped, so leaving a node selected would silently
  // narrow it to that node's rows.
  const onSurfaceTab = (tab) => {
    setSurfaceTab(tab)
    if (tab === 'log') {
      setSelectedNodeId(null)
      setLogFilter('all')
      setLogQuery('')
      vals.clearSelect?.()
    }
  }
  const showingLog = surfaceTab === 'log' && !selectedNode
  // R-numbers are positional, matching the labels the timeline renders.
  const requestLabels = useMemo(() => new Map(
    (vals.runtimeGraph?.requests || []).map((request, index) => [
      request.requestId,
      `R${String(index + 1).padStart(2, '0')}`,
    ]),
  ), [vals.runtimeGraph])

  return (
    <div className={`orchestrator-console${sessionsHidden ? ' sessions-hidden' : ''}${conversationCollapsed ? ' conversation-collapsed' : ''}`}>
      {!sessionsHidden && <SessionsRail vals={vals} onHide={vals.onToggleSessions} />}
      <main className="orchestrator-workspace">
        <NowBanner
          now={vals.nowRun}
          onStop={vals.onStopRun}
          onWatch={() => {
            const live = (vals.runtimeGraph?.nodes || []).find((node) => node.status === 'running')
            if (live) { onSelectNode(live); setDetailTab('log') }
          }}
        />
        <div className="session-strip">
          <div className="session-strip__id">
            <strong>{vals.runs.find((run) => run.selected)?.title || vals.sessionTitle}</strong>
            <span>{vals.sessionTitle.toLowerCase()} · {vals.sessionSubtitle}</span>
          </div>
          <div className="surface-tabs" role="tablist" aria-label="Session surface">
            <button className={!showingLog ? 'is-active' : ''} onClick={() => onSurfaceTab('timeline')}>Timeline</button>
            <button className={showingLog ? 'is-active' : ''} onClick={() => onSurfaceTab('log')}>
              Session log{vals.runtimeLogs?.length ? ` · ${vals.runtimeLogs.length}` : ''}
            </button>
          </div>
        </div>
        <section className="runtime-evidence">
          {selectedNode
            ? <RuntimeDetail
                node={selectedNode}
                rows={matchingLogs}
                tab={detailTab}
                onTab={setDetailTab}
                filter={logFilter}
                onFilter={setLogFilter}
                query={logQuery}
                onQuery={setLogQuery}
                onBack={() => setSelectedNodeId(null)}
              />
            : showingLog
              ? <RuntimeLogs
                  node={{ kind: 'request' }}
                  rows={matchingLogs}
                  filter={logFilter}
                  onFilter={setLogFilter}
                  query={logQuery}
                  onQuery={setLogQuery}
                  requestLabels={requestLabels}
                />
              : <RequestTimeline
                  graph={vals.runtimeGraph}
                  logs={vals.runtimeLogs}
                  selectedId={selectedNodeId}
                  onSelectNode={onSelectNode}
                />}
        </section>
      </main>
      <ConversationPanel
        vals={vals}
        collapsed={conversationCollapsed}
        onToggle={() => setConversationCollapsed((value) => !value)}
      />
    </div>
  )
}

// The largest element on the screen, and the only one that answers the
// question the operator opened the console to ask. It names the actor, the
// act, the elapsed time, and the way out.
function NowBanner({ now = {}, onStop, onWatch }) {
  const startedAt = Number(now.startedAt || 0)
  const [elapsed, setElapsed] = useState(() => elapsedSince(startedAt))

  useEffect(() => {
    if (!now.running || !startedAt) return undefined
    setElapsed(elapsedSince(startedAt))
    const id = setInterval(() => setElapsed(elapsedSince(startedAt)), 1000)
    return () => clearInterval(id)
  }, [now.running, startedAt])

  if (!now.running) {
    return (
      <div className="now-banner is-idle">
        <i />
        <div className="now-banner__body"><h1>Nothing is running</h1></div>
      </div>
    )
  }

  return (
    <div className="now-banner">
      <i />
      <div className="now-banner__body">
        <div className="now-banner__headline">
          <h1>{now.headline}</h1>
          {!!elapsed && <span className="now-banner__elapsed">{elapsed}</span>}
        </div>
        <p className="now-banner__act">
          {now.turnLabel ? `${now.turnLabel} · ` : ''}<code>{now.act}</code>
        </p>
        <div className="now-banner__progress">
          <div><i style={{ width: `${now.progress || 0}%` }} /></div>
          <span>
            {now.maxTurns ? `${now.turns} of ${now.maxTurns} turns · ` : ''}{now.modeLabel}
          </span>
        </div>
      </div>
      <div className="now-banner__actions">
        <button className="ui-button" onClick={onWatch}>Watch log</button>
        {/* Stop lives where you are already looking, and says what it does. */}
        <button className="ui-button ui-button--danger" onClick={onStop}>Stop run</button>
      </div>
    </div>
  )
}

function elapsedSince(startedAt) {
  if (!startedAt) return ''
  const total = Math.max(0, Math.round(Date.now() / 1000 - startedAt))
  const minutes = Math.floor(total / 60)
  const seconds = String(total % 60).padStart(2, '0')
  if (minutes < 60) return `${minutes}m ${seconds}s`
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}

function SessionsRail({ vals, onHide }) {
  const groups = vals.railGroups || []
  return (
    <aside className="session-rail">
      <header>
        <strong>Sessions</strong>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>{vals.runs.length}</span>
          <button aria-label="Hide sessions" onClick={onHide}>←</button>
        </div>
      </header>
      <div className="session-rail__list">
        {groups.length ? groups.map((group) => (
          <div key={group.key}>
            <div className="session-group">{group.label}</div>
            <div className="session-group-list">
              {group.runs.map((run) => <SessionCard key={run.id} run={run} />)}
            </div>
          </div>
        )) : <div className="session-rail__empty">No sessions yet.</div>}
      </div>
    </aside>
  )
}

function SessionCard({ run }) {
  const tone = run.status === 'running' ? 'is-live'
    : run.bucket === 'needsYou' ? 'is-escalated'
      : run.status === 'done' ? 'is-done' : 'is-quiet'
  return (
    <button
      className={`session-card ${tone}${run.selected ? ' is-selected' : ''}`}
      onClick={run.onSelect}
      title={run.title}
    >
      <span className="session-card__title"><i /><strong>{run.title}</strong></span>
      <span className="session-card__meta">
        <span className="session-card__state">
          {run.status === 'running' ? `running · ${run.turnsLabel}` : run.stateLabel}
        </span>
        {/* Duplicate titles were indistinguishable without a time. */}
        <span className="session-card__elapsed">{run.status === 'running' ? run.age : run.clock}</span>
      </span>
    </button>
  )
}

// A finished request is one line. The running one is expanded in place with
// its last log lines, so "what is it doing" needs neither a click nor a
// context switch away from the timeline.
function RequestTimeline({ graph = {}, logs = [], selectedId, onSelectNode }) {
  const requests = graph.requests || []
  if (!requests.length) {
    const nodes = graph.nodes || []
    if (!nodes.length) return <div className="runtime-empty">No audited runtime nodes for this session.</div>
    return (
      <div className="runtime-graph">
        <div className="request-timeline">
          {nodes.map((node) => (
            <RequestRow key={node.id} node={node} selectedId={selectedId} onSelect={onSelectNode} />
          ))}
        </div>
      </div>
    )
  }
  return (
    <div className="runtime-graph">
      <div className="request-timeline">
        {[...requests].reverse().map((request, index) => (
          <div key={request.id}>
            <RequestRow
              node={request}
              order={requests.length - index}
              selectedId={selectedId}
              onSelect={onSelectNode}
            />
            {request.status === 'running' && <LiveLog requestId={request.requestId} logs={logs} />}
            {!!request.agents?.length && (
              <div className="request-agents">
                {request.agents.map((agent) => (
                  <RequestRow key={agent.id} node={agent} selectedId={selectedId} onSelect={onSelectNode} />
                ))}
              </div>
            )}
          </div>
        ))}
        <div className="timeline-hint">
          Finished requests collapse to one line. Open any row for its full log — the timeline stays.
        </div>
      </div>
    </div>
  )
}

function RequestRow({ node, order, selectedId, onSelect }) {
  const live = node.status === 'running'
  const isAgent = node.kind === 'agent'
  const label = order
    ? `R${String(order).padStart(2, '0')} · ${node.title || node.label}`
    : (node.title || node.label)
  return (
    <button
      className={`request-row status-${node.status}${live ? ' is-live' : ''}${node.id === selectedId ? ' is-selected' : ''}`}
      onClick={() => onSelect(node)}
      title={node.title || node.label}
    >
      <i />
      <strong>{label}</strong>
      {isAgent && node.maxTurns
        ? <span>{node.turns}/{node.maxTurns} turns</span>
        : <Metric value={node.tools} suffix="tools" />}
      <Metric value={node.tokens} suffix="tok" />
      {live ? <span>now</span> : <Metric value={node.logCount} suffix="rows" />}
    </button>
  )
}

function LiveLog({ requestId, logs }) {
  const lines = logs
    .filter((row) => row.requestId === requestId && (row.message || '').trim())
    .slice(-LIVE_LOG_LINES)
    .reverse()
  return (
    <div className="request-live-log">
      {lines.length ? lines.map((row) => (
        <div key={row.id}>
          <time>{row.ts || '—'}</time>
          <span className={`role-chip role-${row.role}`}>{String(row.role || row.source || 'root').toUpperCase()}</span>
          <code>{row.message}</code>
        </div>
      )) : <div className="request-live-log__empty">No log lines yet for this request.</div>}
    </div>
  )
}

function RuntimeDetail({ node, rows, tab, onTab, filter, onFilter, query, onQuery, onBack }) {
  const isRequest = node.kind === 'request'
  const logLabel = isRequest ? 'Request log' : 'Agent log'
  return (
    <div className="runtime-detail">
      <div className="runtime-detail__header">
        <button className="runtime-detail__back" onClick={onBack}>← Back to graph</button>
        <div className="runtime-detail__title">
          <div><span className="workspace-kicker">{isRequest ? 'Whole request' : 'This agent only'}</span><h1>{node.title || node.label}</h1></div>
          <code>{node.requestId || node.id}</code>
        </div>
        <div className="runtime-detail__tabs" role="tablist">
          <button className={tab === 'overview' ? 'is-active' : ''} onClick={() => onTab('overview')}>Overview</button>
          <button className={tab === 'log' ? 'is-active' : ''} onClick={() => onTab('log')}>{logLabel}</button>
        </div>
      </div>
      {tab === 'overview'
        ? <RuntimeOverview node={node} isRequest={isRequest} onOpenLog={() => onTab('log')} />
        : <RuntimeLogs node={node} rows={rows} filter={filter} onFilter={onFilter} query={query} onQuery={onQuery} />}
    </div>
  )
}

function RuntimeOverview({ node, isRequest, onOpenLog }) {
  const metrics = [
    { label: 'Role', value: node.role, absent: false },
    { label: 'Turns', value: node.turns + (node.maxTurns ? ` / ${node.maxTurns}` : ''), absent: false },
    { label: 'Tools', ...metricField(node.tools) },
    { label: 'Tokens', ...metricField(node.tokens) },
    { label: 'Log rows', ...metricField(node.logCount) },
  ]
  return (
    <div className="runtime-overview">
      <div className="runtime-overview__hero">
        <span className={`runtime-node__status status-${node.status}`}>● {node.statusLabel}</span>
        <h2>{node.brief || node.title || node.label}</h2>
        <p>{isRequest ? 'Overview covers the root and every agent summoned by this request.' : 'Overview and metrics for this exact agent handle.'}</p>
      </div>
      <div className="runtime-overview__metrics">
        {metrics.map(({ label, value, absent }) => (
          <div key={label}><span>{label}</span><strong className={absent ? 'is-absent' : ''}>{value}</strong></div>
        ))}
      </div>
      <button className="ui-button runtime-overview__log" onClick={onOpenLog}>Open {isRequest ? 'Request log' : 'Agent log'} →</button>
    </div>
  )
}

function RuntimeLogs({ node, rows, filter, onFilter, query, onQuery, requestLabels = null }) {
  const filters = node.kind === 'request' ? REQUEST_LOG_FILTERS : AGENT_LOG_FILTERS
  return (
    <div className="runtime-logs">
      <div className="runtime-logs__controls">
        <input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search logs…" />
        <div className="log-filters">{filters.map((label) => <button key={label} className={filter === label.toLowerCase() ? 'is-active' : ''} onClick={() => onFilter(label.toLowerCase())}>{label}</button>)}</div>
      </div>
      <div className="runtime-log-list">
        {rows.length ? rows.map((row, index) => (
          <article key={row.id} className={`runtime-log-line category-${row.category}`}>
            {/* Session scope spans requests, so a row ordinal says nothing —
                carry which request emitted the line instead. */}
            <b>{requestLabels ? (requestLabels.get(row.requestId) || '··') : String(index + 1).padStart(2, '0')}</b>
            <time>{row.ts || '—'}</time>
            <span className={`role-${row.role}`}>{String(row.role || row.source || 'root').toUpperCase()}</span>
            <code>{row.message || row.detail || ''}</code>
          </article>
        )) : <div className="runtime-empty">No matching log lines for this scope.</div>}
      </div>
    </div>
  )
}

function ConversationPanel({ vals, collapsed, onToggle }) {
  const skills = Array.from(new Set(Object.values(vals.skillsByWorker || {}).flat()))
  if (collapsed) return <aside className="conversation-panel is-collapsed"><button aria-label="Expand conversation" onClick={onToggle}>←</button><span>Conversation</span></aside>
  return (
    <aside className="conversation-panel">
      <header className="conversation-panel__header">
        <strong>Conversation</strong>
        <div><NewSessionButton onClick={vals.onNewSession} disabled={vals.clearDriverDisabled} /><button className="collapse-button" aria-label="Collapse conversation" onClick={onToggle}>→</button></div>
      </header>
      <div className="skills-used"><span>Skills used</span>{skills.length ? skills.map((skill) => <i key={skill}>{skill}</i>) : <small>No successful skill calls recorded</small>}</div>
      <TokenEconomics economics={vals.driverSummary?.economics} />
      <ChatBody vals={vals} config={<RunConfiguration vals={vals} />} />
    </aside>
  )
}

// Execution mode and the pipeline recipe are start-of-run decisions, so they
// belong with the composer rather than in a header band you stare past while
// a run is already going. That is 56px of the chrome the banner reclaimed.
function RunConfiguration({ vals }) {
  const pipelineMode = vals.runMode === 'pipeline'
  return (
    <div className="composer__config">
      <div>
        <div className="run-mode" role="group" aria-label="Execution mode">
          <button className={!pipelineMode ? 'is-active' : ''} onClick={() => vals.onSetRunMode?.('direct')}>Direct</button>
          <button className={pipelineMode ? 'is-active' : ''} onClick={() => vals.onSetRunMode?.('pipeline')}>Pipeline</button>
        </div>
        {pipelineMode
          ? (
            <select
              value={vals.selectedPipeline || ''}
              onChange={(event) => vals.pipelineOptions.find((option) => option.name === event.target.value)?.onSelect?.()}
              aria-label="Pipeline recipe"
            >
              <option value="">Select a governed pipeline</option>
              {vals.pipelineOptions.map((option) => <option key={option.name} value={option.name} disabled={!option.runnable}>{option.name}{option.runnable ? '' : ' · blocked'}</option>)}
            </select>
          )
          : <span className="composer__hint">The driver chooses governed workers as evidence requires.</span>}
      </div>
    </div>
  )
}
