---
name: typescript
description: Idiomatic TypeScript and modern JavaScript patterns — strict typing, ES modules, async, React function components, jest. Use when the user is writing TypeScript or JavaScript — tsconfig, package.json, React components, Node scripts, or jest tests.
applies-to:
  languages: [typescript, javascript]
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - typescript
  - tsx
  - react
  - node
  - npm
  - eslint
  - jest
  - package.json
---

## Purpose

Produce TypeScript / modern JavaScript that passes `tsc --noEmit` under
`strict`, eslint, and jest without modification, and that matches the
host project's module system and React conventions instead of
introducing a second style.

## Procedure

### Before writing: read the project's dialect

1. `tsconfig.json` — `strict`?, `target`, `module`, path aliases.
   Match them; never weaken `strict` to make code compile.
2. `package.json` — `"type": "module"` (ESM, use `import`) vs CommonJS
   (`require`). Scripts block shows the canonical build/test/lint
   commands — use those, not ad-hoc invocations.
3. One sibling file — copy its import ordering, quote style, and
   export shape (named vs default) exactly.

### Typing

- Annotate exported function signatures; let inference handle locals.
- `unknown` over `any` at every untrusted boundary (JSON parses, API
  responses), then narrow with a type guard before use:

```typescript
function isSession(v: unknown): v is Session {
  return typeof v === "object" && v !== null && "sessionId" in v;
}
```

- Model variants as discriminated unions, not optional-field soup:

```typescript
type StageResult =
  | { status: "ok"; output: string }
  | { status: "failed"; error: string };
```

- `interface` for object shapes that may be extended; `type` for
  unions, intersections, and function types. `as` casts are a last
  resort and get a comment saying why the compiler can't see it.

### Async

- `async/await` over raw `.then()` chains; never mix both in one flow.
- Every awaited call that can reject is either caught where the error
  can be *handled* or deliberately propagated — no empty `catch`.
- Independent awaits run concurrently: `await Promise.all([a(), b()])`
  instead of sequential awaits.
- No floating promises: an unawaited call is a bug unless explicitly
  `void`-ed with a reason.

### React (function components only)

- Props typed with an explicit interface; destructure in the signature.
- Hooks at the top level, never conditional. Effect dependency arrays
  are complete — silencing the exhaustive-deps lint hides a stale
  closure bug.
- Derive state where possible; `useState` only for what the user can
  change. Lift state up before reaching for context.
- Lists render with stable `key`s (ids, not array indexes).

### Testing (jest)

- Name pattern mirrors the harness convention:
  `it("rejects an empty session id", ...)` — behaviour, not method
  names.
- Mock at the module boundary (`jest.mock("./client")`), not deep
  internals; assert on observable output, not on call counts alone.
- Async tests `await` their assertions — a test that resolves before
  its expectation runs always passes.

## Anti-patterns

- `// @ts-ignore` / `eslint-disable` without a same-line reason.
- Default exports for shared utilities — named exports keep renames
  greppable (see the refactoring skill).
- Adding a dependency for something the stdlib/platform provides
  (`fetch`, `URL`, `structuredClone`).
