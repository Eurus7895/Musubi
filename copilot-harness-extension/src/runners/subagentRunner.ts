/**
 * runners/subagentRunner.ts — vscode shell that drives a pipeline-side
 * sub-agent's LM session (Phase G.1).
 *
 * Lifecycle, given a `handle_id` already created by `harness_spawn_subagent`:
 *
 *   1. fetch firewalled context (`harness_get_subagent_context`)
 *   2. resolve model (`selectModelForAgent` — honours role's `model:`
 *      frontmatter, falls back to claude-sonnet-4.5)
 *   3. build the LM tool surface (intersection of role allow-list,
 *      vscode.lm.tools, and any spawn-time narrowing)
 *   4. loop sendRequest → tool calls → tool results, capped at
 *      `config.maxTurns` cycles
 *   5. capture the final assistant text as the summary
 *   6. call `harness_complete_subagent` with summary + tools_used + turns;
 *      the harness verifies (token cap, secrets, injection) before
 *      flipping the row to `done`
 *
 * Failure modes are routed to `abandonHandle` so the audit row is
 * always terminal — Hard Invariant #8 ("no silent sub-agents").
 *
 * Pure helpers live in subagentRunnerCore.ts; this file holds only the
 * vscode-touching glue.
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import { McpClient } from "../mcpClient";
import { selectModelForAgent } from "../modelSelector";
import {
  buildSubagentSystemPrompt,
  parseCompleteResponse,
  parseSpawnResponse,
  parseSubagentContext,
  resolveLmToolSurface,
  SUBAGENT_ROLE_CONFIGS,
  type SubagentContext,
  type SubagentRoleConfig,
  type SubagentRoleId,
} from "./subagentRunnerCore";

// ── Public types ────────────────────────────────────────────────────────────

export interface RunSubagentForHandleOptions {
  client: McpClient;
  handleId: string;
  role: SubagentRoleId;
  roots: string[];
  log: (msg: string) => void;
  token: vscode.CancellationToken;
  /**
   * Forwarded into every vscode.lm.invokeTool call. Required by tools
   * that produce workspace edits with a user-confirmation UI; harmless
   * to plumb through for read-only tools.
   */
  toolInvocationToken?: vscode.ChatParticipantToolToken;
  /**
   * Test seam: inject a fake `vscode.lm.tools` listing instead of
   * reading the live workbench registry. Production code passes
   * `undefined` and the real registry is used.
   */
  availableLmToolsOverride?: readonly string[];
  /**
   * Test seam: inject a model object instead of going through
   * `selectModelForAgent`. Production code passes `undefined`.
   */
  modelOverride?: vscode.LanguageModelChat;
}

export interface SpawnAndRunSubagentOptions {
  client: McpClient;
  parentSessionId: string;
  parentAgentName: string;
  role: SubagentRoleId;
  brief: string;
  /**
   * Optional spawn-time tool narrowing. Subset of the role's static
   * allow-list — passing a tighter list lets a coder spawn an explorer
   * with only ["Read","Grep"] for a focused scan, for example.
   */
  allowedTools?: readonly string[];
  outputSchema?: Record<string, unknown>;
  roots: string[];
  log: (msg: string) => void;
  token: vscode.CancellationToken;
  toolInvocationToken?: vscode.ChatParticipantToolToken;
  /** Test seams (see RunSubagentForHandleOptions). */
  availableLmToolsOverride?: readonly string[];
  modelOverride?: vscode.LanguageModelChat;
}

export interface RunSubagentResult {
  ok: boolean;
  summary: string | null;
  /** Echo of the role's terminal status (`done` | `failed` | `escalated`). */
  finalStatus: string | null;
  toolsUsed: string[];
  turns: number;
  reason?: string;
  /** Echoed when the harness rejected the summary at completion time. */
  verificationErrors?: string[];
}

// ── Implementation ─────────────────────────────────────────────────────────

const ABANDONED_RESULT = (reason: string): RunSubagentResult => ({
  ok: false, summary: null, finalStatus: "abandoned",
  toolsUsed: [], turns: 0, reason,
});

