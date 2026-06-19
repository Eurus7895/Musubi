/**
 * harness-tier: ephemeral
 * expires-when: models summarise concisely without role injection
 * cost-lever: deletes the summarizer runner
 * (what: Tests for summarizerRunner.ts.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  buildSummarizerSystemPrompt,
  serializeSummarizerBrief,
} from "./summarizerCore";
import type { OrchestratorMessage } from "./orchestratorCore";

// ── serializeSummarizerBrief ─────────────────────────────────────────────────

test("serializeSummarizerBrief: emits one block per turn with [role] prefix", () => {
  const turns: OrchestratorMessage[] = [
    { role: "user", content: "what is parseCommand?" },
    { role: "assistant", content: "It dispatches based on the first token." },
    { role: "tool", content: '{"tool":"harness_spawn_subagent","result":"{}"}' },
  ];
  const out = serializeSummarizerBrief(turns);
  assert.match(out, /^\[user\] what is parseCommand\?/);
  assert.match(out, /\[assistant\] It dispatches /);
  assert.match(out, /\[tool\] \{"tool":"harness_spawn_subagent"/);
});

test("serializeSummarizerBrief: drops empty / whitespace-only turns", () => {
  const turns: OrchestratorMessage[] = [
    { role: "user", content: "real question" },
    { role: "assistant", content: "   " },
    { role: "user", content: "" },
    { role: "assistant", content: "real answer" },
  ];
  const out = serializeSummarizerBrief(turns);
  assert.equal(out.split("\n\n").length, 2);
  assert.match(out, /real question/);
  assert.match(out, /real answer/);
});

test("serializeSummarizerBrief: empty input yields empty string", () => {
  assert.equal(serializeSummarizerBrief([]), "");
});

// ── buildSummarizerSystemPrompt ──────────────────────────────────────────────

test("buildSummarizerSystemPrompt: strips agent frontmatter", () => {
  const agentMd =
    "---\nname: Summarizer\nmodel: claude-sonnet-4.5\n---\n\nYou are the Summarizer.";
  const out = buildSummarizerSystemPrompt(agentMd, null);
  assert.match(out, /You are the Summarizer\./);
  assert.equal(out.includes("model: claude-sonnet-4.5"), false);
});

test("buildSummarizerSystemPrompt: appends skill body under heading when present", () => {
  const out = buildSummarizerSystemPrompt(
    "## Role\nbody",
    "---\nname: summarizer\n---\n\n# summarizer — procedure\n1. parse turns",
  );
  assert.match(out, /## Skill: summarizer \(pushed by harness\)/);
  assert.match(out, /# summarizer — procedure/);
});

test("buildSummarizerSystemPrompt: omits skill section when skill body empty", () => {
  const out = buildSummarizerSystemPrompt("## Role\nbody", "");
  assert.equal(out.includes("## Skill: summarizer"), false);
});

test("buildSummarizerSystemPrompt: omits skill section when skillBody is null", () => {
  const out = buildSummarizerSystemPrompt("body only", null);
  assert.equal(out.trim(), "body only");
});
