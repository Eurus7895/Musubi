import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import {
  BudgetEnforcer,
  RATES,
  UNKNOWN_FAMILY_RATE,
  estimateCallCredits,
  estimateTokensFromChars,
  rateFor,
  resolvePipelineBudget,
} from "./pipelineBudgetCore";

// ── rateFor ─────────────────────────────────────────────────────────────────

test("rateFor: returns the table entry for a known family", () => {
  const r = rateFor("claude-sonnet-4.6");
  assert.equal(r.input, 3.00);
  assert.equal(r.cached_input, 0.30);
  assert.equal(r.output, 15.00);
  assert.equal(r.cache_write, 3.75);
});

test("rateFor: falls back to UNKNOWN_FAMILY_RATE for unknown family", () => {
  const r = rateFor("not-a-real-model");
  assert.equal(r, UNKNOWN_FAMILY_RATE);
});

// ── estimateTokensFromChars ─────────────────────────────────────────────────

test("estimateTokensFromChars: roughly 4 chars per token", () => {
  assert.equal(estimateTokensFromChars(0), 0);
  assert.equal(estimateTokensFromChars(4), 1);
  assert.equal(estimateTokensFromChars(5), 2);   // Math.ceil
  assert.equal(estimateTokensFromChars(40), 10);
  assert.equal(estimateTokensFromChars(41), 11);
});

// ── estimateCallCredits ─────────────────────────────────────────────────────

test("estimateCallCredits: input-only call on Sonnet", () => {
  // 1M input tokens at $3/M = $3 = 300 credits
  assert.equal(estimateCallCredits("claude-sonnet-4.6", 1_000_000, 0), 300);
});

test("estimateCallCredits: input + output on Sonnet", () => {
  // 100k input × $3/M = $0.30 = 30 credits
  // 10k output × $15/M = $0.15 = 15 credits
  // Total: 45 credits
  const c = estimateCallCredits("claude-sonnet-4.6", 100_000, 10_000);
  assert.equal(Math.round(c * 100) / 100, 45);
});

test("estimateCallCredits: cached portion charged at cached rate", () => {
  // 100k input, 80k cached → 20k fresh at $3 + 80k cached at $0.30
  // = $0.06 + $0.024 = $0.084 = 8.4 credits
  // No output.
  const c = estimateCallCredits("claude-sonnet-4.6", 100_000, 0, 80_000);
  assert.equal(Math.round(c * 1000) / 1000, 8.4);
});

test("estimateCallCredits: unknown family uses Sonnet-level fallback", () => {
  const known = estimateCallCredits("claude-sonnet-4.6", 100_000, 10_000);
  const unknown = estimateCallCredits("nonsense-model", 100_000, 10_000);
  assert.equal(known, unknown);
});

// ── resolvePipelineBudget ───────────────────────────────────────────────────

function makeRoot(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "pbudget-"));
}

function writePipelineYaml(root: string, name: string, body: string): void {
  const dir = path.join(root, ".github", "pipelines", name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "pipeline.yaml"), body, "utf-8");
}

test("resolvePipelineBudget: returns maxCredits:null + default 0.8 ratio when file missing", () => {
  const root = makeRoot();
  const r = resolvePipelineBudget([root], "feature-dev");
  assert.equal(r.maxCredits, null);
  assert.equal(r.warnAtRatio, 0.8);
});

test("resolvePipelineBudget: parses integer max_credits", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: x\nmax_credits: 50\n");
  assert.equal(resolvePipelineBudget([root], "feature-dev").maxCredits, 50);
});

test("resolvePipelineBudget: parses fractional max_credits", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: x\nmax_credits: 12.5\n");
  assert.equal(resolvePipelineBudget([root], "feature-dev").maxCredits, 12.5);
});

test("resolvePipelineBudget: parses warn_at and uses it", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "max_credits: 50\nwarn_at: 0.75\n");
  const r = resolvePipelineBudget([root], "feature-dev");
  assert.equal(r.maxCredits, 50);
  assert.equal(r.warnAtRatio, 0.75);
});

test("resolvePipelineBudget: rejects warn_at outside (0,1]", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "max_credits: 50\nwarn_at: 1.5\n");
  const r = resolvePipelineBudget([root], "feature-dev");
  assert.equal(r.warnAtRatio, 0.8);   // falls back to default
});

test("resolvePipelineBudget: rejects zero max_credits", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "max_credits: 0\n");
  assert.equal(resolvePipelineBudget([root], "feature-dev").maxCredits, null);
});

test("resolvePipelineBudget: tolerates trailing comment on each line", () => {
  const root = makeRoot();
  writePipelineYaml(
    root,
    "feature-dev",
    "max_credits: 30  # tight budget\nwarn_at: 0.7  # warn earlier\n",
  );
  const r = resolvePipelineBudget([root], "feature-dev");
  assert.equal(r.maxCredits, 30);
  assert.equal(r.warnAtRatio, 0.7);
});

test("resolvePipelineBudget: rejects pipeline names with path-traversal chars", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "max_credits: 50\n");
  const r = resolvePipelineBudget([root], "../feature-dev");
  assert.equal(r.maxCredits, null);
});

// ── BudgetEnforcer ──────────────────────────────────────────────────────────

test("BudgetEnforcer: rejects invalid maxCredits at construction", () => {
  assert.throws(() => new BudgetEnforcer(0));
  assert.throws(() => new BudgetEnforcer(-5));
  assert.throws(() => new BudgetEnforcer(NaN));
});

