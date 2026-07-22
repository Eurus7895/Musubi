# Musubi System Atlas Bilingual Tabs Design

## Context

`artifacts/musubi-system-atlas.html` is a standalone maintainer atlas with
progressively enhanced navigation, an interactive architecture map, component
filters, runtime traces, an evidence drawer, and a scored quiz. Its current
copy is primarily Vietnamese but mixes Vietnamese and English at sentence,
label, metadata, and runtime-message level. That makes both language paths
harder to scan and leaves no complete English reading mode.

The atlas must remain a single portable HTML artifact. Source paths, API names,
symbols, literal code, and canonical Musubi concepts must remain exact.

## Goal

Provide two accessible language tabs, `Tiếng Việt` and `English`, that switch
the whole atlas between complete Vietnamese and English presentations without
resetting any interactive state.

The Vietnamese presentation uses Vietnamese prose and controls while retaining
canonical technical terms where translation would reduce precision. Important
terms receive a short Vietnamese meaning at their first prominent use and a
full entry in a bilingual glossary.

## Non-goals

- Splitting the atlas into separate HTML files.
- Duplicating the complete application DOM for each language.
- Translating source paths, API names, identifiers, code literals, or Musubi
  invariant identifiers.
- Changing architecture claims, evidence, interactions, or visual encoding.
- Introducing a build step, external localization dependency, or network call.

## Chosen Approach

Use one interactive DOM with an inline language catalog. Stable translation
keys map each translatable field to Vietnamese and English values. The language
tabs update text, accessible names, metadata, SVG descriptions, and JavaScript
datasets through one language controller.

This is preferred over duplicating the DOM because duplicate panels would
nearly double an already large HTML file, create duplicate-ID hazards, and make
the map, drawer, trace, and quiz state difficult to synchronize. It is also
preferred over separate files because the requested experience is an in-page
tab switch and a single artifact is easier to distribute.

## User Experience

The language switch appears at the start of the orientation header, before the
atlas title and secondary controls. It is exposed as an accessible tab list:

- `Tiếng Việt` is the initial language when no saved preference exists.
- `English` presents all reader-facing prose and controls in English.
- The selected tab uses `aria-selected="true"` and is the only tab in the
  sequential tab order.
- Left/right arrow keys move and activate the adjacent language tab; Home and
  End activate the first and last tab.
- The chosen language is stored in `localStorage` when storage is available.
  Storage failure is non-fatal and leaves the current language active.
- Switching language updates `<html lang>` and the document title.
- Map mode, component filters, selected evidence, trace scenario and step, and
  quiz answers remain unchanged across a language switch.

## Language Rules

### Vietnamese

All sentences, headings, navigation labels, controls, status messages,
accessibility labels, trace narratives, quiz copy, and rendered component
metadata use Vietnamese grammar. Canonical technical terms may remain English
when they are identifiers or established Musubi vocabulary.

At the first prominent occurrence, important retained terms include a concise
meaning, for example:

- `trust boundary (ranh giới tin cậy)`
- `fail-closed (mặc định từ chối khi thiếu quyền rõ ràng)`
- `driver (lớp điều phối có quyền gọi model)`
- `governance substrate (lớp quản trị deterministic, không gọi LLM)`

Repeated inline annotations are avoided so the atlas stays readable. The
glossary provides the durable reference.

### English

All reader-facing prose is idiomatic English. Canonical source symbols and
Musubi terms remain unchanged. The glossary defines the same concepts in
English so both tabs are self-contained.

## Translation Coverage

The catalog covers every reader-visible string source:

1. Static HTML headings, paragraphs, tables, links, buttons, options, fallback
   content, and maintainer records.
2. Accessibility text: `aria-label`, SVG `title` and `desc`, focusable map-node
   labels, drawer labels, and live status messages.
3. JavaScript trace scenarios, trace-step fields, decision/failure messages,
   economics copy, and generated controls.
