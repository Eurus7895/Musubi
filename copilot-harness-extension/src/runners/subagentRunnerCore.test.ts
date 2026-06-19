/**
 * harness-tier: ephemeral
 * expires-when: models gain reliable native multi-agent tool-use
 * cost-lever: deletes the sub-agent core
 * (what: Tests for subagentRunnerCore.ts.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as path from "path";

import {
  buildSubagentSystemPrompt,
  parseCompleteResponse,
  parseSpawnResponse,
  parseSubagentContext,
  resolveLmToolSurface,
  SUBAGENT_ROLE_CONFIGS,
  SYMBOLIC_TO_LM_NAMES,
  type SubagentRoleId,
} from "./subagentRunnerCore";

// ── parseSubagentContext ─────────────────────────────────────────────────────

test("parseSubagentContext: returns null on empty input", () => {
  assert.equal(parseSubagentContext(""), null);
});

test("parseSubagentContext: returns null on non-JSON input", () => {
  assert.equal(parseSubagentContext("not json"), null);
});

test("parseSubagentContext: returns null when status is not ok", () => {
  assert.equal(parseSubagentContext('{"status":"error","brief":"x","role":"explorer"}'), null);
});

test("parseSubagentContext: returns null when brief or role missing", () => {
  assert.equal(parseSubagentContext('{"status":"ok","role":"explorer"}'), null);
  assert.equal(parseSubagentContext('{"status":"ok","brief":"x"}'), null);
});

test("parseSubagentContext: parses well-formed envelope", () => {
  const out = parseSubagentContext(JSON.stringify({
    status: "ok",
    brief: "find callers of foo",
    role: "explorer",
    role_skill: "# Explorer skill body",
    allowed_tools: ["Read", "Grep"],
  }));
  assert.deepEqual(out, {
    brief: "find callers of foo",
    role: "explorer",
    roleSkill: "# Explorer skill body",
    allowedTools: ["Read", "Grep"],
  });
});

test("parseSubagentContext: tolerates missing role_skill / allowed_tools", () => {
  const out = parseSubagentContext(JSON.stringify({
    status: "ok", brief: "x", role: "explorer",
  }));
  assert.deepEqual(out, {
    brief: "x", role: "explorer", roleSkill: null, allowedTools: [],
  });
});

test("parseSubagentContext: filters non-string allowed_tools entries", () => {
  const out = parseSubagentContext(JSON.stringify({
    status: "ok", brief: "x", role: "explorer",
    allowed_tools: ["Read", 42, null, "Grep"],
  }));
  assert.deepEqual(out!.allowedTools, ["Read", "Grep"]);
});

// ── parseSpawnResponse ───────────────────────────────────────────────────────

test("parseSpawnResponse: empty input returns error", () => {
  const r = parseSpawnResponse("");
  assert.equal(r.status, "error");
  assert.match(r.error ?? "", /empty/);
});

test("parseSpawnResponse: malformed JSON returns error", () => {
  const r = parseSpawnResponse("oops {");
  assert.equal(r.status, "error");
  assert.match(r.error ?? "", /non-JSON/);
});

test("parseSpawnResponse: success case extracts handle_id + effective_tools", () => {
  const r = parseSpawnResponse(JSON.stringify({
    status: "spawned",
    handle_id: "abc123",
    effective_tools: ["Read", "Grep"],
  }));
  assert.equal(r.status, "spawned");
  assert.equal(r.handleId, "abc123");
  assert.deepEqual(r.effectiveTools, ["Read", "Grep"]);
});

test("parseSpawnResponse: missing handle_id is treated as error", () => {
  const r = parseSpawnResponse(JSON.stringify({ status: "spawned" }));
  assert.equal(r.status, "error");
});

test("parseSpawnResponse: server error string passes through", () => {
  const r = parseSpawnResponse(JSON.stringify({
    status: "error", error: "policy denied",
  }));
  assert.equal(r.status, "error");
  assert.equal(r.error, "policy denied");
});

// ── parseCompleteResponse ────────────────────────────────────────────────────

test("parseCompleteResponse: ok=true on recorded+done", () => {
  const r = parseCompleteResponse(JSON.stringify({
    status: "recorded",
    final_status: "done",
    summary: "all good",
  }));
  assert.equal(r.ok, true);
  assert.equal(r.finalStatus, "done");
  assert.equal(r.summary, "all good");
});

test("parseCompleteResponse: ok=false when verification rejected", () => {
  const r = parseCompleteResponse(JSON.stringify({
    status: "rejected",
    verification_errors: ["secret detected", "summary too long"],
  }));
  assert.equal(r.ok, false);
  assert.deepEqual(r.verificationErrors, ["secret detected", "summary too long"]);
  assert.match(r.reason ?? "", /secret detected/);
});

test("parseCompleteResponse: escalated final_status reported but ok=false", () => {
  const r = parseCompleteResponse(JSON.stringify({
    status: "recorded", final_status: "escalated", summary: "ran out of turns",
  }));
  assert.equal(r.ok, false);
  assert.equal(r.finalStatus, "escalated");
});

test("parseCompleteResponse: malformed input returns ok=false with reason", () => {
  const r = parseCompleteResponse("not json");
  assert.equal(r.ok, false);
  assert.match(r.reason ?? "", /non-JSON/);
});

// ── buildSubagentSystemPrompt ────────────────────────────────────────────────

test("buildSubagentSystemPrompt: strips agent.md frontmatter", () => {
  const out = buildSubagentSystemPrompt(
    "---\nname: Explorer\nmodel: claude-sonnet-4.5\n---\n\nYou are the Explorer.",
    null,
    "find callers of foo",
  );
  assert.match(out, /You are the Explorer\./);
  assert.equal(out.includes("model: claude-sonnet-4.5"), false);
});

test("buildSubagentSystemPrompt: appends skill body under heading when present", () => {
  const out = buildSubagentSystemPrompt(
    "## Role\nbody",
    "# Skill\nrules",
    "brief here",
  );
  assert.match(out, /## Skill \(pushed by harness\)/);
  assert.match(out, /# Skill\nrules/);
});

test("buildSubagentSystemPrompt: omits skill section when skill is empty / null", () => {
  const a = buildSubagentSystemPrompt("body", null, "brief");
  const b = buildSubagentSystemPrompt("body", "", "brief");
  assert.equal(a.includes("## Skill"), false);
  assert.equal(b.includes("## Skill"), false);
});

test("buildSubagentSystemPrompt: always appends the brief under '## Brief'", () => {
  const out = buildSubagentSystemPrompt("body", null, "the actual brief");
  assert.match(out, /## Brief\n\nthe actual brief/);
});

// ── resolveLmToolSurface ─────────────────────────────────────────────────────

test("resolveLmToolSurface: translates symbolic Read/Grep into concrete LM tool names", () => {
  const out = resolveLmToolSurface({
    harnessAllowedTools: ["Read", "Grep"],
    roleDefaultLmTools: [],
    availableLmTools: [
      "copilot_readFile", "read_file",
      "copilot_searchWorkspace", "grep_search",
      "copilot_runInTerminal", // intentionally unused
    ],
  });
  assert.deepEqual(out, [
    "copilot_readFile", "copilot_searchWorkspace",
    "grep_search", "read_file",
  ]);
});

test("resolveLmToolSurface: drops names the workbench has not registered", () => {
  const out = resolveLmToolSurface({
    harnessAllowedTools: ["Read"],
    roleDefaultLmTools: [],
    availableLmTools: ["copilot_readFile"], // read_file not registered
  });
  assert.deepEqual(out, ["copilot_readFile"]);
});

test("resolveLmToolSurface: falls back to roleDefaultLmTools when harness list is empty", () => {
  const out = resolveLmToolSurface({
    harnessAllowedTools: [],
    roleDefaultLmTools: ["copilot_readFile", "read_file"],
    availableLmTools: ["copilot_readFile"],
  });
  assert.deepEqual(out, ["copilot_readFile"]);
});

test("resolveLmToolSurface: passes through already-concrete tool names", () => {
  const out = resolveLmToolSurface({
    harnessAllowedTools: ["copilot_readFile"],
    roleDefaultLmTools: [],
    availableLmTools: ["copilot_readFile", "grep_search"],
  });
  assert.deepEqual(out, ["copilot_readFile"]);
});

test("resolveLmToolSurface: returns sorted, deduplicated output", () => {
  const out = resolveLmToolSurface({
    harnessAllowedTools: ["Read", "Read", "View"],
    roleDefaultLmTools: [],
    availableLmTools: ["copilot_readFile", "read_file"],
  });
  // both Read and View map to the same concrete tools — should be unique + sorted.
  assert.deepEqual(out, ["copilot_readFile", "read_file"]);
});

// ── SUBAGENT_ROLE_CONFIGS coverage + drift check ─────────────────────────────

test("SUBAGENT_ROLE_CONFIGS: all three runner roles present", () => {
  const ids: SubagentRoleId[] = ["explorer", "investigator", "reviewer-aux"];
  for (const id of ids) {
    const cfg = SUBAGENT_ROLE_CONFIGS[id];
    assert.ok(cfg, `missing config for ${id}`);
    assert.equal(cfg.role, id);
    assert.ok(cfg.maxTurns >= 1, `${id}.maxTurns must be ≥ 1`);
    assert.ok(cfg.wallClockS >= 30, `${id}.wallClockS too low`);
    assert.ok(cfg.defaultLmTools.length >= 1, `${id}.defaultLmTools empty`);
  }
});

test("SUBAGENT_ROLE_CONFIGS: defaultLmTools matches each agent.md::lm_tools (drift check)", () => {
  // Drift guard — if someone edits agent.md and forgets the runtime fallback,
  // this test fails loudly. Skips when the agent files aren't bundled (e.g.
  // running tests against a stripped checkout) so CI in non-monorepo
  // installs doesn't break.
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  for (const role of ["explorer", "investigator", "reviewer-aux"] as const) {
    const cfg = SUBAGENT_ROLE_CONFIGS[role];
    const agentPath = path.join(repoRoot, cfg.agentMdRel);
    if (!fs.existsSync(agentPath)) { continue; }
    const md = fs.readFileSync(agentPath, "utf-8");
    const fmEnd = md.indexOf("\n---", 3);
    if (!md.startsWith("---") || fmEnd < 0) { continue; }
    // Append a newline so the list-item regex matches even when the
    // closing `---` immediately follows the last entry (slice cuts the
    // trailing `\n`).
    const frontmatter = md.slice(3, fmEnd) + "\n";
    // Crude lm_tools list extractor — looks for the `lm_tools:` block and
    // pulls every "- name" entry until the next top-level key.
    const m = frontmatter.match(/lm_tools:\s*\n((?:\s+#.*\n|\s*-\s*\S+.*\n)+)/);
    if (!m) { continue; }
    const declared: string[] = [];
    for (const line of m[1].split(/\n/)) {
      const dashMatch = line.match(/^\s*-\s*(\S+)/);
      if (dashMatch) { declared.push(dashMatch[1]); }
    }
    assert.deepEqual(
      [...cfg.defaultLmTools].sort(),
      declared.sort(),
      `${role}.defaultLmTools drifted from ${cfg.agentMdRel}::lm_tools`,
    );
  }
});

test("SYMBOLIC_TO_LM_NAMES: every entry maps to non-empty concrete list", () => {
  for (const [sym, list] of Object.entries(SYMBOLIC_TO_LM_NAMES)) {
    assert.ok(list.length >= 1, `${sym} has empty mapping`);
    for (const c of list) {
      assert.equal(typeof c, "string");
      assert.notEqual(c.length, 0);
    }
  }
});
