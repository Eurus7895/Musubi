import { test } from "node:test";
import assert from "node:assert/strict";

import {
  summarizeStages,
  summarizeSession,
  formatTiming,
  formatTokens,
  formatCredits,
  describeStage,
  describeChunk,
  describeSession,
  type BudgetSnapshot,
  type StageMetricsRow,
  type StageStatusInfo,
} from "./tasksViewCore";

function row(overrides: Partial<StageMetricsRow> = {}): StageMetricsRow {
  return {
    stage: "plan",
    chunk_id: null,
    attempt: 1,
    started_at: 0,
    ended_at: null,
    lm_ms: 0,
    tokens_in_estimate: 0,
    tokens_out_estimate: 0,
    ...overrides,
  };
}

// ── summarizeStages ─────────────────────────────────────────────────────────

test("summarizeStages: returns every stage even when none has metrics", () => {
  const out = summarizeStages({}, []);
  assert.deepEqual(out.map(s => s.stage), ["plan", "design", "code", "review"]);
  assert.ok(out.every(s => s.status === "pending" && s.attempt === 0));
  assert.ok(out.every(s => s.totalLmMs === 0 && s.chunks.length === 0));
});

test("summarizeStages: propagates status + attempt from harness_get_status", () => {
  const statuses: Record<string, StageStatusInfo> = {
    plan:   { status: "complete", attempt: 1 },
    design: { status: "in_progress", attempt: 2 },
  };
  const out = summarizeStages(statuses, []);
  assert.equal(out[0].status, "complete");
  assert.equal(out[0].attempt, 1);
  assert.equal(out[1].status, "in_progress");
  assert.equal(out[1].attempt, 2);
});

test("summarizeStages: aggregates lm_ms + token counts across rows", () => {
  const metrics = [
    row({ stage: "plan", lm_ms: 1200, tokens_in_estimate: 400, tokens_out_estimate: 200 }),
    row({ stage: "plan", attempt: 2, lm_ms: 800, tokens_in_estimate: 500, tokens_out_estimate: 300 }),
  ];
  const out = summarizeStages({ plan: { status: "complete", attempt: 2 } }, metrics);
  const plan = out[0];
  assert.equal(plan.totalLmMs, 2000);
  assert.equal(plan.totalTokensIn, 900);
  assert.equal(plan.totalTokensOut, 500);
  assert.equal(plan.rowCount, 2);
  assert.equal(plan.chunks.length, 0);  // no chunk_id on either row
});

test("summarizeStages: code stage groups by chunk_id and reports per-chunk totals", () => {
  const metrics = [
    row({ stage: "code", chunk_id: "T1", attempt: 1, lm_ms: 5000, tokens_in_estimate: 1000 }),
    row({ stage: "code", chunk_id: "T1", attempt: 2, lm_ms: 3000, tokens_in_estimate: 800 }),
    row({ stage: "code", chunk_id: "T2", attempt: 1, lm_ms: 2000, tokens_in_estimate: 700 }),
  ];
  const out = summarizeStages({ code: { status: "in_progress", attempt: 2 } }, metrics);
  const code = out[2];
  assert.equal(code.chunks.length, 2);
  const t1 = code.chunks.find(c => c.chunk_id === "T1")!;
  const t2 = code.chunks.find(c => c.chunk_id === "T2")!;
  assert.equal(t1.attempt, 2);
  assert.equal(t1.totalLmMs, 8000);
  assert.equal(t1.totalTokensIn, 1800);
  assert.equal(t2.attempt, 1);
  assert.equal(t2.totalLmMs, 2000);
});

test("summarizeStages: chunks sorted by chunk_id for stable rendering", () => {
  const metrics = [
    row({ stage: "code", chunk_id: "T3", lm_ms: 100 }),
    row({ stage: "code", chunk_id: "T1", lm_ms: 100 }),
    row({ stage: "code", chunk_id: "T2", lm_ms: 100 }),
  ];
  const out = summarizeStages({}, metrics);
  const code = out[2];
  assert.deepEqual(code.chunks.map(c => c.chunk_id), ["T1", "T2", "T3"]);
});

