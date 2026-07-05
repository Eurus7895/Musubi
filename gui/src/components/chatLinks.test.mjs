import assert from 'node:assert/strict'
import { test } from 'node:test'

import { parseInlineSegments } from './chatLinks.js'

test('parses Musubi process log links as internal links', () => {
  const parts = parseInlineSegments('Done. [Open full process log](musubi-log:last)')

  assert.deepEqual(parts, [
    { type: 'text', text: 'Done. ' },
    { type: 'link', label: 'Open full process log', href: 'musubi-log:last' },
  ])
})

test('parses artifact links with encoded Windows paths', () => {
  const parts = parseInlineSegments(
    '- [weather-dashboard.html](musubi-artifact:C%3A%5CWorkspace%5CProjects%5CMusubi%5Cweather-dashboard.html)'
  )

  assert.equal(parts[0].type, 'text')
  assert.equal(parts[1].type, 'link')
  assert.equal(parts[1].label, 'weather-dashboard.html')
  assert.equal(
    parts[1].href,
    'musubi-artifact:C%3A%5CWorkspace%5CProjects%5CMusubi%5Cweather-dashboard.html'
  )
})

test('keeps strong text segments separate from plain text', () => {
  const parts = parseInlineSegments('Agent **done** now')

  assert.deepEqual(parts, [
    { type: 'text', text: 'Agent ' },
    { type: 'strong', text: 'done' },
    { type: 'text', text: ' now' },
  ])
})
