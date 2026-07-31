import test from 'node:test'
import assert from 'node:assert/strict'
import { readStageDrop, readSpawnRole, STAGE_MIME, INDEX_MIME, SPAWN_MIME } from './stageDrag.js'

// A DataTransfer carries every format set on it; getData returns '' for one
// that was never set. That empty string is the whole bug this file guards.
function transfer(entries) {
  return { getData: (mime) => entries[mime] ?? '' }
}

test('a stage card dragged within the lane reads as a reorder', () => {
  assert.deepEqual(readStageDrop(transfer({ [INDEX_MIME]: '2' })), { kind: 'move', from: 2 })
  assert.deepEqual(readStageDrop(transfer({ [INDEX_MIME]: '0' })), { kind: 'move', from: 0 })
})

test('a preset dragged from the library reads as an insert, not as nothing', () => {
  // The regression: the card's drop handler called stopPropagation() before
  // checking the payload, so a library preset dropped onto an existing card
  // was consumed by the card, understood by neither, and never reached the
  // lane behind it. With stages on screen the cards are most of the lane's
  // area, so the more stages you had the more often a drop did nothing.
  const drop = readStageDrop(transfer({ [STAGE_MIME]: JSON.stringify({ kind: 'preset', id: 'plan' }) }))
  assert.deepEqual(drop, { kind: 'insert', payload: { kind: 'preset', id: 'plan' } })
})

test('an agent dragged from the library reads as an insert', () => {
  const drop = readStageDrop(transfer({ [STAGE_MIME]: JSON.stringify({ kind: 'agent', agent: 'reviewer' }) }))
  assert.deepEqual(drop, { kind: 'insert', payload: { kind: 'agent', agent: 'reviewer' } })
})

test('a reorder wins when both formats are present', () => {
  // Browsers keep every format set during a drag. A card carries the index;
  // if a stale stage payload rode along, the gesture is still a reorder.
  const drop = readStageDrop(transfer({ [INDEX_MIME]: '1', [STAGE_MIME]: '{"id":"plan"}' }))
  assert.deepEqual(drop, { kind: 'move', from: 1 })
})

test('a drop the lane does not own is left alone rather than consumed', () => {
  // null is what tells the card handler not to claim the event, so a drag from
  // another surface still reaches whatever is behind it.
  assert.equal(readStageDrop(transfer({})), null)
  assert.equal(readStageDrop(transfer({ [SPAWN_MIME]: '{"role":"explorer"}' })), null)
  assert.equal(readStageDrop(transfer({ [STAGE_MIME]: 'not json' })), null)
  assert.equal(readStageDrop(transfer({ [STAGE_MIME]: 'null' })), null)
  assert.equal(readStageDrop(transfer({ [STAGE_MIME]: '"a string"' })), null)
  assert.equal(readStageDrop(transfer({ [INDEX_MIME]: 'abc' })), null)
  assert.equal(readStageDrop(transfer({ [INDEX_MIME]: '1.5' })), null)
  assert.equal(readStageDrop(transfer({ [INDEX_MIME]: '-1' })), null)
})

test('a DataTransfer that refuses getData does not throw mid-drop', () => {
  // Reading a format the drag never offered throws in some engines; a throw
  // inside a drop handler leaves the lane in a half-dropped state.
  const hostile = { getData: () => { throw new Error('protected mode') } }
  assert.equal(readStageDrop(hostile), null)
  assert.equal(readSpawnRole(hostile), '')
})

test('the Handoffs spawn drag yields a role only for its own payload', () => {
  assert.equal(readSpawnRole(transfer({ [SPAWN_MIME]: JSON.stringify({ role: 'explorer' }) })), 'explorer')
  // A stage dragged onto a spawn cluster must not add a nested worker.
  assert.equal(readSpawnRole(transfer({ [STAGE_MIME]: '{"id":"plan"}' })), '')
  assert.equal(readSpawnRole(transfer({ [INDEX_MIME]: '0' })), '')
  assert.equal(readSpawnRole(transfer({})), '')
  assert.equal(readSpawnRole(transfer({ [SPAWN_MIME]: '{"role":null}' })), '')
  assert.equal(readSpawnRole(transfer({ [SPAWN_MIME]: 'not json' })), '')
})