test("summarizeStages: ignores chunk_id=null when grouping", () => {
  const metrics = [
    row({ stage: "code", chunk_id: null, lm_ms: 100 }),
    row({ stage: "code", chunk_id: "T1", lm_ms: 200 }),
  ];
  const out = summarizeStages({}, metrics);
  const code = out[2];
  assert.equal(code.chunks.length, 1);
  assert.equal(code.chunks[0].chunk_id, "T1");
  assert.equal(code.totalLmMs, 300, "stage total still includes the null-chunk row");
});

// ── formatTiming ────────────────────────────────────────────────────────────

test("formatTiming: 0ms → empty string", () => {
  assert.equal(formatTiming(0), "");
  assert.equal(formatTiming(-5), "");
});

test("formatTiming: sub-second values include ms", () => {
  assert.equal(formatTiming(120), "120ms");
  assert.equal(formatTiming(999), "999ms");
});

test("formatTiming: sub-minute values use seconds with one decimal", () => {
  assert.equal(formatTiming(1000), "1.0s");
  assert.equal(formatTiming(4567), "4.6s");
  assert.equal(formatTiming(59_999), "60.0s");
});

test("formatTiming: minute+ values use m + s", () => {
  assert.equal(formatTiming(60_000), "1m 0s");
  assert.equal(formatTiming(120_000), "2m 0s");
  assert.equal(formatTiming(75_500), "1m 16s");
});

// ── formatTokens ────────────────────────────────────────────────────────────

test("formatTokens: small counts shown raw", () => {
  assert.equal(formatTokens(0), "");
  assert.equal(formatTokens(412), "412");
});

test("formatTokens: 1k+ shown with k suffix", () => {
  assert.equal(formatTokens(1500), "1.5k");
  assert.equal(formatTokens(12_345), "12.3k");
});

// ── describeStage / describeChunk ──────────────────────────────────────────

test("describeStage: omits empty parts cleanly", () => {
  assert.equal(
    describeStage({
      stage: "plan", status: "pending", attempt: 0,
      totalLmMs: 0, totalTokensIn: 0, totalTokensOut: 0, rowCount: 0, chunks: [],
    }),
    "",
  );
});

test("describeStage: composes attempt + timing + chunk-progress when present", () => {
  assert.equal(
    describeStage({
      stage: "code", status: "in_progress", attempt: 2,
      totalLmMs: 12_000, totalTokensIn: 0, totalTokensOut: 0, rowCount: 5,
      chunks: [
        { chunk_id: "T1", attempt: 1, totalLmMs: 4000, totalTokensIn: 0, totalTokensOut: 0, rowCount: 1 },
        { chunk_id: "T2", attempt: 1, totalLmMs: 4000, totalTokensIn: 0, totalTokensOut: 0, rowCount: 1 },
        { chunk_id: "T3", attempt: 1, totalLmMs: 0,    totalTokensIn: 0, totalTokensOut: 0, rowCount: 0 },
      ],
    }),
    "attempt 2 · 12.0s · 2/3 chunks",
  );
});

test("describeStage: single chunk → no chunk-progress suffix", () => {
  assert.equal(
    describeStage({
      stage: "code", status: "complete", attempt: 1,
      totalLmMs: 4000, totalTokensIn: 0, totalTokensOut: 0, rowCount: 1,
      chunks: [
        { chunk_id: "T1", attempt: 1, totalLmMs: 4000, totalTokensIn: 0, totalTokensOut: 0, rowCount: 1 },
      ],
    }),
    "4.0s",
  );
});

test("describeChunk: composes attempt + timing", () => {
  assert.equal(
    describeChunk({
      chunk_id: "T1", attempt: 2,
      totalLmMs: 5000, totalTokensIn: 0, totalTokensOut: 0, rowCount: 2,
      totalCredits: 0,
    }),
    "attempt 2 · 5.0s",
  );
});

// ── Stage 1 (MVP A.4) — credits aggregation + display ──────────────────────

test("summarizeStages: aggregates credits across rows", () => {
  const metrics = [
    row({ stage: "plan", lm_ms: 100, credits: 1.5 }),
    row({ stage: "plan", attempt: 2, lm_ms: 200, credits: 2.3 }),
  ];
  const out = summarizeStages({ plan: { status: "complete", attempt: 2 } }, metrics);
  assert.equal(out[0].totalCredits, 3.8);
});

