// A destructive call is refused by the harness, not by the model, and the
// refusal carries a token the harness itself minted (`agent/blast_radius.py`
// `grant_token`). Consent is that token coming back inside a USER message —
// string equality against a value the harness generated, so nothing has to
// read prose to decide whether the user said yes.
//
// The Console's Approve button submits that exact string on the user's behalf.
// It is a keystroke saver, not a second consent path: the message it sends is
// indistinguishable from the user typing the token, and it travels the same
// `send_chat` route. There is deliberately no GUI-only approval channel — one
// mechanism, two surfaces.

// Six hex digits, the width `GRANT_DIGEST_CHARS` fixes on the Python side.
const GRANT_TOKEN = /\ballow-[0-9a-f]{6}\b/g

/** The token the conversation is waiting on, or null.
 *
 * Only the LAST message counts, and only when the driver wrote it. The gate
 * refuses the tool call, the model then answers the user relaying the refusal,
 * and that answer is the last thing in the transcript — so a token anywhere
 * else is history, already approved or already abandoned.
 */
export function pendingApproval(messages = []) {
  const last = messages[messages.length - 1]
  if (!last || last.role !== 'driver') return null
  const found = String(last.text || '').match(GRANT_TOKEN)
  if (!found) return null
  // The refusal names one token; if a relay quoted several, the last one is
  // the one the sentence "reply with:" introduces.
  return { token: found[found.length - 1], summary: approvalSummary(last.text) }
}

// The blast radius in one line, lifted from the refusal the user is already
// reading. Buttons that say only "Approve" would hide what is being approved.
export function approvalSummary(text) {
  const flat = String(text || '').replace(/\s+/g, ' ')
  const deletes = flat.match(/DELETE (\d+) file\(s\)/)
  if (deletes) return `delete ${deletes[1]} file(s)`
  if (/cannot be resolved statically/.test(flat)) return 'run an unresolvable delete'
  const overwrite = flat.match(/overwrite number (\d+)/)
  if (overwrite) return `overwrite #${overwrite[1]}`
  return 'this destructive step'
}
