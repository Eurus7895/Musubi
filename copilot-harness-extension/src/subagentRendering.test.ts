import { test } from "node:test";
import assert from "node:assert/strict";

import {
  formatSpawnMarker,
  formatCompleteMarker,
  formatMarker,
  SubagentEventTracker,
  type SubagentSpawnEvent,
  type SubagentCompleteEvent,
  type SubagentEventClient,
} from "./subagentRendering";

const baseSpawn: SubagentSpawnEvent = {
  ts: 1000,
  handle_id: "abcd1234efgh",
  parent_session_id: "sess-1",
  parent_agent_name: "agent",
  role: "explorer",
  brief: "find all callers of foo",
  event: "spawned",
  allowed_tools: ["Read", "Grep"],
  max_turns: 10,
  wall_clock_timeout_s: 300,
};

const baseComplete: SubagentCompleteEvent = {
  ts: 2000,
  handle_id: "abcd1234efgh",
  parent_session_id: "sess-1",
  parent_agent_name: "agent",
  role: "explorer",
  brief: "find all callers of foo",
  event: "completed",
  final_status: "done",
  escalated: false,
  turns: 4,
  tools_used: ["Read", "Grep", "Read"],
  summary_truncated: false,
  verification_errors: null,
};

// ── formatSpawnMarker ──────────────────────────────────────────────

test("formatSpawnMarker: includes role, short handle, and brief", () => {
  const out = formatSpawnMarker(baseSpawn);
  assert.match(out, /\*\*explorer\*\*/);
  assert.match(out, /`abcd1234`/);
  assert.match(out, /find all callers of foo/);
  assert.ok(out.startsWith("▶"));
});

test("formatSpawnMarker: truncates briefs longer than 80 chars with ellipsis", () => {
  const long = "x".repeat(120);
  const out = formatSpawnMarker({ ...baseSpawn, brief: long });
  assert.ok(out.endsWith("…"));
  assert.ok(!out.includes("x".repeat(120)));
});

test("formatSpawnMarker: short handle is exactly 8 chars", () => {
  const out = formatSpawnMarker(baseSpawn);
  const match = out.match(/`([^`]+)`/);
  assert.ok(match);
  assert.equal(match![1].length, 8);
});

// ── formatCompleteMarker ───────────────────────────────────────────

test("formatCompleteMarker: done status uses ✓ icon", () => {
  const out = formatCompleteMarker(baseComplete);
  assert.ok(out.startsWith("✓"));
  assert.match(out, /done/);
});

test("formatCompleteMarker: escalated uses ⚠ and labels status", () => {
  const out = formatCompleteMarker({
    ...baseComplete,
    final_status: "escalated",
    escalated: true,
  });
  assert.ok(out.startsWith("⚠"));
  assert.match(out, /escalated \(escalated\)/);
});

test("formatCompleteMarker: failed without escalation uses ✗", () => {
  const out = formatCompleteMarker({
    ...baseComplete,
    final_status: "failed",
    escalated: false,
  });
  assert.ok(out.startsWith("✗"));
  assert.match(out, /failed/);
});

test("formatCompleteMarker: pluralizes turns", () => {
  const one = formatCompleteMarker({ ...baseComplete, turns: 1 });
  const many = formatCompleteMarker({ ...baseComplete, turns: 5 });
  assert.match(one, /1 turn(?!s)/);
  assert.match(many, /5 turns/);
});

test("formatCompleteMarker: tool histogram aggregates duplicates", () => {
  const out = formatCompleteMarker(baseComplete);
  assert.match(out, /Read×2/);
  assert.match(out, /Grep/);
});

test("formatCompleteMarker: surfaces summary truncation flag", () => {
  const out = formatCompleteMarker({ ...baseComplete, summary_truncated: true });
  assert.match(out, /summary truncated/);
});

test("formatCompleteMarker: surfaces verification errors when present", () => {
  const out = formatCompleteMarker({
    ...baseComplete,
    verification_errors: ["secret leak", "schema mismatch"],
  });
  assert.match(out, /verify: secret leak; schema mismatch/);
});

test("formatCompleteMarker: omits tool histogram when no tools used", () => {
  const out = formatCompleteMarker({ ...baseComplete, tools_used: null });
  assert.equal(out.includes("·"), true); // separator still present for turns
  assert.equal(out.includes(", "), false); // no histogram pieces
});

// ── formatMarker dispatch ──────────────────────────────────────────

test("formatMarker: dispatches on event field", () => {
  assert.equal(formatMarker(baseSpawn), formatSpawnMarker(baseSpawn));
  assert.equal(formatMarker(baseComplete), formatCompleteMarker(baseComplete));
});

// ── SubagentEventTracker ───────────────────────────────────────────

class StubClient implements SubagentEventClient {
  calls: Array<{ name: string; args: Record<string, unknown> }> = [];
  responses: string[] = [];

  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    this.calls.push({ name, args });
    return this.responses.shift() ?? JSON.stringify({ events: [], count: 0 });
  }
}

test("SubagentEventTracker: first poll has no since_ts; subsequent polls advance the cursor", async () => {
  const stub = new StubClient();
  stub.responses = [
    JSON.stringify({ events: [baseSpawn, baseComplete], count: 2 }),
    JSON.stringify({ events: [], count: 0 }),
  ];
  const tracker = new SubagentEventTracker(stub, "sess-1");

  const first = await tracker.pollOnce();
  assert.equal(first.events.length, 2);
  assert.equal(first.markers.length, 2);
  assert.equal(stub.calls[0].args.since_ts, undefined);
  assert.equal(stub.calls[0].args.parent_session_id, "sess-1");

  const second = await tracker.pollOnce();
  assert.equal(second.events.length, 0);
  assert.equal(stub.calls[1].args.since_ts, 2000); // max ts from first batch
});

test("SubagentEventTracker: returns formatted markers paired with events", async () => {
  const stub = new StubClient();
  stub.responses = [JSON.stringify({ events: [baseSpawn], count: 1 })];
  const tracker = new SubagentEventTracker(stub, "sess-1");

  const result = await tracker.pollOnce();
  assert.equal(result.markers[0], formatSpawnMarker(baseSpawn));
});

test("SubagentEventTracker: malformed response yields empty result", async () => {
  const stub = new StubClient();
  stub.responses = ["not json at all"];
  const tracker = new SubagentEventTracker(stub, "sess-1");

  const result = await tracker.pollOnce();
  assert.deepEqual(result, { events: [], markers: [] });
});

test("SubagentEventTracker: reset() clears the cursor", async () => {
  const stub = new StubClient();
  stub.responses = [
    JSON.stringify({ events: [baseSpawn], count: 1 }),
    JSON.stringify({ events: [], count: 0 }),
  ];
  const tracker = new SubagentEventTracker(stub, "sess-1");

  await tracker.pollOnce();
  tracker.reset();
  await tracker.pollOnce();

  assert.equal(stub.calls[1].args.since_ts, undefined);
});

test("SubagentEventTracker: forwards limit param", async () => {
  const stub = new StubClient();
  stub.responses = [JSON.stringify({ events: [], count: 0 })];
  const tracker = new SubagentEventTracker(stub, "sess-1", 50);

  await tracker.pollOnce();
  assert.equal(stub.calls[0].args.limit, 50);
});
