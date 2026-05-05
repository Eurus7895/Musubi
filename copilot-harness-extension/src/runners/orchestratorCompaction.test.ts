import { test } from "node:test";
import assert from "node:assert/strict";

import {
  applyCompaction,
  CHARS_PER_TOKEN,
  COMPACT_T1_DROP_TOOLS,
  COMPACT_T2_SUMMARIZE,
  COMPACT_T3_TRUNCATE,
  detectFrustration,
  estimateTokens,
  MODEL_CONTEXT_TOKENS,
  parseConversationResponse,
  planCompaction,
  resolveChatId,
  SpawnTracker,
  totalHistoryTokens,
  TriggerDedup,
  type OrchestratorMessage,
} from "./orchestratorCore";

// ── estimateTokens ───────────────────────────────────────────────────────────

test("estimateTokens: empty string is zero tokens", () => {
  assert.equal(estimateTokens(""), 0);
});

test("estimateTokens: single character costs at least one token", () => {
  assert.equal(estimateTokens("x"), 1);
});

test("estimateTokens: matches verifier heuristic of chars/4 for longer text", () => {
  const text = "x".repeat(80);
  assert.equal(estimateTokens(text), 80 / CHARS_PER_TOKEN);
});

// ── parseConversationResponse ────────────────────────────────────────────────

test("parseConversationResponse: extracts user/assistant/tool messages", () => {
  const raw = JSON.stringify({
    status: "ok",
    messages: [
      { id: 1, role: "user", content: "hi", ts: "2026-05-05T08:00:00+00:00" },
      { id: 2, role: "assistant", content: "hello!", ts: "2026-05-05T08:00:01+00:00" },
      { id: 3, role: "tool", content: "{\"result\":42}", ts: "2026-05-05T08:00:02+00:00" },
    ],
  });
  const msgs = parseConversationResponse(raw);
  assert.equal(msgs.length, 3);
  assert.deepEqual(msgs.map(m => m.role), ["user", "assistant", "tool"]);
});

test("parseConversationResponse: returns empty array on non-ok status", () => {
  const raw = JSON.stringify({ status: "error", error: "boom" });
  assert.deepEqual(parseConversationResponse(raw), []);
});

test("parseConversationResponse: returns empty array on malformed JSON", () => {
  assert.deepEqual(parseConversationResponse("not json"), []);
  assert.deepEqual(parseConversationResponse(""), []);
});

test("parseConversationResponse: filters out rows with unknown role", () => {
  const raw = JSON.stringify({
    status: "ok",
    messages: [
      { id: 1, role: "user", content: "ok" },
      { id: 2, role: "wizard", content: "should be dropped" },
      { id: 3, role: "assistant", content: "ok2" },
    ],
  });
  const msgs = parseConversationResponse(raw);
  assert.deepEqual(msgs.map(m => m.role), ["user", "assistant"]);
});

// ── resolveChatId ────────────────────────────────────────────────────────────

test("resolveChatId: stable for identical inputs", () => {
  const a = resolveChatId({
    participantId: "copilot-harness.harness",
    firstUserPrompt: "find the bug",
    workspacePath: "/repo",
  });
  const b = resolveChatId({
    participantId: "copilot-harness.harness",
    firstUserPrompt: "find the bug",
    workspacePath: "/repo",
  });
  assert.equal(a, b);
  assert.equal(a.length, 16);
  assert.match(a, /^[0-9a-f]{16}$/);
});

test("resolveChatId: differs when first prompt changes", () => {
  const a = resolveChatId({
    participantId: "copilot-harness.harness",
    firstUserPrompt: "find the bug",
    workspacePath: "/repo",
  });
  const b = resolveChatId({
    participantId: "copilot-harness.harness",
    firstUserPrompt: "list the files",
    workspacePath: "/repo",
  });
  assert.notEqual(a, b);
});

