/**
 * pipeline.ts — Automated 5-agent orchestration via VS Code Language Model API.
 *
 * For each agent the extension:
 *   1. Calls harness_* tools directly via McpClient — no vscode.lm.invokeTool()
 *   2. Sends context + agent system prompt to Copilot via vscode.lm.sendRequest()
 *   3. Calls harness_write_stage to validate + store the agent's output
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { McpClient } from "./mcpClient";

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

export interface StepResult {
  success: boolean;
  sessionId: string;
  completedAgent: string;
  completedStage: string;
  output: unknown;
  nextAgent: string | null;  // null when pipeline is complete or escalated
  pipelineComplete: boolean;
  escalated: boolean;
  escalation?: string;
}

// ── Agent pipeline definition ─────────────────────────────────────────────────

const AGENT_PIPELINE = [
  { name: "planner"  as const, readStages: ["plan"]                   as const, writeStage: "plan"   },
  { name: "designer" as const, readStages: ["plan"]                   as const, writeStage: "design" },
  { name: "coder"    as const, readStages: ["design", "plan"]         as const, writeStage: "code"   },
  { name: "reviewer" as const, readStages: ["code", "plan", "design"] as const, writeStage: "review" },
] as const;

const MAX_CODE_ATTEMPTS = 3;

// ── Harness tool invocation ───────────────────────────────────────────────────

async function callHarness(
  client: McpClient,
  toolName: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const text = await client.callTool(toolName, args);
  try {
    return JSON.parse(text);
  } catch {
    // Server returned non-JSON — likely an unhandled exception from FastMCP.
    throw new Error(`${toolName} returned non-JSON response: ${text.slice(0, 300)}`);
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
  client: McpClient,
  sessionId: string,
  agentName: string,
  readStages: readonly string[],
): Promise<Record<string, unknown>> {
  const merged: Record<string, unknown> = {};
  for (const stage of readStages) {
    const result = (await callHarness(client, "harness_read_stage", {
      session_id: sessionId, stage, agent_name: agentName,
    })) as HarnessReadResult;
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
      "The extension has already retrieved your input context shown below.\n" +
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
  client: McpClient,
  sessionId: string,
  stage: string,
  agentName: string,
  output: unknown,
): Promise<void> {
  const result = (await callHarness(client, "harness_write_stage", {
    session_id: sessionId, stage, output: JSON.stringify(output), agent_name: agentName,
  })) as HarnessWriteResult;

  if (result.status !== "stored") {
    const details = result.validation_errors?.join("\n") ?? "";
    throw new Error(
      `harness_write_stage failed for '${stage}': ${result.error ?? "unknown"}\n${details}`.trim(),
    );
  }
}

function materializeCoderFiles(
  workspaceRoot: string,
  output: unknown,
  stream: vscode.ChatResponseStream,
): void {
  if (typeof output !== "object" || output === null) { return; }
  const fileContents = (output as Record<string, unknown>)["file_contents"];
  if (typeof fileContents !== "object" || fileContents === null) { return; }
  for (const [relPath, content] of Object.entries(fileContents as Record<string, unknown>)) {
    if (typeof content !== "string") { continue; }
    const absPath = path.join(workspaceRoot, relPath);
    fs.mkdirSync(path.dirname(absPath), { recursive: true });
    fs.writeFileSync(absPath, content, "utf-8");
    stream.markdown(`  - Created \`${relPath}\``);
  }
}

// ── Correction loop ───────────────────────────────────────────────────────────

async function runCorrectionLoop(
  client: McpClient,
  model: vscode.LanguageModelChat,
  sessionId: string,
  workspaceRoot: string,
  initialReview: ReviewOutput,
  codeAttempt: number,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<ReviewOutput> {
  let currentReview = initialReview;

  while (currentReview.status === "fail" && codeAttempt < MAX_CODE_ATTEMPTS) {
    if (token.isCancellationRequested) { break; }

    codeAttempt++;
    stream.progress(`Review failed — retrying coder (attempt ${codeAttempt} of ${MAX_CODE_ATTEMPTS})`);

    await callHarness(client, "harness_increment_attempt", { session_id: sessionId, stage: "code" });
    await callHarness(client, "harness_increment_attempt", { session_id: sessionId, stage: "review" });

    const coderCtx = await readAgentContext(client, sessionId, "coder", ["design", "plan", "review"]);
    const fixedCode = await runAgentLM(model, loadAgentPrompt(workspaceRoot, "coder"), coderCtx, token);
    await writeStage(client, sessionId, "code", "coder", fixedCode);
    materializeCoderFiles(workspaceRoot, fixedCode, stream);

    stream.progress(`Re-running reviewer (attempt ${codeAttempt})`);
    const reviewerCtx = await readAgentContext(client, sessionId, "reviewer", ["code", "plan", "design"]);
    const newReview = (await runAgentLM(
      model, loadAgentPrompt(workspaceRoot, "reviewer"), reviewerCtx, token,
    )) as ReviewOutput;
    await writeStage(client, sessionId, "review", "reviewer", newReview);

    currentReview = newReview;
    if (newReview.status === "pass" || newReview.status === "escalate") { break; }
  }

  return currentReview;
}

// ── Main entry point ──────────────────────────────────────────────────────────

export async function runPipeline(
  client: McpClient,
  request: string,
  workspaceRoot: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<PipelineResult> {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot", family: "gpt-4o" });
  if (!models.length) {
    throw new Error("No Copilot language model found. Ensure GitHub Copilot Chat is installed and signed in.");
  }
  const model = models[0];

  // ── Session setup (with crash recovery) ──────────────────────────────────────

  const active = (await callHarness(client, "harness_get_active_session", {})) as ActiveSession;
  let sessionId: string;

  if (active.session_id) {
    sessionId = active.session_id;
    stream.progress(`Resuming session ${sessionId} (interrupted at '${active.resume_stage}')`);
  } else {
    const session = (await callHarness(client, "harness_new_session", { request })) as { session_id: string };
    sessionId = session.session_id;
    stream.progress(`Session ${sessionId} created`);
  }

  const stageOutputs: Record<string, unknown> = {};

  // ── Run planner → designer → coder → reviewer ────────────────────────────────

  for (const agent of AGENT_PIPELINE) {
    if (token.isCancellationRequested) { break; }

    const statusData = (await callHarness(
      client, "harness_get_status", { session_id: sessionId },
    )) as SessionStatus;

    if (statusData.stages[agent.writeStage]?.status === "complete") {
      stream.progress(`Stage '${agent.writeStage}' already complete — skipping`);
      continue;
    }

    stream.progress(`Running ${agent.name}...`);

    const context = await readAgentContext(client, sessionId, agent.name, agent.readStages);
    if (agent.name === "planner") {
      context["request"] = request;
    }

    const agentOutput = await runAgentLM(model, loadAgentPrompt(workspaceRoot, agent.name), context, token);
    await writeStage(client, sessionId, agent.writeStage, agent.name, agentOutput);
    stageOutputs[agent.writeStage] = agentOutput;

    if (agent.name === "coder") {
      materializeCoderFiles(workspaceRoot, agentOutput, stream);
    }

    stream.markdown(`✓ **${agent.name}** complete`);

    // ── Correction loop (after reviewer) ─────────────────────────────────────
    if (agent.name === "reviewer") {
      const review = agentOutput as ReviewOutput;

      if (review.status === "pass") { continue; }

      if (review.status === "escalate") {
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: review.escalate_reason ?? "Reviewer escalated.",
        };
      }

      const currentAttempt = statusData.stages["code"]?.attempt ?? 1;
      const finalReview = await runCorrectionLoop(
        client, model, sessionId, workspaceRoot, review, currentAttempt, stream, token,
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

// ── Single-step entry point ───────────────────────────────────────────────────

export async function runStep(
  client: McpClient,
  workspaceRoot: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  options: {
    request?: string;    // provided → create new session; omitted → resume active
    agentName?: string;  // run this specific agent instead of the next pending one
  },
): Promise<StepResult> {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot", family: "gpt-4o" });
  if (!models.length) {
    throw new Error("No Copilot language model found. Ensure GitHub Copilot Chat is installed and signed in.");
  }
  const model = models[0];

  // ── Session setup ─────────────────────────────────────────────────────────────

  const active = (await callHarness(client, "harness_get_active_session", {})) as ActiveSession;
  let sessionId: string;
  let sessionRequest: string;

  if (options.request) {
    const session = (await callHarness(client, "harness_new_session", { request: options.request })) as { session_id: string };
    sessionId = session.session_id;
    sessionRequest = options.request;
    stream.progress(`Session ${sessionId} created`);
  } else if (active.session_id) {
    sessionId = active.session_id;
    sessionRequest = active.request ?? "";
    stream.progress(`Resuming session ${sessionId}`);
  } else {
    throw new Error("No active session. Start a new task with `@harness <your task description>`");
  }

  // ── Resolve which agent to run ────────────────────────────────────────────────

  const statusData = (await callHarness(
    client, "harness_get_status", { session_id: sessionId },
  )) as SessionStatus;

  let agentDef: typeof AGENT_PIPELINE[number] | undefined;

  if (options.agentName) {
    agentDef = AGENT_PIPELINE.find(a => a.name === options.agentName);
    if (!agentDef) {
      throw new Error(`Unknown agent: '${options.agentName}'. Valid: planner, designer, coder, reviewer`);
    }
    if (statusData.stages[agentDef.writeStage]?.status === "complete") {
      throw new Error(
        `Stage '${agentDef.writeStage}' is already complete. ` +
        `Use \`@harness full <task>\` to start a new pipeline, or \`@harness status\` to review progress.`,
      );
    }
  } else {
    for (const agent of AGENT_PIPELINE) {
      if (statusData.stages[agent.writeStage]?.status !== "complete") {
        agentDef = agent;
        break;
      }
    }
  }

  if (!agentDef) {
    return {
      success: true, sessionId, completedAgent: "", completedStage: "",
      output: null, nextAgent: null, pipelineComplete: true, escalated: false,
    };
  }

  // ── Run the agent ─────────────────────────────────────────────────────────────

  stream.progress(`Running ${agentDef.name}...`);
  const context = await readAgentContext(client, sessionId, agentDef.name, agentDef.readStages);
  if (agentDef.name === "planner") {
    context["request"] = sessionRequest;
  }

  const agentOutput = await runAgentLM(
    model, loadAgentPrompt(workspaceRoot, agentDef.name), context, token,
  );
  await writeStage(client, sessionId, agentDef.writeStage, agentDef.name, agentOutput);

  if (agentDef.name === "coder") {
    materializeCoderFiles(workspaceRoot, agentOutput, stream);
  }

  // ── Reviewer: run inline correction loop ─────────────────────────────────────

  let finalOutput: unknown = agentOutput;
  let escalated = false;
  let escalation: string | undefined;

  if (agentDef.name === "reviewer") {
    const review = agentOutput as ReviewOutput;
    if (review.status === "escalate") {
      escalated = true;
      escalation = review.escalate_reason ?? "Reviewer escalated.";
    } else if (review.status === "fail") {
      const currentAttempt = statusData.stages["code"]?.attempt ?? 1;
      const finalReview = await runCorrectionLoop(
        client, model, sessionId, workspaceRoot, review, currentAttempt, stream, token,
      );
      finalOutput = finalReview;
      if (finalReview.status !== "pass") {
        escalated = true;
        escalation = finalReview.status === "escalate"
          ? (finalReview.escalate_reason ?? "Reviewer escalated.")
          : `Max correction attempts (${MAX_CODE_ATTEMPTS}) reached without passing review.`;
      }
    }
  }

  // ── Determine what comes next ─────────────────────────────────────────────────

  let nextAgent: string | null = null;
  let pipelineComplete = false;

  if (!escalated) {
    const updated = (await callHarness(
      client, "harness_get_status", { session_id: sessionId },
    )) as SessionStatus;
    for (const agent of AGENT_PIPELINE) {
      if (updated.stages[agent.writeStage]?.status !== "complete") {
        nextAgent = agent.name;
        break;
      }
    }
    if (!nextAgent) { pipelineComplete = true; }
  }

  return {
    success: true, sessionId,
    completedAgent: agentDef.name, completedStage: agentDef.writeStage,
    output: finalOutput, nextAgent, pipelineComplete, escalated, escalation,
  };
}
