// Leading conversational filler a user prepends when accepting a suggestion.
// "ok then run pipeline" must reach the picker exactly as "run pipeline"
// does — the traced conversation typed that and fell through to the agent,
// which spent a planner round trip and answered with a wall of questions.
// Stripped one token at a time so what remains still has to BE the command:
// a real work order that merely mentions a pipeline ("add a pipeline stage to
// the runner") never matches.
const LEADING_FILLER = new Set([
  'ok', 'okay', 'k', 'yes', 'yeah', 'yep', 'sure', 'alright', 'right',
  'then', 'so', 'now', 'lets', "let's", 'please', 'and', 'also', 'just',
])

// Every phrasing that means "show me the pipeline picker". The picker still
// requires the user to pick and send — nothing auto-launches, so the
// human gate on compliance workflows (locked decision #4) is untouched.
const PIPELINE_COMMANDS = new Set([
  'pipeline',
  '/pipeline',
  'the pipeline',
  'run pipeline',
  'run the pipeline',
  'start pipeline',
  'start the pipeline',
  'use pipeline',
  'use the pipeline',
  'open pipeline',
  'open the pipeline',
])

// One vocabulary, two gates: the picker matches a command exactly, the named
// form matches the same command followed by a recipe name. Deriving the second
// from the first is what keeps them from drifting — hand-maintained, they
// already had: `open pipeline` opened the picker while `open pipeline
// feature-dev` matched neither gate and shipped to the driver agent as a work
// order. Longest-first so the generated alternation is deterministic.
//
// The captured name is only a *candidate*. This module cannot know which
// recipes exist, so the caller must resolve it against the catalog and treat
// an unknown name as ordinary prose — see TauriSource.sendChat.
const NAMED_PIPELINE = new RegExp(
  '^(?:'
  + [...PIPELINE_COMMANDS]
    .map((command) => command.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&'))
    .sort((a, b) => b.length - a.length)
    .join('|')
  + ')\\s+([a-z0-9]+(?:-[a-z0-9]+)*)$',
)

function normalize(text) {
  return String(text || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/[?!.]+$/, '')
}

function stripLeadingFiller(normalized) {
  const words = normalized.split(' ').filter(Boolean)
  let start = 0
  while (start < words.length && LEADING_FILLER.has(words[start].replace(/[,.!]+$/, ''))) {
    start += 1
  }
  return words.slice(start).join(' ')
}

export function classifyChatCommand(text) {
  const core = stripLeadingFiller(normalize(text))
  if (PIPELINE_COMMANDS.has(core)) {
    return { kind: 'openPipelinePicker' }
  }
  return { kind: 'sendToAgent' }
}

// The pipeline named inline ("run pipeline feature-dev"), or '' when the
// message does not name one. Same filler tolerance as classifyChatCommand so
// both entry points accept the phrasings a user actually types.
export function pipelineNameFromCommand(text) {
  const core = stripLeadingFiller(normalize(text))
  return core.match(NAMED_PIPELINE)?.[1] || ''
}
