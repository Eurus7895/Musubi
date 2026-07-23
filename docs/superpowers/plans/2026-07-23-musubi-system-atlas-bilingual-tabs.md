# Musubi System Atlas Bilingual Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the standalone Musubi system atlas into a complete Vietnamese/English experience with accessible language tabs, technical-term explanations, and preserved interaction state.

**Architecture:** Keep one live atlas DOM. Add a flat inline translation catalog whose entries have `vi` and `en` values, mark static nodes and translated attributes with stable keys, and route generated trace/quiz/drawer copy through one `t()` function. A language controller updates the DOM in place, rerenders language-sensitive views from stable IDs, updates accessibility state, and persists only the selected language.

**Tech Stack:** Standalone HTML5, CSS, vanilla JavaScript, Node.js built-ins for deterministic verification, in-app browser for runtime and visual QA.

## Global Constraints

- Keep `artifacts/musubi-system-atlas.html` as one offline, build-free artifact.
- Default to `vi`; store the valid values `vi` or `en` under `musubi-system-atlas.language.v1`.
- Preserve source paths, API names, symbols, code literals, hashes, dates, CLI flags, database names, and invariant IDs.
- Keep existing map, filter, trace, drawer, quiz, reduced-motion, and responsive behavior.
- Switching language must not reset map mode, filters, selected component, trace scenario/step, drawer state, quiz scope, or quiz answers.
- Vietnamese prose retains canonical technical terms only where precision requires it and explains them at first prominent use plus in the glossary.
- English reader-facing copy must not require Vietnamese text for comprehension.
- Do not add dependencies, network calls, or a build step.

---

### Task 1: Add a deterministic bilingual-contract verifier

**Files:**
- Create: `scripts/verify_musubi_system_atlas_i18n.mjs`
- Test: `artifacts/musubi-system-atlas.html`

**Interfaces:**
- Consumes: the atlas HTML and the JSON object in `<script id="atlas-i18n" type="application/json">`.
- Produces: a process exit code and a summary line; exit `0` means the structural bilingual contract is satisfied.

- [ ] **Step 1: Write the failing verifier**

Create a Node.js script using only `node:assert/strict` and `node:fs`. It must:

```js
const html = readFileSync(new URL('../artifacts/musubi-system-atlas.html', import.meta.url), 'utf8');
const catalogMatch = html.match(/<script id="atlas-i18n" type="application\/json">([\s\S]*?)<\/script>/);
assert.ok(catalogMatch, 'missing #atlas-i18n catalog');
const catalog = JSON.parse(catalogMatch[1]);
const keys = [...html.matchAll(/data-i18n(?:-[a-z-]+)?="([^"]+)"/g)].map(match => match[1]);
for (const key of keys) {
  assert.ok(catalog[key], `missing catalog key: ${key}`);
  assert.equal(typeof catalog[key].vi, 'string', `${key}.vi must be a string`);
  assert.equal(typeof catalog[key].en, 'string', `${key}.en must be a string`);
  assert.ok(catalog[key].vi.trim(), `${key}.vi must not be empty`);
  assert.ok(catalog[key].en.trim(), `${key}.en must not be empty`);
}
assert.match(html, /role="tablist"/);
assert.equal((html.match(/role="tab"/g) || []).length, 2);
assert.match(html, /id="language-vi"/);
assert.match(html, /id="language-en"/);
assert.match(html, /const LANGUAGE_STORAGE_KEY = 'musubi-system-atlas\.language\.v1'/);
assert.match(html, /function setLanguage\(language/);
const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map(match => match[1]);
assert.equal(new Set(ids).size, ids.length, 'duplicate HTML ids');
console.log(`atlas i18n contract: ${keys.length} keyed fields, ${Object.keys(catalog).length} catalog entries`);
```

Also assert that the glossary and both no-JavaScript language fallbacks exist:

```js
assert.match(html, /id="glossary"/);
assert.match(html, /data-noscript-language="vi"/);
assert.match(html, /data-noscript-language="en"/);
```

