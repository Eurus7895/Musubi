import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('./Settings.jsx', import.meta.url), 'utf8')

test('Settings does not own session folder grants', () => {
  assert.doesNotMatch(source, /Application workspace/)
  assert.doesNotMatch(source, /Choose folder/)
  assert.doesNotMatch(source, /onChooseWorkspace/)
  assert.match(source, /Core runtime/)
})