test("resolveChatId: differs across workspaces with the same prompt", () => {
  const a = resolveChatId({
    participantId: "copilot-harness.harness",
    firstUserPrompt: "find the bug",
    workspacePath: "/repo-a",
  });
  const b = resolveChatId({
    participantId: "copilot-harness.harness",
    firstUserPrompt: "find the bug",
    workspacePath: "/repo-b",
  });
  assert.notEqual(a, b);
});

// ── totalHistoryTokens ───────────────────────────────────────────────────────

test("totalHistoryTokens: sums system prompt + history + current prompt", () => {
  const messages: OrchestratorMessage[] = [
    { role: "user", content: "x".repeat(40) },        // 10 tokens
    { role: "assistant", content: "y".repeat(80) },   // 20 tokens
  ];
  const total = totalHistoryTokens(messages, "z".repeat(20), "q".repeat(40));
  // system 5 + 10 + 20 + current 10 = 45
  assert.equal(total, 5 + 10 + 20 + 10);
});

// ── planCompaction ───────────────────────────────────────────────────────────

test("planCompaction: returns 'none' below the 80% threshold", () => {
  const directive = planCompaction(
    [], Math.floor(MODEL_CONTEXT_TOKENS * 0.5), MODEL_CONTEXT_TOKENS,
  );
  assert.equal(directive.kind, "none");
});

test("planCompaction: returns 'drop-tools' between 80 and 90%", () => {
  const directive = planCompaction(
    [], Math.floor(MODEL_CONTEXT_TOKENS * 0.85), MODEL_CONTEXT_TOKENS,
  );
  assert.equal(directive.kind, "drop-tools");
});

test("planCompaction: returns 'summarize-old' between 90 and 99%", () => {
  const history: OrchestratorMessage[] = Array.from({ length: 6 }, (_, i) => ({
    role: i % 2 === 0 ? "user" : "assistant",
    content: `msg ${i}`,
  }));
  const directive = planCompaction(
    history, Math.floor(MODEL_CONTEXT_TOKENS * 0.95), MODEL_CONTEXT_TOKENS,
  );
  assert.equal(directive.kind, "summarize-old");
  if (directive.kind === "summarize-old") {
    assert.equal(directive.oldestHalf.length, 3);
  }
});

test("planCompaction: 90% with <2 messages falls through to hard-truncate", () => {
  const directive = planCompaction(
    [{ role: "user", content: "only" }],
    Math.floor(MODEL_CONTEXT_TOKENS * 0.95),
    MODEL_CONTEXT_TOKENS,
  );
  assert.equal(directive.kind, "hard-truncate");
});

test("planCompaction: returns 'hard-truncate' at or above 99%", () => {
  const directive = planCompaction(
    [], Math.floor(MODEL_CONTEXT_TOKENS * 0.999), MODEL_CONTEXT_TOKENS,
  );
  assert.equal(directive.kind, "hard-truncate");
});

test("planCompaction: thresholds are 80/90/99 of model context", () => {
  assert.equal(COMPACT_T1_DROP_TOOLS, 0.80);
  assert.equal(COMPACT_T2_SUMMARIZE, 0.90);
  assert.equal(COMPACT_T3_TRUNCATE, 0.99);
});

// ── applyCompaction ──────────────────────────────────────────────────────────

test("applyCompaction: 'none' returns history unchanged", () => {
  const history: OrchestratorMessage[] = [
    { role: "user", content: "a" },
    { role: "tool", content: "b" },
  ];
  assert.equal(applyCompaction({ kind: "none" }, history), history);
});

test("applyCompaction: 'drop-tools' removes only role:'tool' rows and preserves order", () => {
  const history: OrchestratorMessage[] = [
    { role: "user", content: "first" },
    { role: "tool", content: "tool-result" },
    { role: "assistant", content: "second" },
    { role: "tool", content: "tool-result-2" },
  ];
  const out = applyCompaction({ kind: "drop-tools" }, history);
  assert.deepEqual(out.map(m => m.role), ["user", "assistant"]);
  assert.deepEqual(out.map(m => m.content), ["first", "second"]);
});