4. Quiz prompts, options, explanations, scoring summaries, correct/incorrect
   feedback, and section links.
5. Component metadata rendered into the evidence drawer, including
   responsibility, rationale, inputs, outputs, callers, dependencies,
   enforcement, failure modes, economics, trust zone, lifecycle, expiry, and
   cost lever.
6. The no-JavaScript fallback. Both complete language fallbacks remain
   available without scripting, with Vietnamese first and an English section
   immediately after it.

Proper names, source references, hashes, dates, command-line flags, database
names, code, and invariant IDs are not translated.

## Components

### Language tabs

Small semantic controls own selection and keyboard behavior. They do not own
atlas state beyond the active language.

### Translation catalog

An inline immutable object groups `vi` and `en` strings under stable keys.
Structured runtime datasets use the same identifiers in both languages so the
active scenario, component, and quiz answer can be retained when copy changes.

### Language controller

`setLanguage(language)` validates the requested language, updates static keyed
nodes and attributes, swaps the active runtime datasets, rerenders only the
language-sensitive UI, updates the tab state and document language, and then
persists the preference when possible.

### Glossary

A new glossary section sits before the quiz. It defines the core terms used in
the atlas and participates in normal navigation. Each language has a complete
definition set rather than a word-for-word parenthetical translation list.

## State and Data Flow

1. On startup, the controller reads the saved language; invalid or missing
   values resolve to `vi`.
2. Existing atlas state initializes as before.
3. The controller applies the chosen language and renders language-sensitive
   generated UI from datasets with stable IDs.
4. Selecting another tab calls `setLanguage` without recreating the map or
   resetting atlas state.
5. Generated trace and quiz views rerender using the existing scenario, step,
   scope, and answer IDs.
6. Hash navigation continues to target the same section IDs in both languages.

## Failure Handling

- An unknown language code falls back to Vietnamese.
- Missing translation keys retain the existing node content during runtime and
  are reported by the deterministic verification script; they must not ship.
- Unavailable or blocked `localStorage` does not prevent switching languages.
- JavaScript disabled: both labeled language fallback sections remain readable;
  interactive tab behavior is not advertised as functional.
- Translation changes do not alter evidence sources or architecture claims.

## Accessibility

- Use the WAI-ARIA tab interaction model for the two language controls.
- Keep visible focus indication and the existing 44-pixel minimum control size.
- Update `<html lang>` immediately so assistive technology uses the correct
  pronunciation rules.
- Translate accessible names together with visible labels.
- Preserve reduced-motion handling, drawer focus restoration, and existing
  keyboard interactions.
- Do not rely on color alone to show the selected language.

## Verification

Deterministic checks will verify:

- HTML parses and contains one tab list with exactly two tabs.
- Every translation key has non-empty `vi` and `en` values.
- Both languages cover static content, runtime messages, traces, quiz, evidence
  metadata, SVG descriptions, and accessibility labels.
- Language switching updates `html.lang`, tab ARIA state, document title, and
  visible text without changing existing atlas state IDs.
- No duplicate IDs are introduced.
- The page has no console errors in both language modes.
- Keyboard tab switching, drawer behavior, map selection, trace navigation, and
  quiz scoring work in both languages.
- A visual pass at desktop and narrow viewport confirms that the tab switch and
  translated copy do not overflow or obscure controls.

## Acceptance Criteria

1. A reader can choose `Tiếng Việt` or `English` from the top of the atlas.
2. No reader-facing sentence combines Vietnamese and English except deliberate
   canonical technical terms, identifiers, or short meaning annotations.
3. The Vietnamese tab explains retained technical terms and exposes a complete
   glossary.
4. The English tab is complete and does not depend on Vietnamese copy for
   comprehension.
5. Every interactive feature retains its state while switching languages.
6. The standalone atlas remains usable without network access or a build step.
7. The no-JavaScript fallback exposes the essential content in both languages.
