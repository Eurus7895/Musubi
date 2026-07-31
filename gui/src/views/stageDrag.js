// Drag payloads for the Stages lane.
//
// Two different gestures land on the same drop target. Dragging a stage card
// carries `application/x-musubi-index` (a reorder); dragging a preset or agent
// out of the library carries `application/x-musubi-stage` (an insert). A stage
// card sits inside the lane, so a card's drop handler has to recognise both:
// if it claims the event and understands only one of them, the other is
// swallowed before the lane behind it can react.
//
// Kept as a pure function over the DataTransfer so the behaviour can be
// asserted directly. The handlers this replaced were inline arrows in JSX, and
// the console suite reads JSX as a string — it could assert that a guard was
// spelled `if (!raw) return`, never what the guard did.

export const STAGE_MIME = 'application/x-musubi-stage'
export const INDEX_MIME = 'application/x-musubi-index'
export const SPAWN_MIME = 'application/x-musubi-spawn'

/**
 * Classify a drop on the stage lane or on one of its cards.
 * Returns `{ kind: 'move', from }`, `{ kind: 'insert', payload }`, or null
 * when the payload belongs to neither gesture — a drag from another surface,
 * a file dragged in from the desktop — which the caller must let pass rather
 * than consume.
 */
export function readStageDrop(dataTransfer) {
  const raw = read(dataTransfer, INDEX_MIME)
  if (raw) {
    const from = Number(raw)
    return Number.isInteger(from) && from >= 0 ? { kind: 'move', from } : null
  }
  const payload = parse(read(dataTransfer, STAGE_MIME))
  return payload && typeof payload === 'object' ? { kind: 'insert', payload } : null
}

/** The role name behind a Handoffs spawn drag, or '' for anything else. */
export function readSpawnRole(dataTransfer) {
  const payload = parse(read(dataTransfer, SPAWN_MIME))
  const role = payload && typeof payload === 'object' ? payload.role : ''
  return typeof role === 'string' ? role : ''
}

function read(dataTransfer, mime) {
  try { return dataTransfer.getData(mime) || '' } catch { return '' }
}

function parse(raw) {
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}
