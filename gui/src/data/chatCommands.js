export function classifyChatCommand(text) {
  const raw = String(text || '').trim()
  const normalized = raw.toLowerCase().replace(/\s+/g, ' ')
  if (normalized === 'pipeline' || normalized === '/pipeline' || normalized === 'run pipeline') {
    return { kind: 'openPipelinePicker' }
  }
  return { kind: 'sendToAgent' }
}
