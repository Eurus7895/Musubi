/**
 * runners/orchestrator.ts — Phase B.2 vscode shell for the orchestrator.
 *
 * The orchestrator holds the persistent chat with the user across turns,
 * replays prior turns from the chat context, and spawns read-only
 * sub-agents through real `vscode.lm` tool calls. Pure helpers (system-
 * prompt assembly, MCP dispatch, spawn tracking) live in orchestratorCore.ts
 * so they can be unit-tested without a vscode runtime.
 *
 * Phase C will replace the per-turn parent session with a chat-keyed
 * conversation row + reactive compaction; B.2 wires only the structural
 * skeleton + makes the spawn / await / list tool calls real.
 */

import * as vscode from "vscode";
import { McpClient } from "../mcpClient";
import { selectModelForAgent } from "../modelSelector";
import {
  buildOrchestratorSystemPrompt,
  cleanupOutstandingSubagents,
  dispatchOrchestratorTool,
  loadOrchestratorPrompts,
  MAX_HISTORY_TURNS,
  MAX_TOOL_CYCLES,
  ORCHESTRATOR_AGENT_NAME,
  ORCHESTRATOR_TOOLS,
  SpawnTracker,
  type ToolDispatchContext,
} from "./orchestratorCore";

export {
  ORCHESTRATOR_AGENT_NAME,
  ORCHESTRATOR_TOOLS,
} from "./orchestratorCore";

// ── Memory fetcher (vscode-free wrapper around McpClient) ────────────────────

interface MemoryContext {
  tier1_index?: string;
  tier2_available?: string[];
}