test("BudgetEnforcer: rejects invalid warnAtRatio at construction", () => {
  assert.throws(() => new BudgetEnforcer(50, 0));
  assert.throws(() => new BudgetEnforcer(50, 1.5));
  assert.throws(() => new BudgetEnforcer(50, -0.1));
});

test("BudgetEnforcer: starts at 0 used with maxCredits remaining", () => {
  const e = new BudgetEnforcer(50, 0.8);
  assert.equal(e.creditsUsed, 0);
  assert.equal(e.remaining, 50);
  assert.equal(e.warned, false);
});

test("BudgetEnforcer: preflight allows when projected total is below warn threshold", () => {
  const e = new BudgetEnforcer(50, 0.8);
  assert.equal(e.preflight(10), "allow");
  assert.equal(e.preflight(39), "allow");   // 0 + 39 = 39, still below 40
});

test("BudgetEnforcer: preflight returns warn when projected crosses warn threshold", () => {
  const e = new BudgetEnforcer(50, 0.8);
  // warn at 40 credits
  assert.equal(e.preflight(40), "warn");
  assert.equal(e.preflight(45), "warn");
});

test("BudgetEnforcer: preflight returns halt when projected exceeds cap", () => {
  const e = new BudgetEnforcer(50, 0.8);
  assert.equal(e.preflight(51), "halt");
  assert.equal(e.preflight(1000), "halt");
});

test("BudgetEnforcer: charge accumulates", () => {
  const e = new BudgetEnforcer(50, 0.8);
  e.charge(10);
  assert.equal(e.creditsUsed, 10);
  assert.equal(e.remaining, 40);
  e.charge(5);
  assert.equal(e.creditsUsed, 15);
  assert.equal(e.remaining, 35);
});

test("BudgetEnforcer: charge returns warn exactly once when crossing threshold", () => {
  const e = new BudgetEnforcer(50, 0.8);
  assert.equal(e.charge(30), "allow");   // 30, below 40 warn threshold
  assert.equal(e.warned, false);
  assert.equal(e.charge(15), "warn");    // 45, crosses 40 — warn fires
  assert.equal(e.warned, true);
  assert.equal(e.charge(2), "allow");    // 47, still above warn but already warned
});

test("BudgetEnforcer: charge returns halt when total exceeds cap", () => {
  const e = new BudgetEnforcer(50, 0.8);
  assert.equal(e.charge(40), "warn");
  assert.equal(e.charge(11), "halt");    // 51 > 50
  assert.equal(e.creditsUsed, 51);       // still recorded
});

test("BudgetEnforcer: charge rejects negative spend", () => {
  const e = new BudgetEnforcer(50);
  assert.throws(() => e.charge(-1));
});

test("BudgetEnforcer: preflight is non-mutating", () => {
  const e = new BudgetEnforcer(50, 0.8);
  e.preflight(40);
  e.preflight(60);
  assert.equal(e.creditsUsed, 0);
  assert.equal(e.warned, false);
});

// ── Active-enforcer registry ────────────────────────────────────────────────

import {
  BudgetExhaustedError,
  getActiveBudget,
  registerActiveBudget,
  unregisterActiveBudget,
  _resetActiveBudgets_FOR_TESTS,
} from "./pipelineBudgetCore";

test("registry: getActiveBudget returns null when no session is registered", () => {
  _resetActiveBudgets_FOR_TESTS();
  assert.equal(getActiveBudget("nonexistent"), null);
});

test("registry: register/get round-trip", () => {
  _resetActiveBudgets_FOR_TESTS();
  const e = new BudgetEnforcer(50);
  let captured: unknown = null;
  registerActiveBudget("sess-1", e, (ev) => { captured = ev; });
  const got = getActiveBudget("sess-1");
  assert.ok(got);
  assert.equal(got!.enforcer, e);
  got!.onEvent({
    status: "warn", phase: "preflight", creditsUsed: 5, maxCredits: 50,
    remaining: 45, family: "gpt-5-mini", thisCallCredits: 1,
  });
  assert.ok(captured);
});

test("registry: unregister removes the entry", () => {
  _resetActiveBudgets_FOR_TESTS();
  registerActiveBudget("sess-1", new BudgetEnforcer(50), () => {});
  unregisterActiveBudget("sess-1");
  assert.equal(getActiveBudget("sess-1"), null);
});

test("registry: two sessions can hold independent budgets", () => {
  _resetActiveBudgets_FOR_TESTS();
  const a = new BudgetEnforcer(50);
  const b = new BudgetEnforcer(100);
  registerActiveBudget("sess-a", a, () => {});
  registerActiveBudget("sess-b", b, () => {});
  assert.equal(getActiveBudget("sess-a")!.enforcer.maxCredits, 50);
  assert.equal(getActiveBudget("sess-b")!.enforcer.maxCredits, 100);
});

test("BudgetExhaustedError: carries phase + credits + family", () => {
  const err = new BudgetExhaustedError("postflight", 52.3, 50, "claude-sonnet-4.6", 12.5);
  assert.equal(err.phase, "postflight");
  assert.equal(err.creditsUsed, 52.3);
  assert.equal(err.maxCredits, 50);
  assert.equal(err.family, "claude-sonnet-4.6");
  assert.equal(err.thisCallCredits, 12.5);
  assert.equal(err.name, "BudgetExhaustedError");
  assert.ok(err.message.includes("postflight"));
  assert.ok(err.message.includes("claude-sonnet-4.6"));
});
