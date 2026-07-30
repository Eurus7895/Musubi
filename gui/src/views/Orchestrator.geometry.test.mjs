// Geometry assertions for the Orchestrator's panel toggles.
//
// The rest of the suite reads Orchestrator.jsx as a string and asserts on
// substrings, which cannot fail on a coordinate. Every defect this file guards
// was invisible to that: a toggle 108px from where it claimed to be, a header
// overflowing its column by 30px, a glyph 1.5px off centre. Layout needs a
// layout engine, so this drives headless Chromium over a fixture that mirrors
// the real DOM and measures the result.
//
// The fixture duplicates structure, not styling — it links the real
// src/index.css. Class names are the contract between the two, and
// Orchestrator.test.mjs asserts those against the JSX.
//
// Skipped when no Chromium is on the box; set CHROME_BIN to point at one.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const CSS = fileURLToPath(new URL('../index.css', import.meta.url))

function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    join(process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers', 'chromium-1194/chrome-linux/chrome'),
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ]
  return candidates.find((path) => path && existsSync(path)) || null
}

const chrome = findChrome()

const BANNER = `
  <div class="now-banner"><i class="live-dot"></i><div class="now-banner__body">
    <div class="now-banner__headline"><h1>Explorer is reading policy_engine.py</h1><span class="now-banner__elapsed">2m 04s</span></div>
    <p class="now-banner__act">Turn 3 · <code>Read policy_engine.py</code></p>
    <div class="now-banner__progress"><div><i style="width:40%"></i></div><span>3 of 12 turns · pipeline</span></div>
  </div><div class="now-banner__actions"><button class="ui-button">Watch log</button><button class="ui-button ui-button--danger">Stop run</button></div></div>`

const STRIP = `
  <div class="session-strip"><div class="session-strip__id"><strong>Session</strong><span>meta · sub</span></div>
    <button class="session-delete">Delete session</button>
    <div class="surface-tabs"><button class="is-active">Timeline</button><button>Session log</button></div></div>`

// Two consoles, side by side in the same document: the rail open (carrying the
// hide button) and the rail hidden (carrying the show toggle). Measuring both
// at once is what makes the round-trip assertion possible.
const FIXTURE = `<!doctype html><html><head><link rel="stylesheet" href="file://${CSS}">
<style>html,body{margin:0;height:100%}.probe{display:flex;height:100vh;width:100vw;font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:13px}</style>
</head><body>
<div class="probe"><div class="orchestrator-console" id="open">
  <aside class="session-rail"><header>
    <div class="session-rail__title"><button id="hide" aria-label="Hide sessions"><svg viewBox="0 0 24 24" width="15" height="15" fill="none"><path d="M5 5 V19" stroke="currentColor" stroke-width="1.7"/><path d="M14 8 L10 12 L14 16" stroke="currentColor" stroke-width="1.7"/></svg></button><strong>Sessions</strong></div>
    <div class="session-rail__meta"><span>4</span><button class="session-clean">Clean all</button></div>
  </header><div class="session-rail__list"></div></aside>
  <main class="orchestrator-workspace">${BANNER}${STRIP}<section class="runtime-evidence"></section></main>
  <aside class="conversation-panel"></aside>
</div></div>
<div class="probe"><div class="orchestrator-console sessions-hidden" id="hidden">
  <main class="orchestrator-workspace">
    <button class="rail-toggle" id="show" aria-label="Show sessions"><svg viewBox="0 0 24 24" width="15" height="15" fill="none"><path d="M5 5 V19" stroke="currentColor" stroke-width="1.7"/><path d="M10 8 L14 12 L10 16" stroke="currentColor" stroke-width="1.7"/></svg></button>
    ${BANNER}${STRIP}<section class="runtime-evidence"></section></main>
  <aside class="conversation-panel"></aside>
</div></div>
<pre id="probe-out"></pre>
<script>
const rel = (el, hostId) => {
  const r = el.getBoundingClientRect(), h = document.getElementById(hostId).getBoundingClientRect()
  return { x1: r.left - h.left, x2: r.right - h.left, y1: r.top - h.top, y2: r.bottom - h.top,
           cx: r.left - h.left + r.width / 2, cy: r.top - h.top + r.height / 2, w: r.width, h: r.height }
}
const rail = document.querySelector('#open .session-rail').getBoundingClientRect()
const header = document.querySelector('#open .session-rail header')
const svg = document.querySelector('#hide svg').getBoundingClientRect()
const btn = document.getElementById('hide').getBoundingClientRect()
document.getElementById('probe-out').textContent = 'PROBE' + JSON.stringify({
  hide: rel(document.getElementById('hide'), 'open'),
  show: rel(document.getElementById('show'), 'hidden'),
  dot: rel(document.querySelector('#hidden .live-dot'), 'hidden'),
  headerSpill: header.scrollWidth - header.clientWidth,
  railWidth: rail.width,
  glyphOffsets: { l: svg.left - btn.left, r: btn.right - svg.right, t: svg.top - btn.top, b: btn.bottom - svg.bottom },
}) + 'PROBE'
</script></body></html>`

