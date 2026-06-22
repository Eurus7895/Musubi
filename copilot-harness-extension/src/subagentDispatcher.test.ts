import { test } from "node:test";
import assert from "node:assert/strict";

import {
  decidePreSpawns,
  EXPLORER_BRIEF_MAX_CHARS,
  PER_FILE_REVIEW_MAX,
  PER_FILE_REVIEW_MIN_MODULES,
  renderExplorerBrief,
  renderReviewerAuxBrief,
  spliceResultsIntoContext,
  type DispatcherInput,
  type PreSpawnResult,
} from "./subagentDispatcher";

// ── decidePreSpawns: stage routing ────────────────────────────────────────

const baseInput: DispatcherInput = {
  stage: "coder",
  chunkFilePaths: [],
  chunkId: null,
  design: null,
  fileExistsOnDisk: new Map(),
  remainingBudget: 5,
};

test("decidePreSpawns: planner never pre-spawns", () => {
  const out = decidePreSpawns({
    ...baseInput, stage: "planner",
    chunkFilePaths: ["a.py", "b.py"],
    fileExistsOnDisk: new Map([["a.py", true], ["b.py", true]]),
  });
  assert.deepEqual(out, []);
});

test("decidePreSpawns: designer never pre-spawns", () => {
  const out = decidePreSpawns({
    ...baseInput, stage: "designer",
    chunkFilePaths: ["a.py"],
    fileExistsOnDisk: new Map([["a.py", true]]),
  });
  assert.deepEqual(out, []);
});

test("decidePreSpawns: zero budget short-circuits", () => {
  const out = decidePreSpawns({
    ...baseInput, stage: "coder",
    chunkFilePaths: ["a.py"],
    fileExistsOnDisk: new Map([["a.py", true]]),
    remainingBudget: 0,
  });
  assert.deepEqual(out, []);
});

// ── decidePreSpawns: coder explorer-scan heuristic ───────────────────────

test("coder: spawns explorer when at least one chunk file exists on disk", () => {
  const out = decidePreSpawns({
    ...baseInput, stage: "coder",
    chunkFilePaths: ["existing.py", "new.py"],
    chunkId: "T1",
    fileExistsOnDisk: new Map([["existing.py", true], ["new.py", false]]),
  });
  assert.equal(out.length, 1);
  assert.equal(out[0].role, "explorer");
  assert.equal(out[0].contextKey, "existing_callers_summary");
  assert.match(out[0].label, /T1/);
});

test("coder: skips explorer when chunk has no existing files (new-only chunk)", () => {
  const out = decidePreSpawns({
    ...baseInput, stage: "coder",
    chunkFilePaths: ["new.py", "another_new.py"],
    fileExistsOnDisk: new Map([["new.py", false], ["another_new.py", false]]),
  });
  assert.deepEqual(out, []);
});

test("coder: skips explorer when chunk has no files at all", () => {
  const out = decidePreSpawns({ ...baseInput, stage: "coder" });
  assert.deepEqual(out, []);
});

test("coder: explorer brief lists existing paths and public symbols", () => {
  const out = decidePreSpawns({
    ...baseInput, stage: "coder",
    chunkFilePaths: ["src/foo.py"],
    chunkId: "T1",
    fileExistsOnDisk: new Map([["src/foo.py", true]]),
    design: {
      modules: [
        {
          file: "src/foo.py",
          public_interface: [
            { name: "Foo", signature: "class Foo" },
            { name: "bar", signature: "def bar()" },
          ],
        },
      ],
    },
  });
  assert.equal(out.length, 1);
  assert.match(out[0].brief, /src\/foo\.py/);
  assert.match(out[0].brief, /Foo\b/);
  assert.match(out[0].brief, /\bbar\b/);
});

// ── decidePreSpawns: reviewer per-file heuristic ─────────────────────────

test("reviewer: 0 spawns when chunk has fewer than the threshold modules", () => {
  for (let n = 0; n < PER_FILE_REVIEW_MIN_MODULES; n++) {
    const paths = Array.from({ length: n }, (_, i) => `f${i}.py`);
    const out = decidePreSpawns({
      ...baseInput, stage: "reviewer",
      chunkFilePaths: paths,
      fileExistsOnDisk: new Map(paths.map(p => [p, true])),
    });
    assert.deepEqual(out, [], `expected no spawns at module count ${n}`);
  }
});

test("reviewer: one spawn per file when count is ≥ threshold and ≤ cap", () => {
  const paths = ["a.py", "b.py", "c.py"];
  const out = decidePreSpawns({
    ...baseInput, stage: "reviewer",
    chunkFilePaths: paths,
    fileExistsOnDisk: new Map(paths.map(p => [p, true])),
  });
  assert.equal(out.length, 3);
  for (let i = 0; i < paths.length; i++) {
    assert.equal(out[i].role, "reviewer-aux");
    assert.match(out[i].brief, new RegExp(paths[i]));
    assert.equal(out[i].contextKey, "per_file_review_findings");
  }
});

