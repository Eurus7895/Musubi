import { useEffect, useMemo, useState } from 'react'
import ChatBody from '../components/ChatBody.jsx'
import NewSessionButton from '../components/NewSessionButton.jsx'
import TokenEconomics from '../components/TokenEconomics.jsx'

const LOG_FILTERS = ['All', 'Tools', 'Skills', 'Policy', 'Model']

export default function Orchestrator({ vals }) {
  const [sessionsCollapsed, setSessionsCollapsed] = useState(false)
  const [conversationCollapsed, setConversationCollapsed] = useState(false)
  const [workspaceTab, setWorkspaceTab] = useState('graph')
  const [conversationMode, setConversationMode] = useState('summary')
  const [selectedNodeId, setSelectedNodeId] = useState('root')
  const [logFilter, setLogFilter] = useState('all')
  const [logQuery, setLogQuery] = useState('')
  const nodes = vals.runtimeGraph?.nodes || []

  useEffect(() => {
    if (!nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(nodes[0]?.id || 'root')
    }
  }, [nodes, selectedNodeId])

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || nodes[0]
  const matchingLogs = useMemo(() => {
    const needle = logQuery.trim().toLowerCase()
    return (vals.runtimeLogs || []).filter((row) => {
      const sameWorker = !selectedNode || row.workerId === selectedNode.id
      const sameCategory = logFilter === 'all' || row.category === logFilter
      const matches = !needle || `${row.name} ${row.status} ${row.detail} ${row.role}`.toLowerCase().includes(needle)
      return sameWorker && sameCategory && matches
    })
  }, [vals.runtimeLogs, selectedNode, logFilter, logQuery])

  const onSelectNode = (node) => {
    setSelectedNodeId(node.id)
    setWorkspaceTab('logs')
    if (node.id === 'root') vals.clearSelect?.()
    else vals.onSelectRuntimeNode?.(node.id)
  }

  return (
    <div className={`orchestrator-console${sessionsCollapsed ? ' sessions-collapsed' : ''}${conversationCollapsed ? ' conversation-collapsed' : ''}`}>
      <SessionsRail vals={vals} collapsed={sessionsCollapsed} onToggle={() => setSessionsCollapsed((value) => !value)} />
      <main className="orchestrator-workspace">
        <RunConfiguration vals={vals} />
        <RuntimeStatus vals={vals} />
        <section className="runtime-evidence">
          <div className="runtime-evidence__header">
            <div>
              <span className="workspace-kicker">Runtime evidence</span>
              <h1>{vals.sessionTitle}</h1>
              <p>{vals.sessionSubtitle}</p>
            </div>
            <div className="workspace-tabs" role="tablist" aria-label="Runtime evidence view">
              <button className={workspaceTab === 'graph' ? 'is-active' : ''} onClick={() => setWorkspaceTab('graph')}>Graph</button>
              <button className={workspaceTab === 'logs' ? 'is-active' : ''} onClick={() => setWorkspaceTab('logs')}>Logs</button>
            </div>
          </div>
          {workspaceTab === 'graph'
            ? <RuntimeGraph graph={vals.runtimeGraph} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} />
            : <RuntimeLogs node={selectedNode} rows={matchingLogs} filter={logFilter} onFilter={setLogFilter} query={logQuery} onQuery={setLogQuery} onOpenAudit={vals.onOpenAuditEvidence} />}
        </section>
      </main>
      <ConversationPanel
        vals={vals} collapsed={conversationCollapsed} onToggle={() => setConversationCollapsed((value) => !value)}
        mode={conversationMode} onMode={setConversationMode}
      />
    </div>
  )
}

function SessionsRail({ vals, collapsed, onToggle }) {
  return (
    <aside className="session-rail">
      <header><div><strong>Sessions</strong>{!collapsed && <span>project conversations · newest first</span>}</div><button aria-label={collapsed ? 'Expand sessions' : 'Collapse sessions'} onClick={onToggle}>{collapsed ? '›' : '‹'}</button></header>
      <div className="session-rail__list">
        {vals.runs.length ? vals.runs.map((run) => (
          <button key={run.id} className={`session-card${run.id === vals.activeRunId ? ' is-active' : ''}`} onClick={run.onSelect} title={run.title}>
            <span className="session-card__top"><b>{run.orderLabel}</b><em style={{ color: run.statusColor }}>● {!collapsed && run.statusLabel}</em></span>
            {!collapsed && <><strong>{run.title}</strong><small>{run.subtitle}</small><p>{run.currentBrief}</p></>}
          </button>
        )) : <div className="session-rail__empty">{collapsed ? '—' : 'No sessions yet.'}</div>}
      </div>
    </aside>
  )
}

