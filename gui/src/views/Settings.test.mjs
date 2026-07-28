import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('./Settings.jsx', import.meta.url), 'utf8')

test('Settings exposes one governed application workspace picker', () => {
  assert.match(source, /Application workspace/)
  assert.match(source, /Choose folder/)
  assert.match(source, /onChooseWorkspace/)
  assert.match(source, /workspaceSwitchDisabled/)
  assert.match(source, /Console restarts/)
})
