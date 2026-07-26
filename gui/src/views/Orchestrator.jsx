import { useEffect, useMemo, useState } from 'react'
import ChatBody from '../components/ChatBody.jsx'
import NewSessionButton from '../components/NewSessionButton.jsx'
import TokenEconomics from '../components/TokenEconomics.jsx'

const REQUEST_LOG_FILTERS = ['All', 'Host', 'Root', 'Workers', 'stdout', 'stderr']
const AGENT_LOG_FILTERS = ['All', 'Model', 'Tools', 'Skills', 'Policy', 'stdout', 'stderr']

export default function Orchestrator({ vals }) {
  const [sessionsHidden, setSessionsHidden] = useState(false)
  const [conversationCollapsed, setConversationCollapsed] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [detailTab, setDetailTab] = useState('overview')
  const [logFilter, setLogFilter] = useState('all')
  const [logQuery, setLogQuery] = useState('')
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
    setDetailTab('overview')
    setLogFilter('all')
    setLogQuery('')
    if (node.kind === 'request' || node.id === 'root') vals.clearSelect?.()
    else vals.onSelectRuntimeNode?.(node.id)
  }

  return (
    <div className={`orchestrator-console${sessionsHidden ? ' sessions-hidden' : ''}${conversationCollapsed ? ' conversation-collapsed' : ''}`}>
      {!sessionsHidden && <SessionsRail vals={vals} onHide={() => setSessionsHidden(true)} />}
      <main className="orchestrator-workspace">
        <RunConfiguration vals={vals} onShowSessions={sessionsHidden ? () => setSessionsHidden(false) : null} />
        <RuntimeStatus vals={vals} />
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
            : <>
                <div className="runtime-evidence__header">
                  <div>
                    <span className="workspace-kicker">Runtime evidence</span>
                    <h1>{vals.sessionTitle}</h1>
                    <p>{vals.sessionSubtitle}</p>
                  </div>
                </div>
                <RuntimeGraph graph={vals.runtimeGraph} onSelectNode={onSelectNode} />
              </>}
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

function SessionsRail({ vals, onHide }) {
  return (
    <aside className="session-rail">
      <header>
        <div><strong>Sessions</strong><span>project conversations · newest first</span></div>
        <button aria-label="Hide sessions" onClick={onHide}>←</button>
      </header>
      <div className="session-rail__list">
        {vals.runs.length ? vals.runs.map((run) => (
          <button key={run.id} className={`session-card${run.id === vals.activeRunId ? ' is-active' : ''}`} onClick={run.onSelect} title={run.title}>
            <span className="session-card__top"><b>{run.orderLabel}</b><em style={{ color: run.statusColor }}>● {run.statusLabel}</em></span>
            <strong>{run.title}</strong><small>{run.subtitle}</small><p>{run.currentBrief}</p>
          </button>
        )) : <div className="session-rail__empty">No sessions yet.</div>}
      </div>
    </aside>
  )
}

function RunConfiguration({ vals, onShowSessions }) {
  const pipelineMode = vals.runMode === 'pipeline'
  return (
    <section className="run-config">
      {onShowSessions && <button className="show-sessions" onClick={onShowSessions}>→ Show sessions</button>}
      <div className="run-config__heading"><span>Execution owner</span><strong>Orchestrator</strong></div>
      <div className="run-mode" role="group" aria-label="Execution mode">
        <button className={!pipelineMode ? 'is-active' : ''} onClick={() => vals.onSetRunMode?.('direct')}>Direct</button>
        <button className={pipelineMode ? 'is-active' : ''} onClick={() => vals.onSetRunMode?.('pipeline')}>Pipeline</button>
      </div>
      {pipelineMode && (
        <select value={vals.selectedPipeline || ''} onChange={(event) => vals.pipelineOptions.find((option) => option.name === event.target.value)?.onSelect?.()} aria-label="Pipeline recipe">
          <option value="">Select a governed pipeline</option>
          {vals.pipelineOptions.map((option) => <option key={option.name} value={option.name} disabled={!option.runnable}>{option.name}{option.runnable ? '' : ' · blocked'}</option>)}
        </select>
      )}
      <div className="run-config__hint">{pipelineMode ? 'Runs the selected saved recipe in this conversation.' : 'The driver chooses governed workers as evidence requires.'}</div>
    </section>
  )
}

