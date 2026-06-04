import { test } from "node:test";
import assert from "node:assert/strict";

import {
  summarizeStages,
  formatTiming,
  formatTokens,
  describeStage,
  describeChunk,
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
    }),
    "attempt 2 · 5.0s",
  );
});
