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
  assert.match(source, /Show sessions/)
  assert.equal(source.includes('sessions-collapsed'), false)
})
