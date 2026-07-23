import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const atlasUrl = new URL('../artifacts/musubi-system-atlas.html', import.meta.url);
const html = readFileSync(atlasUrl, 'utf8');

const catalogMatch = html.match(
  /<script id="atlas-i18n" type="application\/json">([\s\S]*?)<\/script>/,
);
assert.ok(catalogMatch, 'missing #atlas-i18n catalog');

const catalog = JSON.parse(catalogMatch[1]);
const keyedFields = [
  ...html.matchAll(/data-i18n(?:-[a-z-]+)?="([^"]+)"/g),
].map((match) => match[1]);

for (const key of keyedFields) {
  assert.ok(catalog[key], `missing catalog key: ${key}`);
  assert.equal(typeof catalog[key].vi, 'string', `${key}.vi must be a string`);
  assert.equal(typeof catalog[key].en, 'string', `${key}.en must be a string`);
  assert.ok(catalog[key].vi.trim(), `${key}.vi must not be empty`);
  assert.ok(catalog[key].en.trim(), `${key}.en must not be empty`);
}

for (const requiredPrefix of [
  'document.',
  'language.',
  'orientation.',
  'nav.',
  'map.',
  'components.',
  'trace.',
  'invariants.',
  'economics.',
  'evolution.',
  'glossary.',
  'quiz.',
  'drawer.',
  'a11y.',
  'noscript.',
]) {
  assert.ok(
    Object.keys(catalog).some((key) => key.startsWith(requiredPrefix)),
    `missing catalog area: ${requiredPrefix}`,
  );
}
assert.ok(
  keyedFields.length >= 120,
  `expected broad static coverage, found ${keyedFields.length}`,
);
assert.match(html, /const TRACE_SCENARIOS_I18N = /);
assert.match(html, /const QUIZ_QUESTIONS_I18N = /);

assert.match(html, /role="tablist"/);
assert.equal(
  (html.match(/<button\b[^>]*\brole="tab"[^>]*>/g) || []).length,
  2,
);
assert.match(html, /id="language-vi"/);
assert.match(html, /id="language-en"/);
assert.match(
  html,
  /const LANGUAGE_STORAGE_KEY = 'musubi-system-atlas\.language\.v1'/,
);
assert.match(html, /const SUPPORTED_LANGUAGES = new Set\(\['vi', 'en'\]\)/);
assert.match(html, /function t\(key, variables = \{\}\)/);
assert.match(html, /function setLanguage\(language/);
assert.match(html, /document\.documentElement\.lang = state\.language/);
assert.match(html, /renderLanguageSensitiveViews\(\)/);
assert.match(
  html,
  /localStorage\.setItem\(LANGUAGE_STORAGE_KEY, state\.language\)/,
);
assert.match(html, /id="glossary"/);
assert.match(html, /data-noscript-language="vi"/);
assert.match(html, /data-noscript-language="en"/);

const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
assert.equal(new Set(ids).size, ids.length, 'duplicate HTML ids');

console.log(
  `atlas i18n contract: ${keyedFields.length} keyed fields, `
  + `${Object.keys(catalog).length} catalog entries`,
);
