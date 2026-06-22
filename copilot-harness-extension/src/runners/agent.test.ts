/**
 * harness-tier: ephemeral
 * expires-when: models reliably drive multi-turn tool-use without compensation
 * cost-lever: deletes the agent runner; the agent.py path becomes primary
 * (what: Tests for agent.ts.)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import {
  AGENT_NAME,
  AGENT_TOOL_NAMES,
  AGENT_TOOLS,
  buildAgentSystemPrompt,
  cleanupOutstandingSubagents,
  dispatchAgentTool,
  loadAgentPrompts,
  SpawnTracker,
  stripFrontmatter,
  type ToolDispatchClient,
} from "./agentCore";

// ── stripFrontmatter ─────────────────────────────────────────────────

test("stripFrontmatter: removes YAML frontmatter and leading newlines", () => {
  const input = "---\nname: foo\nversion: 1.0.0\n---\n\nbody text\n";
  assert.equal(stripFrontmatter(input), "body text\n");
});

test("stripFrontmatter: returns input unchanged when no frontmatter", () => {
  assert.equal(stripFrontmatter("# heading\nbody"), "# heading\nbody");
});

test("stripFrontmatter: returns input unchanged when frontmatter is unterminated", () => {
  const input = "---\nname: foo\nbody never closes";
  assert.equal(stripFrontmatter(input), input);
});

// ── buildAgentSystemPrompt ────────────────────────────────────

test("buildAgentSystemPrompt: includes agent body without frontmatter", () => {
  const out = buildAgentSystemPrompt({
    agentMd: "---\nname: Agent\n---\n\nYou are the Agent.",
    routingSkill: "",
  });
  assert.match(out, /You are the Agent\./);
  assert.equal(out.includes("name: Agent"), false);
});

test("buildAgentSystemPrompt: appends routing skill section header", () => {
  const out = buildAgentSystemPrompt({
    agentMd: "agent body",
    routingSkill: "---\nname: agent-routing\n---\n\nrouting rules.",
  });
  assert.match(out, /## Skill: agent-routing/);
  assert.match(out, /routing rules\./);
});

test("buildAgentSystemPrompt: appends Tier-1 memory when present", () => {
  const out = buildAgentSystemPrompt({
    agentMd: "agent body",
    routingSkill: "",
    memoryTier1: "  decision A; decision B  ",
    tier2Available: ["architecture.md"],
  });
  assert.match(out, /## Project memory \(Tier 1\)/);
  assert.match(out, /decision A; decision B/);
  assert.match(out, /Tier 2 entries available on demand .* architecture\.md/);
});

test("buildAgentSystemPrompt: omits memory section when memoryTier1 is empty", () => {
  const out = buildAgentSystemPrompt({
    agentMd: "agent body",
    routingSkill: "",
    memoryTier1: "   ",
  });
  assert.equal(out.includes("Project memory"), false);
  assert.equal(out.includes("Tier 2 entries"), false);
});

// ── AGENT_TOOLS shape ─────────────────────────────────────────

test("AGENT_TOOLS: exposes the Phase B.2 sub-agent tools + pull-on-demand skill tool", () => {
  // After the Hard Invariant #2 relaxation, harness_get_skill joined
  // the always-advertised set so the agent can pull skill content
  // instead of paying for it in the system prompt every turn.
  assert.deepEqual(
    AGENT_TOOLS.map(t => t.name).sort(),
    [
      "harness_await_subagent",
      "harness_get_skill",
      "harness_list_subagents",
      "harness_spawn_subagent",
    ],
  );
});

test("AGENT_TOOL_NAMES matches AGENT_TOOLS", () => {
  assert.deepEqual(
    [...AGENT_TOOL_NAMES].sort(),
    AGENT_TOOLS.map(t => t.name).sort(),
  );
});

test("AGENT_TOOLS: spawn requires role + brief", () => {
  const spawn = AGENT_TOOLS.find(t => t.name === "harness_spawn_subagent")!;
  const schema = spawn.inputSchema as { required?: string[] };
  assert.ok(Array.isArray(schema.required));
  assert.deepEqual([...schema.required!].sort(), ["brief", "role"]);
});

test("AGENT_TOOLS: await requires handle_id", () => {
  const wait = AGENT_TOOLS.find(t => t.name === "harness_await_subagent")!;
  const schema = wait.inputSchema as { required?: string[] };
  assert.deepEqual(schema.required, ["handle_id"]);
});

// ── dispatchAgentTool ─────────────────────────────────────────

class StubClient implements ToolDispatchClient {
  calls: Array<{ name: string; args: Record<string, unknown> }> = [];
  responses: Map<string, string> = new Map();

  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    this.calls.push({ name, args });
    return this.responses.get(name) ?? "{}";
  }
}

const ctx = { parentSessionId: "sess-123", parentAgentName: AGENT_NAME };

test("dispatchAgentTool: spawn injects parent_session_id + parent_agent_name", async () => {
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_spawn_subagent", {
    role: "explorer",
    brief: "find callers of foo",
  });
  assert.equal(stub.calls.length, 1);
  assert.equal(stub.calls[0].name, "harness_spawn_subagent");
  assert.equal(stub.calls[0].args.parent_session_id, "sess-123");
  assert.equal(stub.calls[0].args.parent_agent_name, "agent");
  assert.equal(stub.calls[0].args.role, "explorer");
  assert.equal(stub.calls[0].args.brief, "find callers of foo");
});

test("dispatchAgentTool: spawn omits allowed_tools / output_schema when not provided", async () => {
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_spawn_subagent", {
    role: "explorer", brief: "x",
  });
  assert.equal(stub.calls[0].args.allowed_tools, undefined);
  assert.equal(stub.calls[0].args.output_schema, undefined);
});

test("dispatchAgentTool: spawn JSON-encodes output_schema before forwarding", async () => {
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_spawn_subagent", {
    role: "explorer",
    brief: "x",
    output_schema: { required: ["answer"] },
  });
  assert.equal(typeof stub.calls[0].args.output_schema, "string");
  assert.equal(stub.calls[0].args.output_schema, JSON.stringify({ required: ["answer"] }));
});

test("dispatchAgentTool: await passes through handle_id and max_wait_s", async () => {
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_await_subagent", {
    handle_id: "abc123", max_wait_s: 30,
  });
  assert.equal(stub.calls[0].args.handle_id, "abc123");
  assert.equal(stub.calls[0].args.max_wait_s, 30);
});

test("dispatchAgentTool: await defaults max_wait_s to 30 when caller omits it", async () => {
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_await_subagent", {
    handle_id: "abc",
  });
  assert.equal(stub.calls[0].args.max_wait_s, 30);
});

test("dispatchAgentTool: list_subagents forwards parent_agent_name as main_agent_name", async () => {
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_list_subagents", {});
  assert.equal(stub.calls[0].args.main_agent_name, "agent");
  assert.equal(stub.calls[0].args.parent_session_id, undefined);
});

test("dispatchAgentTool: rejects unknown tool name", async () => {
  const stub = new StubClient();
  await assert.rejects(
    () => dispatchAgentTool(stub, ctx, "harness_destroy_universe", {}),
    /unknown agent tool/,
  );
});

test("dispatchAgentTool: get_skill forwards skill_id + parent agent_name", async () => {
  // The LM passes skill_id only; the dispatcher fills in agent_name from
  // the dispatch context so the LM can't request a skill outside the
  // agent's allowlist by passing a bogus name.
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_get_skill", {
    skill_id: "agent-routing",
  });
  assert.equal(stub.calls[0].name, "harness_get_skill");
  assert.equal(stub.calls[0].args.skill_id, "agent-routing");
  assert.equal(stub.calls[0].args.agent_name, ctx.parentAgentName);
});

test("dispatchAgentTool: get_skill defaults to empty string when skill_id is non-string", async () => {
  // The MCP tool will reject the empty string fail-closed, but the
  // dispatcher itself shouldn't crash on a malformed args dict.
  const stub = new StubClient();
  await dispatchAgentTool(stub, ctx, "harness_get_skill", {});
  assert.equal(stub.calls[0].args.skill_id, "");
});

// ── SpawnTracker ─────────────────────────────────────────────────────

test("SpawnTracker: records handle_id on successful spawn", () => {
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "h1" }));
  assert.deepEqual(tracker.outstandingHandles(), ["h1"]);
});

test("SpawnTracker: ignores non-spawn tools", () => {
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_list_subagents",
    JSON.stringify({ handle_id: "should-not-track" }));
  assert.deepEqual(tracker.outstandingHandles(), []);
});

test("SpawnTracker: ignores malformed JSON gracefully", () => {
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_spawn_subagent", "not json");
  assert.deepEqual(tracker.outstandingHandles(), []);
});

test("SpawnTracker: recordAwait clears handle on terminal status", () => {
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "h1" }));
  tracker.recordAwait(JSON.stringify({ status: "recorded", handle_id: "h1" }));
  assert.deepEqual(tracker.outstandingHandles(), []);
});

test("SpawnTracker: recordAwait does NOT clear handle on still-running status", () => {
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "h1" }));
  tracker.recordAwait(JSON.stringify({ status: "pending", handle_id: "h1" }));
  assert.deepEqual(tracker.outstandingHandles(), ["h1"]);
});

// ── cleanupOutstandingSubagents ──────────────────────────────────────

test("cleanupOutstandingSubagents: calls harness_complete_subagent with abandoned status", async () => {
  const stub = new StubClient();
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "h1" }));
  tracker.recordSpawn("harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "h2" }));

  await cleanupOutstandingSubagents(stub, tracker);

  assert.equal(stub.calls.length, 2);
  const handles = stub.calls.map(c => c.args.handle_id).sort();
  assert.deepEqual(handles, ["h1", "h2"]);
  for (const c of stub.calls) {
    assert.equal(c.name, "harness_complete_subagent");
    assert.equal(c.args.status, "abandoned");
  }
});

test("cleanupOutstandingSubagents: swallows errors so cleanup is best-effort", async () => {
  const failing: ToolDispatchClient = {
    callTool: async () => { throw new Error("network down"); },
  };
  const tracker = new SpawnTracker();
  tracker.recordSpawn("harness_spawn_subagent",
    JSON.stringify({ status: "spawned", handle_id: "h1" }));
  // Should not throw.
  await cleanupOutstandingSubagents(failing, tracker);
});

test("cleanupOutstandingSubagents: noop when no outstanding handles", async () => {
  const stub = new StubClient();
  await cleanupOutstandingSubagents(stub, new SpawnTracker());
  assert.equal(stub.calls.length, 0);
});

// ── loadAgentPrompts ──────────────────────────────────────────

test("loadAgentPrompts: reads agent.md + routing skill from first existing root", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "orch-test-"));
  try {
    fs.mkdirSync(path.join(tmp, ".github", "agents"), { recursive: true });
    fs.mkdirSync(path.join(tmp, ".github", "skills", "agent-routing"), { recursive: true });
    fs.writeFileSync(
      path.join(tmp, ".github", "agents", "agent.agent.md"),
      "AGENT-BODY", "utf-8",
    );
    fs.writeFileSync(
      path.join(tmp, ".github", "skills", "agent-routing", "SKILL.md"),
      "ROUTING-SKILL", "utf-8",
    );

    const out = loadAgentPrompts([tmp]);
    assert.equal(out.agentMd, "AGENT-BODY");
    assert.equal(out.routingSkill, "ROUTING-SKILL");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("loadAgentPrompts: returns empty strings when files missing", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "orch-test-"));
  try {
    const out = loadAgentPrompts([tmp]);
    assert.equal(out.agentMd, "");
    assert.equal(out.routingSkill, "");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("loadAgentPrompts: workspace root takes priority over fallback", () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "orch-work-"));
  const bundle = fs.mkdtempSync(path.join(os.tmpdir(), "orch-bundle-"));
  try {
    fs.mkdirSync(path.join(work, ".github", "agents"), { recursive: true });
    fs.mkdirSync(path.join(bundle, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(work, ".github", "agents", "agent.agent.md"),
      "FROM-WORK", "utf-8",
    );
    fs.writeFileSync(
      path.join(bundle, ".github", "agents", "agent.agent.md"),
      "FROM-BUNDLE", "utf-8",
    );

    const out = loadAgentPrompts([work, bundle]);
    assert.equal(out.agentMd, "FROM-WORK");
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
    fs.rmSync(bundle, { recursive: true, force: true });
  }
});
