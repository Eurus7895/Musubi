/**
 * runners/orchestratorCore.ts — Phase B.2 orchestrator helpers.
 *
 * Pure helpers (no vscode imports) so they can be unit-tested with
 * node:test. The vscode-using shell lives in runners/orchestrator.ts.
 *
 * What's here:
 *   • Tool definitions (data-only, LanguageModelChatTool-shaped)
 *   • System-prompt assembly + frontmatter stripping
 *   • MCP dispatch from LLM-facing args → MCP-tool args
 *   • SpawnTracker — bookkeeping for outstanding handles per turn
 *   • cleanupOutstandingSubagents — best-effort sweep at turn end
 *   • loadOrchestratorPrompts — disk loader for agent.md + routing skill
 */

import * as fs from "fs";
import * as path from "path";

export const ORCHESTRATOR_AGENT_NAME = "orchestrator";

export const ORCHESTRATOR_TOOL_NAMES = [
  "harness_spawn_subagent",
  "harness_await_subagent",
  "harness_list_subagents",
] as const;

export type OrchestratorToolName = typeof ORCHESTRATOR_TOOL_NAMES[number];

export interface OrchestratorTool {
  name: OrchestratorToolName;
  description: string;
  inputSchema: Record<string, unknown>;
}

/**
 * LanguageModelChatTool-shaped definitions. Kept as plain data so they can
 * be serialised, snapshot-tested, and passed to either `request.tools` or
 * `vscode.lm.registerTool` without dragging vscode into pure tests.
 */
export const ORCHESTRATOR_TOOLS: readonly OrchestratorTool[] = [
  {
    name: "harness_spawn_subagent",
    description:
      "Spawn a read-only sub-agent (explorer, investigator, reviewer-aux, planner, " +
      "coder, reviewer) with a one-sentence brief. Returns a handle_id you must " +
      "pass to harness_await_subagent. The sub-agent sees only the brief — no " +
      "conversation history, memory, or other sub-agent results.",
    inputSchema: {
      type: "object",
      properties: {
        role: {
          type: "string",
          description: "Sub-agent role from the spawn allow-list.",
        },
        brief: {
          type: "string",
          description: "One-sentence task description. The sub-agent sees only this.",
        },
        allowed_tools: {
          type: "array",
          items: { type: "string" },
          description:
            "Optional intersection with the role's tool set (e.g. ['Read','Grep']). " +
            "Omit to grant the role's full allow-list.",
        },
        output_schema: {
          type: "object",
          description:
            "Optional JSON schema describing the structured payload you expect back.",
        },
      },
      required: ["role", "brief"],
      additionalProperties: false,
    },
  },
  {
    name: "harness_await_subagent",
    description:
      "Block until a spawned sub-agent reaches a terminal state (done, failed, " +
      "escalated, abandoned) or its wall-clock cap fires. Returns the sub-agent's " +
      "summary + structured payload.",
    inputSchema: {
      type: "object",
      properties: {
        handle_id: {
          type: "string",
          description: "handle_id returned by harness_spawn_subagent.",
        },
        max_wait_s: {
          type: "number",
          description: "Optional wait cap in seconds (defaults to 60).",
        },
      },
      required: ["handle_id"],
      additionalProperties: false,
    },
  },
  {
    name: "harness_list_subagents",
    description:
      "List the sub-agent roles you are permitted to spawn, with each role's " +
      "tool allow-list. Call before spawning when unsure which role fits.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
  },
];

/** Maximum number of (LLM round-trip → tool dispatch) cycles per user turn. */
export const MAX_TOOL_CYCLES = 8;

/** Cap on the chat history we replay per turn. Phase C replaces this. */
export const MAX_HISTORY_TURNS = 10;

// ── Frontmatter stripping ────────────────────────────────────────────────────

export function stripFrontmatter(text: string): string {
  if (!text.startsWith("---")) { return text; }
  const end = text.indexOf("\n---", 3);
  if (end === -1) { return text; }
  return text.slice(end + 4).replace(/^\n+/, "");
}

// ── System prompt builder ────────────────────────────────────────────────────

export interface OrchestratorPromptInputs {
  agentMd: string;
  routingSkill: string;
  memoryTier1?: string | null;
  tier2Available?: readonly string[];
}

export function buildOrchestratorSystemPrompt(input: OrchestratorPromptInputs): string {
  const parts: string[] = [stripFrontmatter(input.agentMd).trim()];

  if (input.routingSkill && input.routingSkill.trim().length > 0) {
    parts.push(
      "\n\n## Skill: orchestrator-routing (pushed by harness)\n\n" +
      stripFrontmatter(input.routingSkill).trim()
    );
  }

  const mem = (input.memoryTier1 ?? "").trim();
  if (mem.length > 0) {
    parts.push("\n\n## Project memory (Tier 1)\n\n" + mem);
    if (input.tier2Available && input.tier2Available.length > 0) {
      parts.push(
        "\n\nTier 2 entries available on demand via `harness_get_memory_entry`: " +
        input.tier2Available.join(", ")
      );
    }
  }

  return parts.join("");
}

