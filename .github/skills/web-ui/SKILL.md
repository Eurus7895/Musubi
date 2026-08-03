---
name: web-ui
version: 1.0.0
description: Build the presentation layer of a browser artifact — semantic HTML, modern CSS layout, vanilla-JS interactivity, and embedded charts. Use when the user asks for an HTML page, dashboard, report, landing page, or chart. Covers how it looks and behaves, NOT where its data comes from; pair it with a data-source decision when the deliverable needs live or remote data.
completion-contract:
  required-output-fields: [summary, artifacts]
  required-check-types: [file_created_or_modified]
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - html
  - css
  - dashboard
  - landing page
  - chart
  - graph
  - responsive
  - accessibility
  - svg
  - dom
  - frontend
  - web page
tools:
  - musubi_write_file
  - musubi_append_file
---

## Purpose

Produce a browser-rendered artifact that looks intentional, works on a phone
and a laptop, and is usable with a keyboard and a screen reader. This is a
sibling of the `typescript` skill: that one is about app code in a JS/TS
project; this one is about the HTML/CSS/vanilla-JS artifact itself, which is
often generated from a non-JS workspace (a Python repo emitting a dashboard).

**Scope boundary.** This skill covers presentation: markup, layout,
interaction, accessibility. It does NOT decide where the data comes from. If
the deliverable needs live or remote data, that half of the task is not
answered here — say so rather than quietly building something that renders
placeholder data and cannot fetch anything.

## Procedure

### 1. Decide what kind of artifact this is

Ask first: **does the deliverable need data the file does not already have?**
Answer that before choosing a file layout — it is the branch that decides
whether "self-contained" is even available.

- **Needs live or remote data** (a weather view, a status board reading an
  API, anything the user expects to be current) → this is an application, not
  a one-off artifact. Self-contained does NOT apply: it will make network
  requests. Keep the markup/CSS guidance below, and additionally:
  - fetch at runtime; never hard-code a snapshot and present it as live,
  - never put a secret or API key in client-side code — if the source needs
    one, that is a blocker to report, not something to inline,
  - handle the request failing: loading state, error state, and what the user
    sees when the source is unreachable.
- **One-off artifact from data it already has** (dashboard, report, landing
  page rendered from local values) → **one `.html` file** with inline
  `<style>` and `<script>`. No build step, no external requests the viewer's
  network might block.
- **Part of an existing site/app** → match its asset structure and framework;
  do not inline a second styling system beside the project's.

If the brief names a deliverable that fits none of these, that is a signal the
pushed skill does not fit the task — report it with
`musubi_report_skill_mismatch` and keep going with the closest branch.

### 2. Structure with semantic HTML

- Start every document with `<!DOCTYPE html>`, `<html lang="…">`,
  `<meta charset="utf-8">`, and
  `<meta name="viewport" content="width=device-width, initial-scale=1">`.
  The viewport meta is what makes a page responsive at all — never omit it.
- Use landmarks: `<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`.
  One `<h1>` per page; heading levels descend without skipping.
- Controls are real elements: `<button>` for actions, `<a href>` for
  navigation, `<label>` bound to every input. Never a clickable `<div>`.

### 3. Layout with modern CSS

- Flexbox for one-dimensional rows/columns; Grid for two-dimensional page
  and card layouts. Avoid absolute positioning for layout.
- Make it fluid by default: `max-width` + `margin-inline:auto` for the
  content column; `min()/max()/clamp()` for type and spacing; percentage or
  `fr` tracks over fixed pixels.
- Add breakpoints only where the layout actually breaks (`@media
  (max-width: …)`), not at device-specific widths.
- Respect the viewer: honor `prefers-color-scheme` for dark/light and
  `prefers-reduced-motion` before adding animation.

### 4. Interactivity in vanilla JS

- Query with `document.querySelector`; attach `addEventListener`, never
  inline `onclick`. Keep state in one plain object and re-render from it.
- Guard every DOM lookup that can miss; escape any user/data-derived string
  before inserting it as HTML (`textContent`, not `innerHTML`, for text).
- No framework or bundler for a self-contained artifact — it must open from
  `file://` with no server. This applies to the self-contained branch only;
  an app that fetches live data is expected to make network requests, and
  `fetch` with an error path is the right tool there.

### 5. Charts and data viz

- Prefer inline **SVG** for small, static charts — it is self-contained,
  scales crisply, and needs no library.
- If a charting library is genuinely warranted in a self-contained artifact,
  inline the library source rather than linking a CDN a strict viewer will
  block. State this tradeoff; do not silently add a remote `<script src>`.
- Every chart has a text alternative: a caption, a `<title>`/`<desc>` in the
  SVG, or an adjacent data table. A color-only distinction fails
  colorblind users — pair color with shape, label, or pattern.

### 6. Accessibility floor (non-negotiable)

- Color contrast ≥ 4.5:1 for body text against its background.
- Every `<img>` has `alt`; decorative images use `alt=""`.
- Visible focus states on all interactive elements; the full flow is
  operable with Tab/Enter/Space alone.
- Reach for a native element before an ARIA role; a correct `<button>`
  beats `role="button"` plus keyboard handlers.

### 7. Verify by inspection, not by dumping

- Confirm the file exists and its size is plausible; check the first and
  last lines close their tags. Do not print the whole artifact to "read" it.
- Sanity-check the responsive contract: the viewport meta is present and no
  fixed pixel width exceeds a phone viewport.

## Anti-patterns

- A wall of `<div>`s with click handlers instead of semantic controls.
- Fixed-pixel widths that force horizontal scrolling on a phone.
- A CDN `<link>`/`<script>` in a file that was supposed to be
  self-contained — it breaks the moment the viewer is offline or sandboxed.
- Rendering hard-coded sample data in a view the user asked to be live, and
  not saying so. If the data source is unavailable to you, that is a blocker
  to report — a page that looks current but is frozen is worse than one that
  says it could not reach its source.
- Putting an API key, token, or credential in client-side code. It is visible
  to anyone who opens the page.
- Conveying meaning with color alone (red/green status with no label).