function RunConfiguration({ vals }) {
  const pipelineMode = vals.runMode === 'pipeline'
  return (
    <section className="run-config">
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
  const graph = vals.runtimeGraph || { nodes: [], mode: 'direct' }
  return (
    <div className="runtime-status">
      <span className={vals.driverBusy ? 'status-pill is-running' : 'status-pill'}><i />{vals.driverBusy ? 'Running' : 'Idle'}</span>
      <span><b>{graph.mode === 'pipeline' ? graph.pipelineName || 'Pipeline' : 'Direct'}</b> mode</span>
      <span><b>{graph.nodes.length}</b> audited nodes</span>
      <span><b>{vals.runtimeLogs?.length || 0}</b> evidence rows</span>
      <span className="runtime-status__model">{vals.activeModel}</span>
    </div>
  )
}

function RuntimeGraph({ graph = {}, selectedNodeId, onSelectNode }) {
  const nodes = graph.nodes || []
  if (!nodes.length) return <div className="runtime-empty">No audited runtime nodes for this session.</div>
  return (
    <div className="runtime-graph">
      <div className="runtime-graph__notice">Edges mean “summoned”. They do not imply sequential or parallel execution.</div>
      <div className="runtime-graph__list">
        {nodes.map((node, index) => (
          <div className="runtime-node-row" key={node.id}>
            <div className="runtime-node-row__rail"><span>{String(index + 1).padStart(2, '0')}</span>{index < nodes.length - 1 && <i />}</div>
            <button className={`runtime-node${node.id === selectedNodeId ? ' is-selected' : ''}`} onClick={() => onSelectNode(node)}>
              <span className="runtime-node__identity"><em>{node.kind}</em><strong>{node.label}</strong><code>{node.id}</code></span>
              <span className={`runtime-node__status status-${node.status}`}>● {node.statusLabel}</span>
              <span className="runtime-node__metrics"><b>{node.turns}{node.maxTurns ? `/${node.maxTurns}` : ''}</b> turns <b>{node.tools}</b> tools <b>{new Intl.NumberFormat('en-US').format(node.tokens || 0)}</b> tokens</span>
              <span className="runtime-node__skills">{node.skills?.length ? node.skills.map((skill) => <i key={skill}>{skill}</i>) : <small>no skill evidence</small>}</span>
              <span className="runtime-node__open">Open logs →</span>
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function RuntimeLogs({ node, rows, filter, onFilter, query, onQuery, onOpenAudit }) {
  return (
    <div className="runtime-logs">
      <div className="runtime-logs__controls">
        <div><strong>{node?.label || 'Runtime'}</strong><code>{node?.id || 'unassigned'}</code></div>
        <div className="log-filters">{LOG_FILTERS.map((label) => <button key={label} className={filter === label.toLowerCase() ? 'is-active' : ''} onClick={() => onFilter(label.toLowerCase())}>{label}</button>)}</div>
        <input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search evidence…" />
      </div>
      <div className="runtime-log-list">
        {rows.length ? rows.map((row) => (
          <article key={row.id} className={`runtime-log-row category-${row.category}`}>
            <time>{row.ts || 'cycle'}</time><span>{row.category}</span><strong>{row.name}</strong><em className={`status-${row.status}`}>{row.status}</em><p>{row.detail || 'Stored audit metadata only.'}</p>
            {row.auditId && <button onClick={onOpenAudit}>Open in Audit</button>}
          </article>
        )) : <div className="runtime-empty">No matching evidence for this node.</div>}
      </div>
    </div>
  )
}

function ConversationPanel({ vals, collapsed, onToggle, mode, onMode }) {
  const skills = Array.from(new Set(Object.values(vals.skillsByWorker || {}).flat()))
  if (collapsed) return <aside className="conversation-panel is-collapsed"><button aria-label="Expand conversation" onClick={onToggle}>‹</button><span>Conversation</span></aside>
  return (
    <aside className="conversation-panel">
      <header className="conversation-panel__header">
        <div><strong>Conversation</strong><span>narrative and artifacts</span></div>
        <div><NewSessionButton onClick={vals.onNewSession} disabled={vals.clearDriverDisabled} /><button className="collapse-button" aria-label="Collapse conversation" onClick={onToggle}>›</button></div>
      </header>
      <div className="conversation-tabs">
        <button className={mode === 'summary' ? 'is-active' : ''} onClick={() => onMode('summary')}>Summary</button>
        <button className={mode === 'verbose' ? 'is-active' : ''} onClick={() => onMode('verbose')}>Verbose</button>
      </div>
      <div className="skills-used"><span>Skills used</span>{skills.length ? skills.map((skill) => <i key={skill}>{skill}</i>) : <small>No successful skill calls recorded</small>}</div>
      <TokenEconomics economics={vals.driverSummary?.economics} />
      {mode === 'verbose' && <VerboseEvidence rows={vals.runtimeLogs || []} />}
      <ChatBody vals={vals} />
    </aside>
  )
}

function VerboseEvidence({ rows }) {
  const visible = rows.slice(-12)
  return <div className="verbose-evidence"><strong>Audited activity</strong>{visible.length ? visible.map((row) => <div key={row.id}><span>{row.role}</span><b>{row.category === 'skills' ? `skill:${row.name}` : row.name}</b><em>{row.status}</em></div>) : <p>No tool, skill, policy, or model evidence recorded.</p>}</div>
}
