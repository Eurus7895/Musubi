import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('./Orchestrator.jsx', import.meta.url), 'utf8')

test('Orchestrator owns Direct and Pipeline execution configuration', () => {
  assert.match(source, /Direct/)
  assert.match(source, /Pipeline/)
  assert.match(source, /onSetRunMode/)
  assert.match(source, /pipelineOptions/)
  assert.match(source, /NewSessionButton/)
})

test('Orchestrator owns editable session folder grants', () => {
  assert.match(source, /SessionFolders/)
  assert.match(source, /Add folder/)
  assert.match(source, /onRenameSessionFolder/)
  assert.match(source, /onRemoveSessionFolder/)
  assert.match(source, /fixed harness root|fixed/)
})

test('center workspace opens request or agent detail and returns to the graph', () => {
  for (const label of ['Back to graph', 'Request log', 'Agent log', 'Overview', 'All', 'Tools', 'Policy', 'Model']) {
    assert.equal(source.includes(label), true, label)
  }
  assert.match(source, /runtimeGraph/)
  assert.match(source, /runtimeLogs/)
  assert.match(source, /onSelectNode/)
  assert.equal(source.includes('workspaceTab'), false)
})

test('Conversation keeps narrative, skill provenance, and token economics without verbose mode', () => {
  assert.equal(source.includes('>Summary<'), false)
  assert.equal(source.includes('>Verbose<'), false)
  assert.match(source, /Skills used/)
  assert.match(source, /skillsByWorker/)
  assert.match(source, /TokenEconomics/)
  assert.equal((source.match(/<ChatBody/g) || []).length, 1)
})

test('legacy duplicated summary and verbose evidence surfaces are removed', () => {
  for (const legacy of ['VerboseEvidence', 'Run summary', 'Step detail', 'agent flow']) {
    assert.equal(source.includes(legacy), false, legacy)
  }
})

test('hiding Sessions removes the rail instead of collapsing it', () => {
  assert.match(source, /sessions-hidden/)
  assert.equal(source.includes('sessions-collapsed'), false)
  // The rail toggle is owned by the source so the activity bar can drive it.
  assert.match(source, /vals\.sessionsHidden/)
  assert.match(source, /vals\.onToggleSessions/)
  // The composer's old button — which put the control for the leftmost pane
  // in the bottom-right corner of the window — is gone.
  assert.equal(source.includes('show-sessions'), false)
  assert.equal(/composer__config[\s\S]*Show sessions/.test(source), false)
  // Restoring the rail has a visible affordance, in the corner the rail's own
  // ← hide button occupied, so the gesture round-trips where it started.
  assert.match(source, /rail-toggle/)
  assert.match(source, /aria-label="Show sessions"/)
  assert.match(source, /aria-label="Hide sessions"/)
})

test('the Now banner answers what the agent is doing, and offers the way out', () => {
  assert.match(source, /function NowBanner/)
  // Actor, act, elapsed, and stop — the four things wanted mid-run.
  assert.match(source, /nowRun/)
  assert.match(source, /now\.headline/)
  assert.match(source, /now\.act/)
  assert.match(source, /elapsedSince/)
  assert.match(source, /Stop run/)
  assert.match(source, /onStopRun/)
  // The banner ticks its own clock; the data source is event-driven.
  assert.match(source, /setInterval/)
})

test('the run status strip and run-config band no longer sit above the evidence', () => {
  // 206px of stacked chrome came before the first data row. Run mode is a
  // start-of-run decision and now lives with the composer instead.
  assert.equal(source.includes('runtime-status'), false)
  assert.equal(source.includes('audited nodes'), false)
  assert.equal(source.includes('log rows</span>'), false)
  assert.match(source, /composer__config/)
  assert.match(source, /config=\{<RunConfiguration/)
})

test('the session log is reachable without drilling into a single request', () => {
  // The Timeline/Session log toggle had stylesheet rules but no component, so
  // the only log surface was per-request — a run spanning several requests
  // could not be read end to end.
  assert.match(source, /surface-tabs/)
  assert.match(source, />Timeline</)
  assert.match(source, /Session log/)
  assert.match(source, /surfaceTab/)
  // Session scope is unscoped by construction: a lingering node selection
  // would silently narrow it back down to that node's rows.
  assert.match(source, /setSelectedNodeId\(null\)/)
  // Rows carry the request that emitted them, since a row ordinal is useless
  // once the log spans requests.
  assert.match(source, /requestLabels/)
  assert.equal(source.includes('workspaceTab'), false)
})

test('finished requests collapse to one line and absent values are not zeros', () => {
  assert.match(source, /function RequestTimeline/)
  assert.match(source, /function RequestRow/)
  // A sparse run typesets its noughts like real data unless they are dashed.
  assert.match(source, /metricField/)
  assert.match(source, /'—'/)
  assert.match(source, /is-absent/)
  // The running request shows its last log lines without leaving the timeline.
  assert.match(source, /function LiveLog/)
  assert.match(source, /LIVE_LOG_LINES/)
  // The 980px cap wasted gutter on a wide display; the pane grows instead.
  assert.equal(source.includes('request-graph'), false)
})
