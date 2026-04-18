/**
 * pipeline.ts — Automated 5-agent orchestration via VS Code Language Model API.
 *
 * For each agent the extension:
 *   1. Calls harness_read_stage via vscode.lm.invokeTool() — enforces firewall, injects skills
 *   2. Sends context + agent system prompt to Copilot via vscode.lm.sendRequest()
 *   3. Calls harness_write_stage via vscode.lm.invokeTool() — validates + stores output
 *
 * All harness_* tools are invoked on the single MCP server VS Code manages
 * via .vscode/mcp.json. No second server process is spawned.
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

// ── Types ─────────────────────────────────────────────────────────────────────

interface HarnessReadResult {
  data: unknown;
  injected_skills?: Record<string, string>;
}

interface HarnessWriteResult {
  status: "stored" | "error";
  error?: string;
  validation_errors?: string[];
}

interface ReviewOutput {
  status: "pass" | "fail" | "escalate";
  attempt: number;
  issues?: Array<{ severity: string; description: string; fix_instruction: string }>;
  escalate_reason?: string | null;
}

interface SessionStatus {
  session_id: string;
  stages: Record<string, { status: string; attempt: number }>;
}

interface ActiveSession {
  session_id: string | null;
  request?: string;
  resume_stage?: string;
  attempt?: number;
}

export interface PipelineResult {
  success: boolean;
  sessionId: string;
  stages: Record<string, unknown>;
  escalated: boolean;
  escalation?: string;
}

// ── Agent pipeline definition ─────────────────────────────────────────────────

const AGENT_PIPELINE = [
  { name: "planner"  as const, readStages: ["plan"]                    as const, writeStage: "plan"   },
  { name: "designer" as const, readStages: ["plan"]                    as const, writeStage: "design" },
  { name: "coder"    as const, readStages: ["design", "plan"]          as const, writeStage: "code"   },
  { name: "reviewer" as const, readStages: ["code", "plan", "design"]  as const, writeStage: "review" },
] as const;

const MAX_CODE_ATTEMPTS = 3;

// ── Harness tool invocation ───────────────────────────────────────────────────

/**
 * Call a harness_* MCP tool via VS Code's built-in MCP client.
 * VS Code manages the single server instance from .vscode/mcp.json.
 */