function measure(width) {
  const dir = mkdtempSync(join(tmpdir(), 'musubi-geom-'))
  const page = join(dir, 'probe.html')
  writeFileSync(page, FIXTURE)
  const dom = execFileSync(chrome, [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
    `--window-size=${width},900`, '--virtual-time-budget=3000', '--dump-dom', `file://${page}`,
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 32 * 1024 * 1024, timeout: 30_000 })
  const match = dom.match(/PROBE(\{.*\})PROBE/s)
  assert.ok(match, 'probe page did not report measurements')
  return JSON.parse(match[1])
}

test('hiding and showing the sessions rail round-trips to one coordinate', { skip: !chrome && 'no chromium found' }, () => {
  for (const width of [1600, 1100]) {
    const m = measure(width)
    // The hide button unmounts with the rail; the toggle that replaces it has
    // to be where the hand already is. It used to arrive 108px lower.
    assert.ok(Math.abs(m.hide.cx - m.show.cx) <= 4, `x drift at ${width}px: ${m.hide.cx} vs ${m.show.cx}`)
    assert.ok(Math.abs(m.hide.cy - m.show.cy) <= 4, `y drift at ${width}px: ${m.hide.cy} vs ${m.show.cy}`)
  }
})

test('the rail header fits its column at every width', { skip: !chrome && 'no chromium found' }, () => {
  for (const width of [1600, 1100]) {
    const m = measure(width)
    // At 1100px the rail is 58px. Clean all used to push the header to 85.4px
    // and paint the hide button over the Now banner.
    assert.equal(m.headerSpill, 0, `header overflows its column by ${m.headerSpill}px at ${width}px`)
    assert.ok(m.hide.x2 <= m.railWidth, `hide button escapes the rail at ${width}px`)
  }
})

test('the show toggle clears the Now banner it sits beside', { skip: !chrome && 'no chromium found' }, () => {
  const m = measure(1600)
  const overlaps = !(m.show.x2 <= m.dot.x1 || m.dot.x2 <= m.show.x1
    || m.show.y2 <= m.dot.y1 || m.dot.y2 <= m.show.y1)
  assert.equal(overlaps, false, 'the rail toggle overlaps the banner live dot')
})

test('panel icons sit centred in their 28px box', { skip: !chrome && 'no chromium found' }, () => {
  const { glyphOffsets: g } = measure(1600)
  // Without flex centring the glyph rides the text baseline: 5px above, 8px
  // below, and a 7/6 split across.
  assert.ok(Math.abs(g.l - g.r) < 0.51, `glyph off horizontal centre: ${g.l} / ${g.r}`)
  assert.ok(Math.abs(g.t - g.b) < 0.51, `glyph off vertical centre: ${g.t} / ${g.b}`)
})