function RuntimeStatus({ vals }) {
  const graph = vals.runtimeGraph || { nodes: [], requests: [], mode: 'direct' }
  return (
    <div className="runtime-status">
      <span className={vals.driverBusy ? 'status-pill is-running' : 'status-pill'}><i />{vals.driverBusy ? 'Running' : 'Idle'}</span>
      <span><b>{graph.mode === 'pipeline' ? graph.pipelineName || 'Pipeline' : 'Direct'}</b> mode</span>
      <span><b>{graph.requests?.length || 0}</b> requests</span>
      <span><b>{graph.nodes.length}</b> audited nodes</span>
      <span><b>{vals.runtimeLogs?.length || 0}</b> log rows</span>
      <span className="runtime-status__model">{vals.activeModel}</span>
    </div>
  )
}

function RuntimeGraph({ graph = {}, onSelectNode }) {
  const requests = graph.requests || []
  if (!requests.length) {
    const nodes = graph.nodes || []
    if (!nodes.length) return <div className="runtime-empty">No audited runtime nodes for this session.</div>
    return <div className="runtime-graph"><div className="runtime-graph__list">{nodes.map((node) => <GraphNode key={node.id} node={node} onSelect={onSelectNode} />)}</div></div>
  }
  return (
    <div className="runtime-graph">
      <div className="runtime-graph__notice">Each arrow means “continued” or “summoned”; every prior request remains visible in this session.</div>
      <div className="request-graph">
        {requests.map((request, index) => (
          <section className="request-group" key={request.id}>
            <GraphNode node={request} onSelect={onSelectNode} order={index + 1} />
            {!!request.agents?.length && (
              <div className="request-group__agents">
                {request.agents.map((agent) => <GraphNode key={agent.id} node={agent} onSelect={onSelectNode} />)}
              </div>
            )}
            {index < requests.length - 1 && <div className="request-group__continue"><span>↓</span><em>next request</em></div>}
          </section>
        ))}
      </div>
    </div>
  )
}

function GraphNode({ node, onSelect, order }) {
  return (
    <button className={`runtime-node kind-${node.kind}`} onClick={() => onSelect(node)}>
      <span className="runtime-node__identity">
        <em>{node.kind}</em>
        <strong>{node.title || node.label}</strong>
        <code>{node.requestId || node.id}</code>
      </span>
      <span className={`runtime-node__status status-${node.status}`}>● {node.statusLabel}</span>
      <span className="runtime-node__metrics">
        {order ? <b>R{String(order).padStart(2, '0')}</b> : <b>{node.turns}{node.maxTurns ? `/${node.maxTurns}` : ''}</b>}
        {' · '}<b>{node.tools}</b> tools{' · '}<b>{new Intl.NumberFormat('en-US').format(node.tokens || 0)}</b> tokens
      </span>
      <span className="runtime-node__skills">{node.skills?.length ? node.skills.map((skill) => <i key={skill}>{skill}</i>) : <small>{node.logCount || 0} log rows</small>}</span>
      <span className="runtime-node__open">Open details →</span>
    </button>
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
  return (
    <div className="runtime-overview">
      <div className="runtime-overview__hero">
        <span className={`runtime-node__status status-${node.status}`}>● {node.statusLabel}</span>
        <h2>{node.brief || node.title || node.label}</h2>
        <p>{isRequest ? 'Overview covers the root and every agent summoned by this request.' : 'Overview and metrics for this exact agent handle.'}</p>
      </div>
      <div className="runtime-overview__metrics">
        <div><span>Role</span><strong>{node.role}</strong></div>
        <div><span>Turns</span><strong>{node.turns}{node.maxTurns ? ` / ${node.maxTurns}` : ''}</strong></div>
        <div><span>Tools</span><strong>{node.tools || 0}</strong></div>
        <div><span>Tokens</span><strong>{new Intl.NumberFormat('en-US').format(node.tokens || 0)}</strong></div>
        <div><span>Log rows</span><strong>{node.logCount || 0}</strong></div>
      </div>
      <button className="runtime-overview__log" onClick={onOpenLog}>Open {isRequest ? 'Request log' : 'Agent log'} →</button>
    </div>
  )
}

function RuntimeLogs({ node, rows, filter, onFilter, query, onQuery }) {
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
            <b>{String(index + 1).padStart(2, '0')}</b>
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
        <div><strong>Conversation</strong><span>narrative and artifacts</span></div>
        <div><NewSessionButton onClick={vals.onNewSession} disabled={vals.clearDriverDisabled} /><button className="collapse-button" aria-label="Collapse conversation" onClick={onToggle}>→</button></div>
      </header>
      <div className="skills-used"><span>Skills used</span>{skills.length ? skills.map((skill) => <i key={skill}>{skill}</i>) : <small>No successful skill calls recorded</small>}</div>
      <TokenEconomics economics={vals.driverSummary?.economics} />
      <ChatBody vals={vals} />
    </aside>
  )
}