- [ ] **Step 2: Run the verifier and confirm RED**

Run:

```powershell
node scripts/verify_musubi_system_atlas_i18n.mjs
```

Expected: non-zero exit with `AssertionError: missing #atlas-i18n catalog`.

- [ ] **Step 3: Commit the failing verifier**

```powershell
git add scripts/verify_musubi_system_atlas_i18n.mjs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "test(atlas): define bilingual content contract"
```

### Task 2: Implement the language shell and controller

**Files:**
- Modify: `artifacts/musubi-system-atlas.html:1-125`
- Modify: `artifacts/musubi-system-atlas.html:526-836`
- Test: `scripts/verify_musubi_system_atlas_i18n.mjs`

**Interfaces:**
- Consumes: catalog entries shaped as `{ vi: string, en: string }`, static elements marked with `data-i18n`, and translated attributes marked with `data-i18n-<attribute>`.
- Produces: `t(key, variables = {})`, `setLanguage(language, persist = true)`, and `state.language`.

- [ ] **Step 1: Add failing controller assertions**

Extend the verifier with:

```js
assert.match(html, /const SUPPORTED_LANGUAGES = new Set\(\['vi', 'en'\]\)/);
assert.match(html, /function t\(key, variables = \{\}\)/);
assert.match(html, /document\.documentElement\.lang = state\.language/);
assert.match(html, /renderLanguageSensitiveViews\(\)/);
assert.match(html, /localStorage\.setItem\(LANGUAGE_STORAGE_KEY, state\.language\)/);
```

- [ ] **Step 2: Run the verifier and confirm RED**

Run:

```powershell
node scripts/verify_musubi_system_atlas_i18n.mjs
```

Expected: non-zero exit at the first missing controller assertion.

- [ ] **Step 3: Add the accessible tab shell**

Insert before the atlas title:

```html
<div class="language-switcher">
  <span id="language-label" data-i18n="language.label">Ngôn ngữ</span>
  <div class="language-tabs" role="tablist" aria-labelledby="language-label">
    <button id="language-vi" role="tab" type="button" data-language="vi"
      aria-selected="true" tabindex="0">Tiếng Việt</button>
    <button id="language-en" role="tab" type="button" data-language="en"
      aria-selected="false" tabindex="-1">English</button>
  </div>
</div>
```

Add CSS for a visible selected state, focus preservation, wrapping on narrow viewports, and the existing `44px` minimum target size. The selected state must use border, background, and `aria-selected`, not color alone.

- [ ] **Step 4: Add the catalog parser and language controller**

Add:

```js
const LANGUAGE_STORAGE_KEY = 'musubi-system-atlas.language.v1';
const SUPPORTED_LANGUAGES = new Set(['vi', 'en']);
const I18N = JSON.parse(document.getElementById('atlas-i18n').textContent);

function t(key, variables = {}) {
  const entry = I18N[key];
  const template = entry?.[state.language] ?? entry?.vi ?? key;
  return Object.entries(variables).reduce(
    (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
    template
  );
}
```

Implement `setLanguage(language, persist = true)` to:

1. Normalize unsupported values to `vi`.
2. Set `state.language`, `<html lang>`, and `document.title`.
3. Replace `textContent` for `[data-i18n]`.
4. Replace `innerHTML` only for `[data-i18n-html]`.
5. Update the explicit attributes `aria-label`, `placeholder`, `title`, and `content` for matching `data-i18n-*` markers.
6. Update both tab buttons' `aria-selected` and `tabindex`.
7. Call `renderLanguageSensitiveViews()`.
8. Save the valid language inside a guarded `try/catch` only when `persist` is true.

Initialize the saved language after existing state and event listeners are ready. Invalid storage, unavailable storage, and malformed values must resolve to `vi` without throwing.

- [ ] **Step 5: Add keyboard behavior**

On the tab list, handle `ArrowLeft`, `ArrowRight`, `Home`, and `End`. With two tabs, arrows wrap. Each supported key both focuses and activates its destination. A click activates the clicked language.

