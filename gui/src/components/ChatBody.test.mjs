import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('./ChatBody.jsx', import.meta.url), 'utf8')

test('a pending destructive approval is answerable without retyping the token', () => {
  assert.match(source, /function ApprovalRow/)
  assert.match(source, /vals\.approval && <ApprovalRow/)
  assert.match(source, /approval\.onApprove/)
  assert.match(source, /approval\.onReject/)
  // The token is on the button, not hidden behind the label: what is being
  // approved has to be legible at the moment of approving.
  assert.match(source, /\{approval\.token\}/)
  assert.match(source, /\{approval\.summary\}/)
})

test('the approval row sits with the conversation, not in the composer', () => {
  // The refusal it answers is the last message; a control docked to the
  // composer would read as a mode switch rather than a reply to that message.
  const scroll = source.slice(0, source.indexOf('className="composer"'))
  assert.match(scroll, /<ApprovalRow/)
})
