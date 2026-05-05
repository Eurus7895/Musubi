/**
 * runners/summarizerRunner.ts — first extension-side sub-agent runner
 * (Phase C.2). Drives the 90% reactive-compaction branch of the
 * orchestrator: spawn → fetch firewalled context → single LM round-trip
 * → complete. The harness verifies the summary (token cap, secrets,
 * injection) on completion; failures fall through to hard truncation
 * upstream.
 *
 * Phase A built `harness_spawn_subagent` / `harness_complete_subagent` /
 * `harness_await_subagent` as pure storage operations. Until C.2 there
 * was no extension-side LM session that turned a `running` row into a
 * terminal one. This file is that session, scoped tightly to the
 * summarizer role. Phase D promotes the shape into a generic
 * `runSubagent(role, brief)` for planner / coder / reviewer.
 *
 * No tools are registered for this run — the brief carries the older
 * conversation window already serialized; the summarizer only emits
 * markdown text.
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { McpClient } from "../mcpClient";
import { selectModelForAgent } from "../modelSelector";
import { type OrchestratorMessage } from "./orchestratorCore";
import {
  buildSummarizerSystemPrompt,
  serializeSummarizerBrief,
} from "./summarizerCore";

// Re-export pure helpers so existing imports keep working.
export { buildSummarizerSystemPrompt, serializeSummarizerBrief };

/** Wall-clock seconds the summarizer is allowed before the harness escalates. */
const SUMMARIZER_WALL_CLOCK_S = 60;
const SUMMARIZER_AGENT_NAME = "summarizer";
const PARENT_AGENT_NAME = "orchestrator";

function loadSummarizerAgentMd(roots: ReadonlyArray<string>): string {
  for (const root of roots) {
    const p = path.join(root, ".github", "agents", "summarizer.agent.md");
    try { return fs.readFileSync(p, "utf-8"); } catch { /* keep looking */ }
  }
  return "";
}

export interface RunSummarizerOptions {
  client: McpClient;
  parentSessionId: string;
  oldHalf: ReadonlyArray<OrchestratorMessage>;
  roots: string[];
  log: (msg: string) => void;
  token: vscode.CancellationToken;
}

export interface SummarizerResult {
  ok: boolean;
  summary: string | null;
  /** Why ok=false, when applicable. Caller can fall through on failure. */
  reason?: string;
}

/**
 * Run a one-shot summarizer sub-agent over `oldHalf` and return the
 * harness-verified summary text. Returns `{ok:false, reason}` on any
 * failure path; the caller is expected to fall through to hard-truncate
 * compaction in that case.
 *
 * Lifecycle:
 *   1. spawn (max_turns=1, wall_clock=60s)
 *   2. fetch firewalled context (brief + role_skill)
 *   3. select model honoring summarizer.agent.md::model
 *   4. send a single LM request with no tools registered
 *   5. complete with the captured text — harness verifies, may truncate
 *   6. return verified summary (or null on failure)
 */
export async function runSummarizerSubagent(
  opts: RunSummarizerOptions,
): Promise<SummarizerResult> {
  const { client, parentSessionId, oldHalf, roots, log, token } = opts;

  const brief = serializeSummarizerBrief(oldHalf);
  if (!brief) {
    return { ok: false, summary: null, reason: "empty brief" };
  }

  // 1. spawn
  let handleId: string;
  try {
    const spawnRaw = await client.callTool("harness_spawn_subagent", {
      parent_session_id: parentSessionId,
      parent_agent_name: PARENT_AGENT_NAME,
      role: SUMMARIZER_AGENT_NAME,
      brief,
      max_turns: 1,
      wall_clock_timeout_s: SUMMARIZER_WALL_CLOCK_S,
    });
    const spawn = JSON.parse(spawnRaw) as { status?: string; handle_id?: string; error?: string };
    if (spawn.status !== "spawned" || !spawn.handle_id) {
      log(`[summarizer] spawn failed: ${spawn.error ?? spawnRaw}`);
      return { ok: false, summary: null, reason: spawn.error ?? "spawn failed" };
    }
    handleId = spawn.handle_id;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[summarizer] spawn threw: ${msg}`);
    return { ok: false, summary: null, reason: msg };
  }

  // 2. firewalled context — pulls the role's SKILL.md via the harness
  let roleSkill: string | null = null;
  try {
    const ctxRaw = await client.callTool("harness_get_subagent_context", {
      handle_id: handleId,
    });
    const ctx = JSON.parse(ctxRaw) as { status?: string; role_skill?: string | null };
    if (ctx.status === "ok" && typeof ctx.role_skill === "string") {
      roleSkill = ctx.role_skill;
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[summarizer] get_subagent_context threw: ${msg}`);
    // Non-fatal — proceed without skill body if needed.
  }

  // 3. model selection + 4. LM call
  const agentMd = loadSummarizerAgentMd(roots);
  const systemPrompt = buildSummarizerSystemPrompt(agentMd, roleSkill);

  let model: vscode.LanguageModelChat;
  try {
    model = await selectModelForAgent({
      roots, agentName: SUMMARIZER_AGENT_NAME, log,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    await abandonHandle(client, handleId, "no model available", log);
    return { ok: false, summary: null, reason: msg };
  }

  let summaryText = "";
  try {
    const messages: vscode.LanguageModelChatMessage[] = [
      vscode.LanguageModelChatMessage.User(systemPrompt),
      vscode.LanguageModelChatMessage.User(brief),
    ];
    const response = await model.sendRequest(messages, {}, token);
    for await (const part of response.stream) {
      if (part instanceof vscode.LanguageModelTextPart) { summaryText += part.value; }
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[summarizer] LM call failed: ${msg}`);
    await abandonHandle(client, handleId, "LM call failed", log);
    return { ok: false, summary: null, reason: msg };
  }

  if (!summaryText.trim()) {
    await abandonHandle(client, handleId, "empty summary", log);
    return { ok: false, summary: null, reason: "empty summary" };
  }

  // 5. complete — harness verifies (truncate cap, secrets, injection)
  try {
    const completeRaw = await client.callTool("harness_complete_subagent", {
      handle_id: handleId,
      summary: summaryText,
      status: "done",
      turns: 1,
    });
    const complete = JSON.parse(completeRaw) as {
      status?: string;
      final_status?: string;
      summary?: string;
      verification_errors?: string[];
    };
    if (complete.status !== "recorded" || complete.final_status !== "done") {
      log(`[summarizer] not recorded as done: ${completeRaw}`);
      return {
        ok: false, summary: null,
        reason: complete.verification_errors?.join("; ") ?? "completion not recorded",
      };
    }
    return {
      ok: true,
      summary: typeof complete.summary === "string" ? complete.summary : summaryText,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[summarizer] complete threw: ${msg}`);
    return { ok: false, summary: null, reason: msg };
  }
}

async function abandonHandle(
  client: McpClient,
  handleId: string,
  reason: string,
  log: (msg: string) => void,
): Promise<void> {
  try {
    await client.callTool("harness_complete_subagent", {
      handle_id: handleId,
      summary: `[harness] summarizer abandoned: ${reason}`,
      status: "abandoned",
      turns: 0,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`[summarizer] abandon failed: ${msg}`);
  }
}