test("summarizeStages: missing credits field defaults to 0", () => {
  // Simulates pre-Stage-1 rows where the credits column was absent.
  const metrics = [row({ stage: "plan", lm_ms: 100 })] as readonly StageMetricsRow[];
  const out = summarizeStages({ plan: { status: "complete", attempt: 1 } }, metrics);
  assert.equal(out[0].totalCredits, 0);
});

test("summarizeStages: chunk totals include per-chunk credits", () => {
  const metrics = [
    row({ stage: "code", chunk_id: "T1", lm_ms: 100, credits: 5.0 }),
    row({ stage: "code", chunk_id: "T1", attempt: 2, lm_ms: 80, credits: 3.0 }),
    row({ stage: "code", chunk_id: "T2", lm_ms: 60, credits: 2.5 }),
  ];
  const out = summarizeStages({ code: { status: "in_progress", attempt: 2 } }, metrics);
  const code = out[2];
  assert.equal(code.totalCredits, 10.5);
  const t1 = code.chunks.find(c => c.chunk_id === "T1")!;
  assert.equal(t1.totalCredits, 8.0);
});

test("formatCredits: 0 → empty string", () => {
  assert.equal(formatCredits(0), "");
  assert.equal(formatCredits(-1), "");
});

test("formatCredits: < 100 uses one decimal", () => {
  assert.equal(formatCredits(3.2), "3.2c");
  assert.equal(formatCredits(12.0), "12.0c");
  assert.equal(formatCredits(99.9), "99.9c");
});

test("formatCredits: 100-999 rounded to int", () => {
  assert.equal(formatCredits(150), "150c");
  assert.equal(formatCredits(412.7), "413c");
});

test("formatCredits: 1k+ uses k suffix", () => {
  assert.equal(formatCredits(1500), "1.5kc");
  assert.equal(formatCredits(12_345), "12.3kc");
});

test("describeStage: includes credits when totalCredits > 0", () => {
  assert.equal(
    describeStage({
      stage: "code", status: "in_progress", attempt: 2,
      totalLmMs: 12_000, totalTokensIn: 0, totalTokensOut: 0,
      totalCredits: 14.2, rowCount: 3, chunks: [],
    }),
    "attempt 2 · 12.0s · 14.2c",
  );
});

test("describeStage: omits credits when totalCredits is 0", () => {
  assert.equal(
    describeStage({
      stage: "plan", status: "complete", attempt: 1,
      totalLmMs: 1500, totalTokensIn: 0, totalTokensOut: 0,
      totalCredits: 0, rowCount: 1, chunks: [],
    }),
    "1.5s",
  );
});

test("describeChunk: includes credits", () => {
  assert.equal(
    describeChunk({
      chunk_id: "T1", attempt: 1,
      totalLmMs: 3000, totalTokensIn: 0, totalTokensOut: 0,
      totalCredits: 4.7, rowCount: 1,
    }),
    "3.0s · 4.7c",
  );
});

// ── summarizeSession + describeSession ────────────────────────────────────

test("summarizeSession: prefers live budget over historic", () => {
  const live: BudgetSnapshot = {
    creditsUsed: 8.2, maxCredits: 50, remaining: 41.8, warnAtRatio: 0.8,
  };
  const s = summarizeSession("abc123", "active", 6.0, live);
  // historic was 6.0 but live shows 8.2 — live wins for the "now" display
  assert.equal(s.totalCredits, 8.2);
  assert.equal(s.liveBudget, live);
});

test("summarizeSession: falls back to historic when no live budget", () => {
  const s = summarizeSession("abc123", "paused", 12.4, null);
  assert.equal(s.totalCredits, 12.4);
  assert.equal(s.liveBudget, null);
});

test("describeSession: live budget shows X/Y format with percent", () => {
  const s = summarizeSession("abc123", "active", 0, {
    creditsUsed: 12.4, maxCredits: 50, remaining: 37.6, warnAtRatio: 0.8,
  });
  assert.equal(describeSession(s), "12.4 / 50 credits (25%)");
});

test("describeSession: paused session shows just credits used", () => {
  const s = summarizeSession("abc123", "paused", 12.4, null);
  assert.equal(describeSession(s), "12.4 credits used");
});

test("describeSession: empty when no budget and no historic spend", () => {
  const s = summarizeSession("abc123", "pending", 0, null);
  assert.equal(describeSession(s), "");
});