async function fetchMemoryContext(client: McpClient): Promise<MemoryContext> {
  try {
    const raw = await client.callTool("harness_get_memory_context", {});
    const parsed = JSON.parse(raw) as MemoryContext;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

async function createOrchestratorSession(
  client: McpClient,
  request: string,
): Promise<string> {
  const raw = await client.callTool("harness_new_session", { request });
  const parsed = JSON.parse(raw) as { session_id?: string };
  if (!parsed.session_id) {
    throw new Error("harness_new_session returned no session_id");
  }
  return parsed.session_id;
}

// ── Chat-history replay (mirrors extension.ts::extractChatHistory) ───────────

/**
 * Pull recent user/assistant turns out of the VS Code ChatContext for the
 * harness participant only. Capped at maxTurns most-recent pairs; older
 * turns drop. Phase C replaces this with the conversations table.
 */
export function extractChatHistory(
  chatContext: vscode.ChatContext,
  maxTurns: number,
  participantId: string = "copilot-harness.harness",
): vscode.LanguageModelChatMessage[] {
  const relevant: vscode.ChatContext["history"][number][] = [];
  for (const turn of chatContext.history) {
    const tid = (turn as { participant?: string }).participant;
    if (tid && tid !== participantId) { continue; }
    relevant.push(turn);
  }
  const sliced = relevant.slice(-maxTurns * 2);
  const messages: vscode.LanguageModelChatMessage[] = [];
  for (const turn of sliced) {
    const asRequest = turn as { prompt?: string };
    const asResponse = turn as { response?: ReadonlyArray<unknown> };
    if (typeof asRequest.prompt === "string" && asRequest.prompt.length > 0) {
      messages.push(vscode.LanguageModelChatMessage.User(asRequest.prompt));
      continue;
    }
    if (Array.isArray(asResponse.response)) {
      const text = asResponse.response
        .map(part => {
          const p = part as { value?: unknown };
          if (p && typeof p.value === "object" && p.value !== null) {
            const md = p.value as { value?: unknown };
            if (typeof md.value === "string") { return md.value; }
          }
          if (typeof p.value === "string") { return p.value; }
          return "";
        })
        .join("");
      if (text.trim().length > 0) {
        messages.push(vscode.LanguageModelChatMessage.Assistant(text));
      }
    }
  }
  return messages;
}

// ── Vscode runtime: tool registration + runner ───────────────────────────────

interface ActiveOrchestrator {
  client: McpClient;
  parentSessionId: string;
  tracker: SpawnTracker;
}

let _active: ActiveOrchestrator | null = null;

/**
 * Set the module-level "current orchestrator turn" pointer. The registered
 * vscode.lm tools dispatch through this. Only one orchestrator turn runs
 * at a time per workspace.
 */
export function _setActiveOrchestrator(active: ActiveOrchestrator | null): void {
  _active = active;
}

/**
 * Register the three orchestrator tools with vscode.lm so the LLM can call
 * them as real tool calls. Must be called once at extension activation;
 * the returned disposable should be added to the extension subscriptions.
 *
 * NOTE: vscode.lm.registerTool also requires the tool name to appear in
 * package.json `contributes.languageModelTools`. If registration fails
 * (older vscode, missing manifest entry) we log and continue — the
 * orchestrator runner still works for inert / no-tool-call turns.
 */
export function registerOrchestratorTools(
  log: (msg: string) => void,
): vscode.Disposable {
  const subs: vscode.Disposable[] = [];

  for (const def of ORCHESTRATOR_TOOLS) {
    try {
      subs.push(vscode.lm.registerTool(def.name, {
        async invoke(options, token) {
          const active = _active;
          if (!active) {
            return new vscode.LanguageModelToolResult([
              new vscode.LanguageModelTextPart(
                `[harness] ${def.name} called outside an orchestrator turn`,
              ),
            ]);
          }
          if (token.isCancellationRequested) {
            return new vscode.LanguageModelToolResult([
              new vscode.LanguageModelTextPart(`[harness] ${def.name} cancelled`),
            ]);
          }
          const args = (options.input as Record<string, unknown>) ?? {};
          const ctx: ToolDispatchContext = {
            parentSessionId: active.parentSessionId,
            parentAgentName: ORCHESTRATOR_AGENT_NAME,
          };
          let raw: string;
          try {
            raw = await dispatchOrchestratorTool(active.client, ctx, def.name, args);
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            log(`[orchestrator] tool ${def.name} dispatch error: ${msg}`);
            return new vscode.LanguageModelToolResult([
              new vscode.LanguageModelTextPart(
                JSON.stringify({ status: "error", error: msg }),
              ),
            ]);
          }
          if (def.name === "harness_spawn_subagent") {
            active.tracker.recordSpawn(def.name, raw);
          } else if (def.name === "harness_await_subagent") {
            active.tracker.recordAwait(raw);
          }
          return new vscode.LanguageModelToolResult([
            new vscode.LanguageModelTextPart(raw),
          ]);
        },
      }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log(`[orchestrator] could not register tool ${def.name}: ${msg}`);
    }
  }

  return vscode.Disposable.from(...subs);
}

export interface RunOrchestratorOptions {
  prompt: string;
  client: McpClient;
  chatContext: vscode.ChatContext;
  stream: vscode.ChatResponseStream;
  token: vscode.CancellationToken;
  roots: string[];
  log: (msg: string) => void;
}

/**
 * Run a single orchestrator turn. Creates a parent session for the turn,
 * builds the system prompt + replayed history, then loops sendRequest →
 * tool calls → results until the model produces a turn with no tool calls
 * (or MAX_TOOL_CYCLES is exhausted).
 *
 * Sub-session cleanup runs in `finally` so abandoned spawns are recorded
 * even if the LLM call throws or the user cancels.
 */
export async function runOrchestrator(opts: RunOrchestratorOptions): Promise<void> {
  const { prompt, client, chatContext, stream, token, roots, log } = opts;

  // Honors the orchestrator agent's `model:` frontmatter, and lets the
  // pushed `orchestrator-routing` skill (or any future complicated skill)
  // override via `model:` in its own SKILL.md — see modelSelector.ts.
  let model: vscode.LanguageModelChat;
  try {
    model = await selectModelForAgent({
      roots,
      agentName: ORCHESTRATOR_AGENT_NAME,
      skills: ["orchestrator-routing"],
      log,
    });
  } catch {
    stream.markdown("**Error:** No Copilot language model available.");
    return;
  }

  const { agentMd, routingSkill } = loadOrchestratorPrompts(roots);
  const memory = await fetchMemoryContext(client);
  const systemPrompt = buildOrchestratorSystemPrompt({
    agentMd,
    routingSkill,
    memoryTier1: memory.tier1_index,
    tier2Available: memory.tier2_available,
  });

  const parentSessionId = await createOrchestratorSession(client, prompt);
  const tracker = new SpawnTracker();
  _setActiveOrchestrator({ client, parentSessionId, tracker });
  log(`[orchestrator] turn started — parent_session_id=${parentSessionId}`);

  try {
    const history: vscode.LanguageModelChatMessage[] = [
      vscode.LanguageModelChatMessage.User(systemPrompt),
      ...extractChatHistory(chatContext, MAX_HISTORY_TURNS),
      vscode.LanguageModelChatMessage.User(prompt),
    ];

    const lmTools: vscode.LanguageModelChatTool[] = ORCHESTRATOR_TOOLS.map(t => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
    }));

    for (let cycle = 0; cycle < MAX_TOOL_CYCLES; cycle++) {
      if (token.isCancellationRequested) { return; }

      const response = await model.sendRequest(history, { tools: lmTools }, token);

      let textBuf = "";
      const toolCalls: vscode.LanguageModelToolCallPart[] = [];

      for await (const part of response.stream) {
        if (part instanceof vscode.LanguageModelTextPart) {
          textBuf += part.value;
          stream.markdown(part.value);
        } else if (part instanceof vscode.LanguageModelToolCallPart) {
          toolCalls.push(part);
        }
      }

      if (toolCalls.length === 0) { return; }

      // Reflect the assistant's tool-call turn into the history, then run
      // each tool and append the result as a user-role tool-result message.
      history.push(vscode.LanguageModelChatMessage.Assistant([
        ...(textBuf.length > 0 ? [new vscode.LanguageModelTextPart(textBuf)] : []),
        ...toolCalls,
      ]));

      const resultParts: vscode.LanguageModelToolResultPart[] = [];
      for (const call of toolCalls) {
        try {
          const invokeResult = await vscode.lm.invokeTool(
            call.name,
            { input: call.input, toolInvocationToken: undefined },
            token,
          );
          resultParts.push(new vscode.LanguageModelToolResultPart(
            call.callId, invokeResult.content,
          ));
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          log(`[orchestrator] invokeTool(${call.name}) failed: ${msg}`);
          resultParts.push(new vscode.LanguageModelToolResultPart(call.callId, [
            new vscode.LanguageModelTextPart(
              JSON.stringify({ status: "error", error: msg }),
            ),
          ]));
        }
      }
      history.push(vscode.LanguageModelChatMessage.User(resultParts));
    }

    log(`[orchestrator] hit MAX_TOOL_CYCLES (${MAX_TOOL_CYCLES}) — forcing final answer`);
    stream.markdown(
      "\n\n_[harness] Tool-call budget exhausted — orchestrator forced to wrap up._",
    );
  } finally {
    await cleanupOutstandingSubagents(client, tracker);
    _setActiveOrchestrator(null);
    log(`[orchestrator] turn ended — parent_session_id=${parentSessionId}`);
  }
}
