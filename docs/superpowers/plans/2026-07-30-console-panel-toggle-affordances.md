# Console panel toggle affordances

## Context

Four defects in the Orchestrator console, all of them geometry, and one of
them a live rendering fault.

**The rail toggle does not round-trip.** The hide control lives in
`.session-rail header` (`Orchestrator.jsx`), a 48 px row at the top of the
rail's grid column, so its centre is at **(213, 23.5)** measured from the
console's top-left. Hiding the rail unmounts that header, and the replacement
show control rendered inside `.session-strip` — the sibling *after*
`<NowBanner>`. Its y is therefore a function of the banner's height, and the
banner has two heights: **105.5 px** running, **53.0 px** idle. The show
control landed at **(36, 132)** during a run and **(36, 79.5)** at rest. The
gesture closed the rail in one place and reopened it 108.5 px lower — or
56 px lower, depending on data unrelated to the gesture.

**The arrow characters said direction, not subject.** Four panel controls used
a bare `←` / `→`. Two of them — hide the left rail, collapse the right panel —
pointed opposite ways while doing the same kind of thing, and two pointed the
same way while doing opposite things.

**Add folder and Remove had no affordance.** `.session-folders button`
declared border, background, colour and padding but neither `cursor: pointer`
nor `:hover` — the only button group in the console with neither, which is why
both read as dead labels. Three further groups had a pointer but no hover,
including `.pause-panel__actions`, the one place an operator commits an
irreversible decision on a halted pipeline.

**The rail header overflowed its own column.** Below 1180 px the rail narrows
to 58 px and the media query hid the label and the count — but not "Clean all".
Header content measured **85.4 px** inside **46 px** of usable width, so the
header overflowed by **30.3 px** and painted the hide button directly on top of
the Now banner's pulsing live dot, with "Clean all" clipped off the left edge
of the window.

None of this could fail a test: the console's suite reads `Orchestrator.jsx`
as a string and asserts on substrings, and a substring cannot carry a
coordinate.

## Goal

Give hide and show one coordinate, give the four panel controls an icon that
names both the edge and the direction, give every button group a hover state,
keep the rail header inside its column at every width, and add a layout-aware
test so the next regression fails a check instead of an eye.

## Tech stack

- React view (`gui/src/views/Orchestrator.jsx`)
- Global stylesheet and `:root` token layer (`gui/src/index.css`)
- `node:test` driving headless Chromium over a DOM fixture for geometry

## Steps

1. Move the hide button to the head of `.session-rail header`, wrapped with the
   label in `.session-rail__title`, and group the count and Clean all in
   `.session-rail__meta`. This puts hide at (26, 23.5).
2. Render the show toggle as an absolutely positioned child of
   `.orchestrator-workspace` at `top: 10px; left: 12px` — centre (26, 24), half
   a pixel from where hide was. Pad `.sessions-hidden .now-banner` so the
   toggle clears the banner's live dot.
3. Add `PanelIcon({ side, direction })`: a bar naming the edge the panel is on,
   a chevron naming the way it will move. Use it at all four panel controls.
   Leave `← Back to graph` and `Open Agent log →` as text — they point at a
   destination, not a panel edge.
4. Centre the glyph in its 28 px box. An inline `<svg>` rides the text
   baseline: 5 px above, 8 px below, and a 7/6 split across.
5. Promote the four hover literals already duplicated across the sheet to
   `:root` (`--raised-hi`, `--line-hover`, `--danger-hi`, `--danger-wash`), then
   give `.session-folders`, `.session-folder`, `.pause-panel__actions`,
   `.surface-tabs` / `.run-mode` / `.log-filters`, and `.runtime-detail__tabs`
   the hover state each was missing.
6. Hide `.session-rail__meta` below 1180 px and left-align the header, so the
   column fits and the hide button keeps its 12 px inset. Clean all is a
   destructive bulk action; losing it at narrow widths beats rendering it half
   off-screen.
7. Suppress the native steppers on `.builder-field input[type="number"]` —
   Pipeline Studio's correction-attempts field, the one number input the
   console actually renders.
8. Add `Orchestrator.geometry.test.mjs`: measure the fixture in headless
   Chromium at 1600 px and 1100 px and assert hide/show agree within 4 px, the
   header does not overflow its column, the toggle does not intersect the live
   dot, and the glyph is centred. Skip when no Chromium is present.

9. Give the collapsed conversation panel the header band it was dropping. Step 1
   fixed the sessions rail; the panel on the opposite edge had the same defect
   and kept it. `ConversationPanel`'s collapsed branch rendered the toggle as a
   bare child of `.conversation-panel.is-collapsed`, positioned by
   `padding-top: 14px` instead of by a header, so it sat at cy 28 against the
   expanded header's 23.5 and 26 px from the console's right edge against 23.5 —
   and the header rule that runs across the console stopped at the panel. The
   collapsed branch now renders the same `.conversation-panel__header`, with
   `justify-content: flex-end` because a single child under the shared
   `space-between` would park at the left edge. Measure the panel from the
   console's *right* edge: it is right-anchored and its column changes 420 px →
   48 px, so a left-relative comparison would call a fixed button moved.

## Result

Round-trip travel **207.6 px → 0.5 px** for the sessions rail and **5.2 px → 0**
for the conversation panel, identical at both breakpoints. Header overflow
**30.3 px → 0**. Presentation only: no substrate, policy, audit, `LMRouter`, or
Tauri command path is touched.