async function callHarness(
  toolName: string,
  args: Record<string, unknown>,
  token: vscode.CancellationToken,
  toolToken?: vscode.ChatParticipantToolToken,
): Promise<unknown> {
  const result = await vscode.lm.invokeTool(
    toolName,
    { input: args, toolInvocationToken: toolToken },
    token,
  );
  const text = result.content
    .map(p => (p instanceof vscode.LanguageModelTextPart ? p.value : ""))
    .join("");
  try {
    return JSON.parse(text);
  } catch {
    return text; // skill/reference content is plain text
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function loadAgentPrompt(workspaceRoot: string, agentName: string): string {
  const filePath = path.join(workspaceRoot, ".github", "agents", `${agentName}.agent.md`);
  try {
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return (
      `You are the ${agentName} agent in the CopilotHarness pipeline. ` +
      `Analyse the provided input context and produce valid JSON output matching your output schema.`
    );
  }
}

function extractJson(text: string): unknown {
  try { return JSON.parse(text.trim()); } catch { /* fall through */ }
  const blockMatch = text.match(/```(?:json)?\s*\n([\s\S]*?)\n```/);
  if (blockMatch) { try { return JSON.parse(blockMatch[1].trim()); } catch { /* fall through */ } }
  const objMatch = text.match(/\{[\s\S]*\}/);
  if (objMatch) { try { return JSON.parse(objMatch[0]); } catch { /* fall through */ } }
  throw new Error(`Cannot extract JSON from model response:\n${text.substring(0, 500)}`);
}

async function readAgentContext(
  sessionId: string,
  agentName: string,
  readStages: readonly string[],
  token: vscode.CancellationToken,
  toolToken?: vscode.ChatParticipantToolToken,
): Promise<Record<string, unknown>> {
  const merged: Record<string, unknown> = {};
  for (const stage of readStages) {
    const result = (await callHarness(
      "harness_read_stage",
      { session_id: sessionId, stage, agent_name: agentName },
      token,
      toolToken,
    )) as HarnessReadResult;
    if (result.data !== null && result.data !== undefined) {
      merged[stage] = result.data;
    }
    if (result.injected_skills) {
      merged["injected_skills"] = result.injected_skills;
    }
  }
  return merged;
}

async function runAgentLM(
  model: vscode.LanguageModelChat,
  agentPrompt: string,
  context: Record<string, unknown>,
  token: vscode.CancellationToken,
): Promise<unknown> {
  const messages = [
    vscode.LanguageModelChatMessage.User(
      agentPrompt +
      "\n\n---\n\n" +
      "IMPORTANT — you are being driven by the CopilotHarness VS Code extension.\n" +
      "The extension has already called harness_read_stage to retrieve your input context below.\n" +
      "The extension will call harness_write_stage with your output automatically.\n" +
      "Your ONLY task: analyse the context and respond with VALID JSON matching your output schema.\n" +
      "Do NOT call any tools. Do NOT include markdown or explanation outside the JSON.",
    ),
    vscode.LanguageModelChatMessage.User(
      `Input context from the harness:\n\n${JSON.stringify(context, null, 2)}`,
    ),
  ];
  const response = await model.sendRequest(messages, {}, token);
  let text = "";
  for await (const chunk of response.text) { text += chunk; }
  return extractJson(text);
}

async function writeStage(
  sessionId: string,
  stage: string,
  agentName: string,
  output: unknown,
  token: vscode.CancellationToken,
  toolToken?: vscode.ChatParticipantToolToken,
): Promise<void> {
  const result = (await callHarness(
    "harness_write_stage",
    { session_id: sessionId, stage, output: JSON.stringify(output), agent_name: agentName },
    token,
    toolToken,
  )) as HarnessWriteResult;

  if (result.status !== "stored") {
    const details = result.validation_errors?.join("\n") ?? "";
    throw new Error(
      `harness_write_stage failed for '${stage}': ${result.error ?? "unknown"}\n${details}`.trim(),
    );
  }
}

// ── Correction loop ───────────────────────────────────────────────────────────

async function runCorrectionLoop(
  model: vscode.LanguageModelChat,
  sessionId: string,
  workspaceRoot: string,
  initialReview: ReviewOutput,
  codeAttempt: number,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  toolToken?: vscode.ChatParticipantToolToken,
): Promise<ReviewOutput> {
  let currentReview = initialReview;

  while (currentReview.status === "fail" && codeAttempt < MAX_CODE_ATTEMPTS) {
    if (token.isCancellationRequested) break;

    codeAttempt++;
    stream.progress(`Review failed — retrying coder (attempt ${codeAttempt} of ${MAX_CODE_ATTEMPTS})`);

    await callHarness("harness_increment_attempt", { session_id: sessionId, stage: "code" }, token, toolToken);
    await callHarness("harness_increment_attempt", { session_id: sessionId, stage: "review" }, token, toolToken);

    const coderCtx = await readAgentContext(sessionId, "coder", ["design", "plan", "review"], token, toolToken);
    const fixedCode = await runAgentLM(model, loadAgentPrompt(workspaceRoot, "coder"), coderCtx, token);
    await writeStage(sessionId, "code", "coder", fixedCode, token, toolToken);

    stream.progress(`Re-running reviewer (attempt ${codeAttempt})`);
    const reviewerCtx = await readAgentContext(sessionId, "reviewer", ["code", "plan", "design"], token, toolToken);
    const newReview = (await runAgentLM(
      model, loadAgentPrompt(workspaceRoot, "reviewer"), reviewerCtx, token,
    )) as ReviewOutput;
    await writeStage(sessionId, "review", "reviewer", newReview, token, toolToken);

    currentReview = newReview;
    if (newReview.status === "pass" || newReview.status === "escalate") break;
  }

  return currentReview;
}

// ── Main entry point ──────────────────────────────────────────────────────────

export async function runPipeline(
  request: string,
  workspaceRoot: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  toolToken?: vscode.ChatParticipantToolToken,
): Promise<PipelineResult> {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot", family: "gpt-4o" });
  if (!models.length) {
    throw new Error("No Copilot language model found. Ensure GitHub Copilot Chat is installed and signed in.");
  }
  const model = models[0];

  // ── Session setup (with crash recovery) ──────────────────────────────────────

  const active = (await callHarness("harness_get_active_session", {}, token, toolToken)) as ActiveSession;
  let sessionId: string;

  if (active.session_id) {
    sessionId = active.session_id;
    stream.progress(`Resuming session ${sessionId} (interrupted at '${active.resume_stage}')`);
  } else {
    const session = (await callHarness("harness_new_session", { request }, token, toolToken)) as { session_id: string };
    sessionId = session.session_id;
    stream.progress(`Session ${sessionId} created`);
  }

  const stageOutputs: Record<string, unknown> = {};

  // ── Run planner → designer → coder → reviewer ────────────────────────────────

  for (const agent of AGENT_PIPELINE) {
    if (token.isCancellationRequested) break;

    const statusData = (await callHarness(
      "harness_get_status", { session_id: sessionId }, token, toolToken,
    )) as SessionStatus;

    if (statusData.stages[agent.writeStage]?.status === "complete") {
      stream.progress(`Stage '${agent.writeStage}' already complete — skipping`);
      continue;
    }

    stream.progress(`Running ${agent.name}...`);

    const context = await readAgentContext(sessionId, agent.name, agent.readStages, token, toolToken);
    if (agent.name === "planner") {
      context["request"] = request;
    }

    const agentOutput = await runAgentLM(model, loadAgentPrompt(workspaceRoot, agent.name), context, token);
    await writeStage(sessionId, agent.writeStage, agent.name, agentOutput, token, toolToken);
    stageOutputs[agent.writeStage] = agentOutput;

    stream.markdown(`✓ **${agent.name}** complete`);

    // ── Correction loop (after reviewer) ─────────────────────────────────────
    if (agent.name === "reviewer") {
      const review = agentOutput as ReviewOutput;

      if (review.status === "pass") continue;

      if (review.status === "escalate") {
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: review.escalate_reason ?? "Reviewer escalated.",
        };
      }

      const currentAttempt = statusData.stages["code"]?.attempt ?? 1;
      const finalReview = await runCorrectionLoop(
        model, sessionId, workspaceRoot, review, currentAttempt, stream, token, toolToken,
      );
      stageOutputs["review"] = finalReview;

      if (finalReview.status !== "pass") {
        const reason = finalReview.status === "escalate"
          ? (finalReview.escalate_reason ?? "Reviewer escalated.")
          : `Max correction attempts (${MAX_CODE_ATTEMPTS}) reached without passing review.`;
        return { success: false, sessionId, stages: stageOutputs, escalated: true, escalation: reason };
      }
    }
  }

  return { success: true, sessionId, stages: stageOutputs, escalated: false };
}