test("applyCompaction: 'summarize-old' keeps recent half", () => {
  const history: OrchestratorMessage[] = Array.from({ length: 6 }, (_, i) => ({
    role: i % 2 === 0 ? "user" : "assistant",
    content: `m${i}`,
  }));
  const out = applyCompaction(
    { kind: "summarize-old", oldestHalf: history.slice(0, 3) }, history,
  );
  assert.deepEqual(out.map(m => m.content), ["m3", "m4", "m5"]);
});

test("applyCompaction: 'hard-truncate' keeps newest under budget", () => {
  // 5 messages, each ~20 tokens. Budget for 3 of them.
  const history: OrchestratorMessage[] = Array.from({ length: 5 }, (_, i) => ({
    role: "user" as const,
    content: `m${i}` + "x".repeat(78), // 80 chars / 4 = 20 tokens
  }));
  const out = applyCompaction(
    { kind: "hard-truncate", budgetTokens: 60 }, history,
  );
  // Newest message always survives; with a 60-token budget and 20 tokens/msg
  // we keep ~3 newest in chronological order.
  assert.ok(out.length >= 1 && out.length <= 3);
  assert.equal(out[out.length - 1].content, history[history.length - 1].content);
  // Survivors are a contiguous newest-first slice.
  for (let i = 1; i < out.length; i++) {
    const expectedIdx = history.length - out.length + i;
    assert.equal(out[i].content, history[expectedIdx].content);
  }
});

test("applyCompaction: 'hard-truncate' returns at least one message even if oversized", () => {
  const history: OrchestratorMessage[] = [
    { role: "user", content: "x".repeat(10000) }, // ~2500 tokens
  ];
  const out = applyCompaction(
    { kind: "hard-truncate", budgetTokens: 5 }, history,
  );
  assert.equal(out.length, 1);
});

// ── detectFrustration ───────────────────────────────────────────────────────

test("detectFrustration: matches each shipped pattern", () => {
  const cases: Array<[string, string]> = [
    ["That's wrong",                "wrong/broken assertion"],
    ["This isn't working again",    "still not working"],
    ["Stop doing that",             "stop doing X"],
    ["No, I told you twice",        "repeated correction"],
    ["I give up",                   "give up"],
    ["Never mind",                  "never mind"],
    ["Forget it",                   "forget it"],
    ["ugh",                         "ugh"],
  ];
  for (const [text, label] of cases) {
    assert.equal(detectFrustration(text), label, `text=${text}`);
  }
});

test("detectFrustration: no match on neutral text", () => {
  for (const text of [
    "Please add a unit test for parseCommand.",
    "Could you explain what /clear does?",
    "Run the tests and let me know.",
  ]) {
    assert.equal(detectFrustration(text), null, `text=${text}`);
  }
});

test("detectFrustration: empty / whitespace returns null", () => {
  assert.equal(detectFrustration(""), null);
  assert.equal(detectFrustration("   "), null);
});

// ── TriggerDedup ────────────────────────────────────────────────────────────

test("TriggerDedup: first call returns true, subsequent return false", () => {
  const dedup = new TriggerDedup();
  assert.equal(dedup.shouldFire("k"), true);
  assert.equal(dedup.shouldFire("k"), false);
  assert.equal(dedup.shouldFire("k"), false);
});

test("TriggerDedup: distinct keys fire independently", () => {
  const dedup = new TriggerDedup();
  assert.equal(dedup.shouldFire("a"), true);
  assert.equal(dedup.shouldFire("b"), true);
  assert.equal(dedup.shouldFire("a"), false);
  assert.equal(dedup.shouldFire("b"), false);
});

// ── SpawnTracker.roleFor ────────────────────────────────────────────────────

test("SpawnTracker: roleFor returns the role passed at recordSpawn", () => {
  const t = new SpawnTracker();
  t.recordSpawn(
    "harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "abc" }),
    "reviewer-aux",
  );
  assert.equal(t.roleFor("abc"), "reviewer-aux");
});

test("SpawnTracker: roleFor returns null for unknown handle", () => {
  const t = new SpawnTracker();
  assert.equal(t.roleFor("nope"), null);
});