/**
 * Best-effort completion-as-abandoned. Used on every error path so the
 * audit row never sticks at `running`. Errors swallowed — housekeeping.
 */
async function abandonHandle(
  client: McpClient,
  handleId: string,
  reason: string,
  log: (msg: string) => void,
): Promise<void> {
  try {
    await client.callTool("harness_complete_subagent", {
      handle_id: handleId,
      summary: `[harness] sub-agent abandoned: ${reason}`,
      status: "abandoned",
      turns: 0,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[subagent] abandon failed: ${msg}`);
  }
}

function loadAgentMd(roots: ReadonlyArray<string>, rel: string): string {
  for (const root of roots) {
    try {
      return fs.readFileSync(path.join(root, rel), "utf-8");
    } catch { /* keep looking */ }
  }
  return "";
}

/**
 * Walk the live workbench registry and return the names of LM tools
 * the model could actually invoke. Filtered through
 * `canBeReferencedInPrompt` to skip tools their owners hid from prompt
 * surfaces. The `availableLmToolsOverride` seam in opts lets tests
 * inject a deterministic list instead.
 */
function listAvailableLmTools(override?: readonly string[]): string[] {
  if (override) { return [...override]; }
  return vscode.lm.tools
    .filter(t => (t as { canBeReferencedInPrompt?: boolean }).canBeReferencedInPrompt !== false)
    .map(t => t.name);
}

function buildLmToolDefs(
  surface: readonly string[],
  override?: readonly string[],
): vscode.LanguageModelChatTool[] {
  if (override) {
    // In tests we don't have real LM tool defs; return placeholder
    // entries so the runner can still serialize a request.
    return surface.map(name => ({
      name, description: name, inputSchema: { type: "object" },
    }));
  }
  const live = new Map(vscode.lm.tools.map(t => [t.name, t]));
  const out: vscode.LanguageModelChatTool[] = [];
  for (const name of surface) {
    const t = live.get(name);
    if (!t) { continue; }
    out.push({
      name: t.name,
      description: t.description ?? t.name,
      inputSchema: (t.inputSchema as Record<string, unknown> | undefined) ?? { type: "object" },
    });
  }
  return out;
}

function stringifyToolResult(content: ReadonlyArray<unknown>): string {
  const parts: string[] = [];
  for (const p of content) {
    if (p instanceof vscode.LanguageModelTextPart) { parts.push(p.value); continue; }
    const v = (p as { value?: unknown }).value;
    if (typeof v === "string") { parts.push(v); }
  }
  return parts.join("");
}

/**
 * Run the LM tool-call loop for an already-spawned sub-agent handle.
 * Captures assistant text across cycles, calls `harness_complete_subagent`
 * with the captured summary, and returns the harness-verified result.
 */
export async function runSubagentForHandle(
  opts: RunSubagentForHandleOptions,
): Promise<RunSubagentResult> {
  const {
    client, handleId, role, roots, log, token,
    toolInvocationToken, availableLmToolsOverride, modelOverride,
  } = opts;

  const config: SubagentRoleConfig | undefined = SUBAGENT_ROLE_CONFIGS[role];
  if (!config) {
    await abandonHandle(client, handleId, `unknown role: ${role}`, log);
    return ABANDONED_RESULT(`unknown role: ${role}`);
  }

  // 1. firewalled context
  let ctx: SubagentContext | null;
  try {
    const raw = await client.callTool("harness_get_subagent_context", {
      handle_id: handleId,
    });
    ctx = parseSubagentContext(raw);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[subagent:${role}] get_subagent_context threw: ${msg}`);
    await abandonHandle(client, handleId, `context fetch failed: ${msg}`, log);
    return ABANDONED_RESULT(msg);
  }
  if (!ctx) {
    await abandonHandle(client, handleId, "context fetch returned malformed envelope", log);
    return ABANDONED_RESULT("malformed context envelope");
  }
  if (ctx.role !== role) {
    // The handle exists but for a different role — refuse rather than
    // dispatch the wrong runner. The audit row still terminates.
    await abandonHandle(client, handleId, `role mismatch: ctx=${ctx.role} runner=${role}`, log);
    return ABANDONED_RESULT(`role mismatch: ctx=${ctx.role} runner=${role}`);
  }

  // 2. model
  let model: vscode.LanguageModelChat;
  try {
    model = modelOverride ?? await selectModelForAgent({
      roots, agentName: config.agentName, log,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[subagent:${role}] model selection failed: ${msg}`);
    await abandonHandle(client, handleId, `no model available: ${msg}`, log);
    return ABANDONED_RESULT(msg);
  }

  // 3. LM tool surface
  const availableTools = listAvailableLmTools(availableLmToolsOverride);
  const surface = resolveLmToolSurface({
    harnessAllowedTools: ctx.allowedTools,
    roleDefaultLmTools: config.defaultLmTools,
    availableLmTools: availableTools,
  });
  const lmTools = buildLmToolDefs(surface, availableLmToolsOverride);
  log(`[subagent:${role}] tool surface: ${surface.length} tools — [${surface.join(", ")}]`);

  // 4. system prompt + LM loop
  const agentMd = loadAgentMd(roots, config.agentMdRel);
  const systemPrompt = buildSubagentSystemPrompt(agentMd, ctx.roleSkill, ctx.brief);

  const history: vscode.LanguageModelChatMessage[] = [
    vscode.LanguageModelChatMessage.User(systemPrompt),
  ];
  const summaryBuf: string[] = [];
  const toolsUsed = new Set<string>();
  let turns = 0;

  for (let cycle = 0; cycle < config.maxTurns; cycle++) {
    if (token.isCancellationRequested) {
      await abandonHandle(client, handleId, "cancelled by user", log);
      return ABANDONED_RESULT("cancelled");
    }

    turns = cycle + 1;
    let response: vscode.LanguageModelChatResponse;
    try {
      response = await model.sendRequest(
        history,
        lmTools.length > 0 ? { tools: lmTools } : {},
        token,
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`[subagent:${role}] sendRequest threw: ${msg}`);
      await abandonHandle(client, handleId, `LM call failed: ${msg}`, log);
      return ABANDONED_RESULT(msg);
    }

    let textBuf = "";
    const toolCalls: vscode.LanguageModelToolCallPart[] = [];
    try {
      for await (const part of response.stream) {
        if (part instanceof vscode.LanguageModelTextPart) {
          textBuf += part.value;
        } else if (part instanceof vscode.LanguageModelToolCallPart) {
          toolCalls.push(part);
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`[subagent:${role}] stream consume failed: ${msg}`);
      await abandonHandle(client, handleId, `stream failed: ${msg}`, log);
      return ABANDONED_RESULT(msg);
    }

    if (textBuf.length > 0) { summaryBuf.push(textBuf); }

    // No more tool calls — the model produced its final answer.
    if (toolCalls.length === 0) { break; }

    // Reflect assistant turn + execute tools.
    history.push(vscode.LanguageModelChatMessage.Assistant([
      ...(textBuf.length > 0 ? [new vscode.LanguageModelTextPart(textBuf)] : []),
      ...toolCalls,
    ]));

    const resultParts: vscode.LanguageModelToolResultPart[] = [];
    for (const call of toolCalls) {
      toolsUsed.add(call.name);
      let resultContent: unknown[];
      try {
        const invokeResult = await vscode.lm.invokeTool(
          call.name,
          { input: call.input, toolInvocationToken },
          token,
        );
        resultContent = [...invokeResult.content];
        log(`[subagent:${role}]   tool ${call.name}: ok ${stringifyToolResult(resultContent).length}ch`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        log(`[subagent:${role}]   tool ${call.name}: FAIL — ${msg}`);
        resultContent = [
          new vscode.LanguageModelTextPart(JSON.stringify({ status: "error", error: msg })),
        ];
      }
      resultParts.push(new vscode.LanguageModelToolResultPart(call.callId, resultContent));
    }
    history.push(vscode.LanguageModelChatMessage.User(resultParts));
  }

  const summaryText = summaryBuf.join("").trim();
  if (!summaryText) {
    // Empty model output is a verification fail — record it as abandoned
    // so the parent gets a clear terminal status instead of a 'done' row
    // with no useful summary.
    await abandonHandle(client, handleId, "empty summary from model", log);
    return ABANDONED_RESULT("empty summary");
  }

  // 5. complete — harness verifies.
  let completeRaw: string;
  try {
    completeRaw = await client.callTool("harness_complete_subagent", {
      handle_id: handleId,
      summary: summaryText,
      status: "done",
      turns,
      tools_used: [...toolsUsed],
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[subagent:${role}] complete threw: ${msg}`);
    return {
      ok: false, summary: summaryText, finalStatus: null,
      toolsUsed: [...toolsUsed], turns, reason: msg,
    };
  }

  const parsed = parseCompleteResponse(completeRaw);
  return {
    ok: parsed.ok,
    summary: parsed.summary ?? summaryText,
    finalStatus: parsed.finalStatus ?? null,
    toolsUsed: [...toolsUsed],
    turns,
    reason: parsed.reason,
    verificationErrors: parsed.verificationErrors,
  };
}

/**
 * Spawn → run → await — the synchronous helper a parent (pipeline stage
 * or other runner) calls when it wants a sub-agent's answer inline.
 *
 * Three round-trips with the harness:
 *   1. `harness_spawn_subagent` registers the handle and returns it.
 *   2. `runSubagentForHandle` executes the LM session and writes the
 *      terminal row.
 *   3. `harness_await_subagent` reads back the verified terminal row so
 *      the caller sees the harness's view (post truncation / verification),
 *      not just the runner's local view.
 */
export async function spawnAndRunSubagent(
  opts: SpawnAndRunSubagentOptions,
): Promise<RunSubagentResult> {
  const {
    client, parentSessionId, parentAgentName, role, brief, allowedTools,
    outputSchema, roots, log, token, toolInvocationToken,
    availableLmToolsOverride, modelOverride,
  } = opts;

  const config = SUBAGENT_ROLE_CONFIGS[role];
  if (!config) {
    return { ok: false, summary: null, finalStatus: null,
      toolsUsed: [], turns: 0, reason: `unknown role: ${role}` };
  }

  // 1. spawn
  const spawnArgs: Record<string, unknown> = {
    parent_session_id: parentSessionId,
    parent_agent_name: parentAgentName,
    role,
    brief,
    max_turns: config.maxTurns,
    wall_clock_timeout_s: config.wallClockS,
  };
  if (allowedTools && allowedTools.length > 0) {
    spawnArgs.allowed_tools = [...allowedTools];
  }
  if (outputSchema) {
    spawnArgs.output_schema = JSON.stringify(outputSchema);
  }

  let spawnRaw: string;
  try {
    spawnRaw = await client.callTool("harness_spawn_subagent", spawnArgs);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[subagent:${role}] spawn threw: ${msg}`);
    return { ok: false, summary: null, finalStatus: null,
      toolsUsed: [], turns: 0, reason: msg };
  }
  const spawn = parseSpawnResponse(spawnRaw);
  if (spawn.status !== "spawned" || !spawn.handleId) {
    log(`[subagent:${role}] spawn failed: ${spawn.error}`);
    return { ok: false, summary: null, finalStatus: null,
      toolsUsed: [], turns: 0, reason: spawn.error };
  }
  const handleId = spawn.handleId;

  // 2. run
  const runResult = await runSubagentForHandle({
    client, handleId, role, roots, log, token,
    toolInvocationToken, availableLmToolsOverride, modelOverride,
  });

  // 3. await — surfaces the harness's verified row. If the run failed
  // before reaching `harness_complete_subagent`, the abandon path
  // already wrote a terminal row so the await returns immediately.
  try {
    await client.callTool("harness_await_subagent", {
      handle_id: handleId,
      max_wait_s: 5,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[subagent:${role}] await failed (non-fatal): ${msg}`);
  }

  return runResult;
}