- [ ] **Step 6: Run the verifier and confirm GREEN for the shell**

Run:

```powershell
node scripts/verify_musubi_system_atlas_i18n.mjs
```

Expected: the controller assertions pass; any remaining failure points to untranslated catalog coverage added in Task 3.

### Task 3: Translate all static and generated reader-facing copy

**Files:**
- Modify: `artifacts/musubi-system-atlas.html:105-525`
- Modify: `artifacts/musubi-system-atlas.html:526-836`
- Test: `scripts/verify_musubi_system_atlas_i18n.mjs`

**Interfaces:**
- Consumes: `t(key, variables)`, stable component/scenario/question IDs, and the current `state.language`.
- Produces: complete `vi` and `en` copy for static markup, component metadata, map labels, trace data, quiz data, drawer messages, and live regions.

- [ ] **Step 1: Strengthen coverage assertions and confirm RED**

Require keys for every translated surface:

```js
for (const requiredPrefix of [
  'document.', 'language.', 'orientation.', 'nav.', 'map.', 'components.',
  'trace.', 'invariants.', 'economics.', 'evolution.', 'glossary.', 'quiz.',
  'drawer.', 'a11y.', 'noscript.'
]) {
  assert.ok(Object.keys(catalog).some(key => key.startsWith(requiredPrefix)),
    `missing catalog area: ${requiredPrefix}`);
}
assert.ok(keys.length >= 120, `expected broad static coverage, found ${keys.length}`);
```

Add checks that `TRACE_SCENARIOS_I18N` and `QUIZ_QUESTIONS_I18N` exist and contain both language properties for every reader-facing field.

Run the verifier and expect failure on the first missing area or coverage threshold.

- [ ] **Step 2: Translate the orientation, navigation, and map**

Mark all headings, prose, controls, table headers, SVG `title`/`desc`, edge labels, and accessibility labels with catalog keys. Keep component IDs, code, hashes, dates, and evidence paths literal.

The first Vietnamese orientation paragraph must use these explanations:

```text
driver (lớp điều phối có quyền gọi model)
trust boundary (ranh giới tin cậy)
fail-closed (mặc định từ chối khi thiếu quyền rõ ràng)
governance substrate (lớp quản trị deterministic, không gọi LLM)
```

- [ ] **Step 3: Translate components and maintainer evidence**

Move each component's reader-facing metadata into `COMPONENT_I18N[componentId]`:

```js
{
  title: { vi: 'CLI host', en: 'CLI host' },
  summary: {
    vi: 'Điểm vào standalone cho direct worker và pipeline do operator chọn.',
    en: 'Standalone entry point for direct workers and operator-selected pipelines.'
  },
  responsibility: {
    vi: 'Parse lệnh và khởi chạy execution mode rõ ràng.',
    en: 'Parse commands and launch an explicit execution mode.'
  },
  why: {
    vi: 'Một host duy nhất giữ semantics khởi chạy nhất quán.',
    en: 'One host keeps launch semantics consistent.'
  },
  inputs: { vi: 'argv và profile', en: 'argv and profile' },
  outputs: { vi: 'direct run hoặc pipeline run', en: 'direct run or pipeline run' },
  calledBy: { vi: 'operator', en: 'operator' },
  dependsOn: { vi: 'driver configuration', en: 'driver configuration' },
  enforces: { vi: 'explicit operator launch', en: 'explicit operator launch' },
  failureModes: {
    vi: 'Lệnh hoặc recipe sai bị từ chối trước model call.',
    en: 'Invalid commands or recipes are rejected before a model call.'
  },
  economics: {
    vi: 'Parse local không tốn token.',
    en: 'Local parsing consumes no tokens.'
  },
  trustZone: { vi: 'model-calling driver', en: 'model-calling driver' },
  durability: { vi: 'bền vững', en: 'durable' },
  expiresWhen: { vi: 'không bao giờ', en: 'never' },
  costLever: { vi: 'không áp dụng', en: 'not applicable' }
}
```