// ── MCP dispatch ─────────────────────────────────────────────────────────────

export interface ToolDispatchContext {
  parentSessionId: string;
  parentAgentName: string;
}

export interface ToolDispatchClient {
  callTool(name: string, args: Record<string, unknown>): Promise<string>;
}

/**
 * Dispatch a single orchestrator tool call to the harness. Translates the
 * LLM-facing argument shape into the MCP-tool argument shape and returns
 * the raw JSON string the MCP tool produced. The caller is responsible
 * for feeding it back to the model.
 */
export async function dispatchOrchestratorTool(
  client: ToolDispatchClient,
  ctx: ToolDispatchContext,
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  switch (toolName) {
    case "harness_spawn_subagent": {
      const out: Record<string, unknown> = {
        parent_session_id: ctx.parentSessionId,
        parent_agent_name: ctx.parentAgentName,
        role: args.role,
        brief: args.brief,
      };
      if (Array.isArray(args.allowed_tools)) {
        out.allowed_tools = args.allowed_tools;
      }
      if (args.output_schema && typeof args.output_schema === "object") {
        out.output_schema = JSON.stringify(args.output_schema);
      }
      return client.callTool("harness_spawn_subagent", out);
    }
    case "harness_await_subagent": {
      const out: Record<string, unknown> = {
        handle_id: args.handle_id,
      };
      if (typeof args.max_wait_s === "number") { out.max_wait_s = args.max_wait_s; }
      return client.callTool("harness_await_subagent", out);
    }
    case "harness_list_subagents":
      return client.callTool("harness_list_subagents", {
        main_agent_name: ctx.parentAgentName,
      });
    default:
      throw new Error(`unknown orchestrator tool: ${toolName}`);
  }
}

// ── Spawn tracker (turn-end cleanup bookkeeping) ─────────────────────────────

export class SpawnTracker {
  private readonly outstanding = new Set<string>();

  recordSpawn(toolName: string, mcpResultJson: string): void {
    if (toolName !== "harness_spawn_subagent") { return; }
    try {
      const parsed = JSON.parse(mcpResultJson) as { handle_id?: string };
      if (typeof parsed.handle_id === "string") {
        this.outstanding.add(parsed.handle_id);
      }
    } catch {
      // server returned non-JSON; nothing to track
    }
  }

  recordAwait(mcpResultJson: string): void {
    try {
      const parsed = JSON.parse(mcpResultJson) as {
        status?: string; handle_id?: string;
      };
      if (parsed.status === "recorded" && typeof parsed.handle_id === "string") {
        this.outstanding.delete(parsed.handle_id);
      }
    } catch {
      // ignore
    }
  }

  outstandingHandles(): string[] {
    return [...this.outstanding];
  }
}

/**
 * Best-effort sweep at user-turn-end. Marks any sub-agent the orchestrator
 * spawned-but-never-awaited as 'abandoned'. Errors are swallowed — this
 * is housekeeping, not correctness.
 */
export async function cleanupOutstandingSubagents(
  client: ToolDispatchClient,
  tracker: SpawnTracker,
): Promise<void> {
  for (const handle of tracker.outstandingHandles()) {
    try {
      await client.callTool("harness_complete_subagent", {
        handle_id: handle,
        status: "abandoned",
        summary: "[harness] orchestrator turn ended before await",
        turns: 0,
      });
    } catch {
      // ignore — best-effort cleanup
    }
  }
}

// ── Disk loaders ─────────────────────────────────────────────────────────────

function readFirstExisting(roots: string[], rel: string): string | null {
  for (const root of roots) {
    const p = path.join(root, rel);
    try { return fs.readFileSync(p, "utf-8"); } catch { /* keep looking */ }
  }
  return null;
}

export interface LoadedPrompts {
  agentMd: string;
  routingSkill: string;
}

/** Load orchestrator agent.md + routing skill from .github/. */
export function loadOrchestratorPrompts(roots: string[]): LoadedPrompts {
  const agentMd = readFirstExisting(roots, path.join(".github", "agents", "orchestrator.agent.md"))
    ?? "";
  const routingSkill = readFirstExisting(
    roots, path.join(".github", "skills", "orchestrator-routing", "SKILL.md"),
  ) ?? "";
  return { agentMd, routingSkill };
}
