/**
 * harness-tier: ephemeral
 * expires-when: the sub-agent split is dissolved
 * cost-lever: deletes the per-stage budget tracker
 * (what: Tests for pipelineSubagentBudget.ts.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  StageSpawnBudget,
  SubagentBudgetExhausted,
} from "./pipelineSubagentBudget";

test("StageSpawnBudget: starts unspent and not exhausted", () => {
  const b = new StageSpawnBudget("sess1", "coder", 1, 5);
  assert.equal(b.used, 0);
  assert.equal(b.exhausted, false);
  assert.equal(b.stageKey, "sess1/coder#1");
});

test("StageSpawnBudget: consume increments used", () => {
  const b = new StageSpawnBudget("sess1", "coder", 1, 3);
  b.consume();
  assert.equal(b.used, 1);
  b.consume();
  assert.equal(b.used, 2);
  assert.equal(b.exhausted, false);
});

test("StageSpawnBudget: exhausted flips when used reaches limit", () => {
  const b = new StageSpawnBudget("s", "coder", 1, 2);
  b.consume();
  assert.equal(b.exhausted, false);
  b.consume();
  assert.equal(b.exhausted, true);
});

test("StageSpawnBudget: limit=0 means exhausted from the start", () => {
  const b = new StageSpawnBudget("s", "planner", 1, 0);
  assert.equal(b.exhausted, true);
});

test("StageSpawnBudget: rejects negative or non-integer limits", () => {
  assert.throws(() => new StageSpawnBudget("s", "coder", 1, -1), /non-negative integer/);
  assert.throws(() => new StageSpawnBudget("s", "coder", 1, 1.5), /non-negative integer/);
});

test("SubagentBudgetExhausted: carries the budget instance + descriptive message", () => {
  const b = new StageSpawnBudget("sess42", "reviewer", 2, 3);
  b.consume(); b.consume(); b.consume();
  assert.equal(b.exhausted, true);
  const err = new SubagentBudgetExhausted(b);
  assert.equal(err.name, "SubagentBudgetExhausted");
  assert.equal(err.budget, b);
  assert.match(err.message, /sess42\/reviewer#2/);
  assert.match(err.message, /limit=3/);
  assert.match(err.message, /used=3/);
});

test("SubagentBudgetExhausted: is an Error instance for catch ergonomics", () => {
  const b = new StageSpawnBudget("s", "coder", 1, 1);
  const err = new SubagentBudgetExhausted(b);
  assert.ok(err instanceof Error);
  assert.ok(err instanceof SubagentBudgetExhausted);
});
