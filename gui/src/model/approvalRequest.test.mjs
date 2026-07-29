import test from 'node:test'
import assert from 'node:assert/strict'
import { pendingApproval, approvalSummary } from './approvalRequest.js'

const refusal = (token) => ({
  role: 'driver',
  text: 'This would DELETE 3 file(s): build/a.js, build/b.js, build/c.js. '
    + 'Deletion cannot be undone from here.\n\n'
    + 'To approve exactly this and nothing else, reply with: ' + token,
})

test('the token in the last driver message is the one awaiting an answer', () => {
  const found = pendingApproval([{ role: 'you', text: 'clean the build dir' }, refusal('allow-a3f9c1')])

  assert.equal(found.token, 'allow-a3f9c1')
  assert.equal(found.summary, 'delete 3 file(s)')
})

test('a driver message without a token offers nothing to approve', () => {
  assert.equal(pendingApproval([{ role: 'driver', text: 'Done — nothing was deleted.' }]), null)
  assert.equal(pendingApproval([]), null)
})

test('an answered refusal is history, not a pending offer', () => {
  // Once the user has replied, the last message is their turn (or the driver's
  // next answer, which carries a fresh token if the gate fired again). Either
  // way the old token must not keep offering itself.
  const answered = [refusal('allow-a3f9c1'), { role: 'you', text: 'allow-a3f9c1' }]

  assert.equal(pendingApproval(answered), null)
})

test('a note between the refusal and now closes the offer', () => {
  // Centered notes are harness chatter, not the driver speaking. If one is the
  // last thing in the transcript, the refusal is no longer what the user is
  // being asked about.
  const noted = [refusal('allow-a3f9c1'), { role: 'note', text: 'run cancelled' }]

  assert.equal(pendingApproval(noted), null)
})

test('the token pattern is the six hex digits the harness mints', () => {
  // Not a general "allow-*" match: the width comes from GRANT_DIGEST_CHARS, so
  // prose mentioning an allow-list or a flag cannot dress itself as consent.
  assert.equal(pendingApproval([{ role: 'driver', text: 'add an allow-list entry' }]), null)
  assert.equal(pendingApproval([{ role: 'driver', text: 'reply with: allow-zzzzzz' }]), null)
  assert.equal(pendingApproval([{ role: 'driver', text: 'reply with: allow-abc12' }]), null)
})

test('the summary names the blast radius for every refusal shape', () => {
  assert.equal(approvalSummary('This would DELETE 12 file(s): a, b'), 'delete 12 file(s)')
  assert.equal(
    approvalSummary('its targets cannot be resolved statically, so the harness cannot say'),
    'run an unresolvable delete',
  )
  assert.equal(approvalSummary('This is overwrite number 6 in this run'), 'overwrite #6')
  assert.equal(approvalSummary('something new the harness learned to refuse'), 'this destructive step')
})