On language change, update the card title/summary and its `dataset` values before rerendering the maintainer record, relationship state, filters, and evidence drawer.

- [ ] **Step 4: Translate trace and quiz datasets**

Keep IDs, correct-answer indexes, component references, evidence references, difficulty IDs, and section IDs stable. Give every reader-facing field a `{ vi, en }` pair. `renderTrace`, `renderQuiz`, `answerQuestion`, and `resetQuiz` must use the active language while retaining `state.scenario`, `state.traceStep`, `state.quizScope`, and `state.quiz.answers`.

- [ ] **Step 5: Add the bilingual glossary**

Add `#glossary` before `#quiz`, link it from navigation, and define at least:

```text
driver
worker
trust boundary
governance substrate
fail-closed
evaluator firewall
append-only
durable
ephemeral
projection
```

Each definition has complete Vietnamese and English catalog values. The Vietnamese definitions explain retained English terms rather than replacing their canonical names.

- [ ] **Step 6: Add complete no-JavaScript fallbacks**

Keep two labeled fallback sections:

```html
<section data-noscript-language="vi" lang="vi">
  <h2>Nội dung cốt lõi khi JavaScript bị tắt</h2>
  <p>Driver suy luận; Musubi kiểm soát môi trường thực thi.</p>
</section>
<section data-noscript-language="en" lang="en">
  <h2>Core content when JavaScript is disabled</h2>
  <p>The driver reasons; Musubi controls the execution environment.</p>
</section>
```

Each includes the mental model, trust/lifecycle distinction, core runtime flow, evidence interpretation, glossary, and quiz answer guidance in its own language.

- [ ] **Step 7: Run deterministic verification**

Run:

```powershell
node scripts/verify_musubi_system_atlas_i18n.mjs
```

Expected: exit `0` and a summary reporting at least `120` keyed fields with no duplicate IDs or missing bilingual entries.

- [ ] **Step 8: Commit the complete translation**

```powershell
git add artifacts/musubi-system-atlas.html scripts/verify_musubi_system_atlas_i18n.mjs
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(atlas): add bilingual language tabs"
```

### Task 4: Browser, accessibility, and state-retention verification

**Files:**
- Modify if a failing browser check requires a fix: `artifacts/musubi-system-atlas.html`
- Test: `scripts/verify_musubi_system_atlas_i18n.mjs`

**Interfaces:**
- Consumes: the completed standalone atlas.
- Produces: fresh deterministic output and browser evidence for both languages and responsive layouts.

- [ ] **Step 1: Serve the workspace locally**

Run:

```powershell
.\.venv\Scripts\python.exe -m http.server 8765 --directory C:\Workspace\Projects\Musubi
```

Expected: the server listens on `http://127.0.0.1:8765`.

- [ ] **Step 2: Verify language and state behavior in the browser**

Open `/artifacts/musubi-system-atlas.html` and verify:

1. Fresh storage opens Vietnamese and `<html lang="vi">`.
2. Selecting English updates visible static text, generated trace/quiz/drawer text, SVG accessible text, document title, and `<html lang="en">`.
3. Reload retains English.
4. Selecting a component, choosing control map mode, moving to a later trace step, changing quiz scope, and answering a question all survive switching `en → vi → en`.
5. Arrow keys, Home, and End move and activate language tabs.
6. Mobile navigation and evidence drawers still open, close, restore focus, and respond to Escape.
7. No browser console error appears.

- [ ] **Step 3: Perform visual checks**

Capture or inspect desktop (`1440×1000`) and narrow (`390×844`) layouts in both languages. Confirm the tab strip wraps safely, translated labels do not overlap, the architecture map remains horizontally scrollable, and focus indicators remain visible.

- [ ] **Step 4: Run fresh final verification**

Run:

```powershell
node scripts/verify_musubi_system_atlas_i18n.mjs
git diff --check
git status --short
```

Expected: verifier exit `0`, no whitespace errors, and only the known unrelated `vietnam-weather.html` remains untracked after the feature commit.