test("reviewer: caps at PER_FILE_REVIEW_MAX when chunk is large", () => {
  const big = Array.from({ length: 12 }, (_, i) => `f${i}.py`);
  const out = decidePreSpawns({
    ...baseInput, stage: "reviewer",
    chunkFilePaths: big,
    fileExistsOnDisk: new Map(big.map(p => [p, true])),
  });
  assert.equal(out.length, PER_FILE_REVIEW_MAX);
});

test("reviewer: respects remainingBudget cap below the heuristic cap", () => {
  const paths = ["a.py", "b.py", "c.py", "d.py"];
  const out = decidePreSpawns({
    ...baseInput, stage: "reviewer",
    chunkFilePaths: paths,
    fileExistsOnDisk: new Map(paths.map(p => [p, true])),
    remainingBudget: 2,  // tighter than chunk size + below PER_FILE_REVIEW_MAX
  });
  assert.equal(out.length, 2);
});

// ── renderExplorerBrief ─────────────────────────────────────────────────

test("renderExplorerBrief: includes paths, symbols, and a chunk label", () => {
  const out = renderExplorerBrief(["a.py", "b.py"], ["Foo", "bar"], "T2");
  assert.match(out, /chunk T2/);
  assert.match(out, /a\.py/);
  assert.match(out, /b\.py/);
  assert.match(out, /Foo/);
  assert.match(out, /bar/);
});

test("renderExplorerBrief: omits chunk label and symbols section when empty", () => {
  const out = renderExplorerBrief(["a.py"], [], null);
  assert.equal(out.includes("Symbols"), false);
  assert.equal(out.includes("chunk"), false);
});

test("renderExplorerBrief: truncates to max char count", () => {
  const symbols = Array.from({ length: 20 }, (_, i) => `symbol_${i}`.padEnd(80, "x"));
  const out = renderExplorerBrief(["a.py"], symbols, null);
  assert.ok(out.length <= EXPLORER_BRIEF_MAX_CHARS, `len=${out.length}`);
});

// ── renderReviewerAuxBrief ──────────────────────────────────────────────

test("renderReviewerAuxBrief: mentions the file path and the checklist", () => {
  const out = renderReviewerAuxBrief("src/foo.py", "T1");
  assert.match(out, /src\/foo\.py/);
  assert.match(out, /chunk T1/);
  assert.match(out, /verdict/);
  assert.match(out, /severity/);
});

test("renderReviewerAuxBrief: omits chunk label when null", () => {
  const out = renderReviewerAuxBrief("foo.py", null);
  assert.equal(out.includes("(chunk"), false);
});

// ── spliceResultsIntoContext ────────────────────────────────────────────

const sampleDescriptor = {
  role: "explorer" as const,
  brief: "...",
  contextKey: "existing_callers_summary",
  label: "explorer: T1",
};

test("spliceResultsIntoContext: empty input is a no-op", () => {
  const base = { foo: 1 };
  const out = spliceResultsIntoContext(base, []);
  assert.deepEqual(out, { foo: 1 });
});

test("spliceResultsIntoContext: single result becomes a single object", () => {
  const r: PreSpawnResult = {
    descriptor: sampleDescriptor,
    summary: "found 3 callers",
    finalStatus: "done",
  };
  const out = spliceResultsIntoContext({ foo: 1 }, [r]);
  assert.equal((out.foo as number), 1);
  assert.deepEqual(out["existing_callers_summary"], {
    label: "explorer: T1",
    summary: "found 3 callers",
  });
});

test("spliceResultsIntoContext: multiple same-key results become a list", () => {
  const r1: PreSpawnResult = {
    descriptor: { ...sampleDescriptor, label: "reviewer-aux: a.py", role: "reviewer-aux", contextKey: "per_file_review_findings" },
    summary: "a.py looks ok",
    finalStatus: "done",
  };
  const r2: PreSpawnResult = {
    descriptor: { ...sampleDescriptor, label: "reviewer-aux: b.py", role: "reviewer-aux", contextKey: "per_file_review_findings" },
    summary: "b.py needs fixes",
    finalStatus: "done",
  };
  const out = spliceResultsIntoContext({}, [r1, r2]);
  assert.ok(Array.isArray(out["per_file_review_findings"]));
  assert.equal((out["per_file_review_findings"] as unknown[]).length, 2);
});

test("spliceResultsIntoContext: drops results with null summary", () => {
  const r: PreSpawnResult = {
    descriptor: sampleDescriptor,
    summary: null,
    finalStatus: "abandoned",
    reason: "spawn failed",
  };
  const out = spliceResultsIntoContext({ foo: 1 }, [r]);
  assert.equal(out["existing_callers_summary"], undefined);
  assert.equal((out.foo as number), 1);
});

test("spliceResultsIntoContext: doesn't mutate the input context", () => {
  const r: PreSpawnResult = {
    descriptor: sampleDescriptor,
    summary: "ok",
    finalStatus: "done",
  };
  const base: Record<string, unknown> = { foo: 1 };
  spliceResultsIntoContext(base, [r]);
  assert.deepEqual(base, { foo: 1 });
});
