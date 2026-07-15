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

test('center workspace separates topology from filtered evidence logs', () => {
  for (const label of ['Graph', 'Logs', 'All', 'Tools', 'Skills', 'Policy', 'Model']) {
    assert.match(source, new RegExp(`['\"]${label}['\"]|>${label}<`))
  }
  assert.match(source, /runtimeGraph/)
  assert.match(source, /runtimeLogs/)
  assert.match(source, /onSelectNode/)
})

test('Conversation owns narrative modes and skill provenance', () => {
  assert.match(source, /Summary/)
  assert.match(source, /Verbose/)
  assert.match(source, /Skills used/)
  assert.match(source, /skillsByWorker/)
  assert.equal((source.match(/<ChatBody/g) || []).length, 1)
})

test('legacy duplicated summary and expanded step-detail surfaces are removed', () => {
  for (const legacy of ['TokenEconomics', 'Run summary', 'Step detail', 'agent flow']) {
    assert.equal(source.includes(legacy), false, legacy)
  }
})
