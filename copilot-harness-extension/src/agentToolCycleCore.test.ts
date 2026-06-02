import { test } from "node:test";
import assert from "node:assert/strict";

import { parseFrontmatterMaxTurns } from "./modelSelectorCore";
import {
  MAX_TURNS_BY_AGENT,
  DEFAULT_PIPELINE_MAX_TURNS,
  resolveMaxTurns,
} from "./agentToolCycleCore";

// ── parseFrontmatterMaxTurns ────────────────────────────────────────────────

test("parseFrontmatterMaxTurns: returns null for text without frontmatter", () => {
  assert.equal(parseFrontmatterMaxTurns("no frontmatter"), null);
});

test("parseFrontmatterMaxTurns: returns null when field absent", () => {
  assert.equal(parseFrontmatterMaxTurns("---\nmodel: claude-sonnet-4.5\n---\nbody"), null);
});

test("parseFrontmatterMaxTurns: parses integer value", () => {
  assert.equal(parseFrontmatterMaxTurns("---\nmaxTurns: 5\n---\nbody"), 5);
});

test("parseFrontmatterMaxTurns: ignores trailing comment", () => {
  assert.equal(parseFrontmatterMaxTurns("---\nmaxTurns: 10  # coder needs many turns\n---"), 10);
});

test("parseFrontmatterMaxTurns: returns null for zero", () => {
  assert.equal(parseFrontmatterMaxTurns("---\nmaxTurns: 0\n---"), null);
});

test("parseFrontmatterMaxTurns: returns null for non-integer string", () => {
  assert.equal(parseFrontmatterMaxTurns("---\nmaxTurns: abc\n---"), null);
});

test("parseFrontmatterMaxTurns: returns null for missing closing ---", () => {
  assert.equal(parseFrontmatterMaxTurns("---\nmaxTurns: 3\nbody"), null);
});

// ── MAX_TURNS_BY_AGENT defaults ─────────────────────────────────────────────

test("MAX_TURNS_BY_AGENT: coder has the highest cap", () => {
  const max = Math.max(...Object.values(MAX_TURNS_BY_AGENT));
  assert.equal(MAX_TURNS_BY_AGENT["coder"], max);
});

test("MAX_TURNS_BY_AGENT: all values are positive integers", () => {
  for (const [name, v] of Object.entries(MAX_TURNS_BY_AGENT)) {
    assert.ok(Number.isInteger(v) && v > 0, `${name}: ${v} should be a positive integer`);
  }
});

test("MAX_TURNS_BY_AGENT: scoper has the lowest cap (2)", () => {
  assert.equal(MAX_TURNS_BY_AGENT["code-review-scoper"], 2);
});

// ── resolveMaxTurns ──────────────────────────────────────────────────────────

test("resolveMaxTurns: frontmatter value wins over table", () => {
  assert.equal(resolveMaxTurns("coder", 7), 7);
});

test("resolveMaxTurns: null frontmatter falls back to table", () => {
  assert.equal(resolveMaxTurns("coder", null), MAX_TURNS_BY_AGENT["coder"]);
});

test("resolveMaxTurns: unknown agent with null frontmatter falls back to DEFAULT", () => {
  assert.equal(resolveMaxTurns("unknown-agent", null), DEFAULT_PIPELINE_MAX_TURNS);
});

test("resolveMaxTurns: zero frontmatter treated as null (falls back)", () => {
  // parseFrontmatterMaxTurns already returns null for zero; this tests
  // that resolveMaxTurns also rejects it defensively.
  assert.equal(resolveMaxTurns("planner", 0), MAX_TURNS_BY_AGENT["planner"]);
});
