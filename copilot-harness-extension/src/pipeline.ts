/**
 * pipeline.ts — Automated 5-agent orchestration via VS Code Language Model API.
 *
 * Replaces the human relay in Phase 1:
 *   Phase 1 (manual):   developer opens each @agent in Copilot Chat
 *   Phase 2 (this):     extension calls vscode.lm.sendRequest() per agent
 *
 * For each agent the extension:
 *   1. Calls harness_read_stage (enforces context firewall, injects skills)
 *   2. Sends context + agent system prompt to the language model
 *   3. Parses the structured JSON response
 *   4. Calls harness_write_stage (injection scan + schema validation + store)
 *
 * After the reviewer writes a "fail" review, the correction loop retries the
 * coder (with fix_instructions only — firewall enforced) up to MAX_CODE_ATTEMPTS.
 *
 * Nothing in state.py / verifier.py / context_builder.py / server.py changes —
 * the extension is a new consumer of the same MCP tools.
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { HarnessClient } from "./client";

// ── Types ─────────────────────────────────────────────────────────────────────

interface HarnessReadResult {
  data: unknown;
  injected_skills?: Record<string, string>;
  note?: string;
}

interface HarnessWriteResult {
  status: "stored" | "error";
  error?: string;
  validation_errors?: string[];
}

interface ReviewOutput {
  status: "pass" | "fail" | "escalate";
  attempt: number;
  issues?: Array<{
    severity: string;
    description: string;
    fix_instruction: string;
  }>;
  escalate_reason?: string | null;
}

interface SessionStatus {
  session_id: string;
  request: string;
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

// ── Agent configuration ───────────────────────────────────────────────────────

/**
 * readStages: stages this agent reads from the harness.
 *   The FIRST entry is the "primary" stage — it triggers skill auto-injection
 *   via STAGE_SKILL_MAP and marks the agent's output stage in_progress.
 *
 * writeStage: the stage this agent writes output to.
 */
const AGENT_PIPELINE = [
  {
    name: "planner" as const,
    readStages: ["plan"] as const,
    writeStage: "plan",
  },
  {
    name: "designer" as const,
    readStages: ["plan"] as const,
    writeStage: "design",
  },
  {
    name: "coder" as const,
    readStages: ["design", "plan"] as const,
    writeStage: "code",
  },
  {
    name: "reviewer" as const,
    readStages: ["code", "plan", "design"] as const,
    writeStage: "review",
  },
] as const;

const MAX_CODE_ATTEMPTS = 3;

// ── Helpers ───────────────────────────────────────────────────────────────────

function loadAgentPrompt(workspaceRoot: string, agentName: string): string {
  const filePath = path.join(
    workspaceRoot,
    ".github",
    "agents",
    `${agentName}.agent.md`,
  );
  try {
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return (
      `You are the ${agentName} agent in the CopilotHarness pipeline. ` +
      `Analyse the provided input context and produce valid JSON output matching your output schema.`
    );
  }
}

/**
 * Extract JSON from a language model response.
 * Handles direct JSON, markdown ```json ... ``` blocks, and embedded objects.
 */
function extractJson(text: string): unknown {
  // Direct parse (model returned raw JSON).
  try {
    return JSON.parse(text.trim());
  } catch { /* fall through */ }

  // Markdown code block: ```json ... ``` or ``` ... ```
  const blockMatch = text.match(/```(?:json)?\s*\n([\s\S]*?)\n```/);
  if (blockMatch) {
    try {
      return JSON.parse(blockMatch[1].trim());
    } catch { /* fall through */ }
  }

  // Find the first complete JSON object in the text.
  const objMatch = text.match(/\{[\s\S]*\}/);
  if (objMatch) {
    try {
      return JSON.parse(objMatch[0]);
    } catch { /* fall through */ }
  }

  throw new Error(
    `Cannot extract JSON from language model response:\n${text.substring(0, 500)}`,
  );
}

/**
 * Read all permitted stages for an agent and merge into one context dict.
 * The primary stage (first in readStages) is read first so that:
 *   - harness marks the output stage in_progress (crash recovery)
 *   - STAGE_SKILL_MAP skill injection is triggered on the right (stage, agent) pair
 */
async function readAgentContext(
  client: HarnessClient,
  sessionId: string,
  agentName: string,
  readStages: readonly string[],
): Promise<Record<string, unknown>> {
  const merged: Record<string, unknown> = {};
  for (const stage of readStages) {
    const result = (await client.callTool("harness_read_stage", {
      session_id: sessionId,
      stage,
      agent_name: agentName,
    })) as HarnessReadResult;

    if (result.data !== null && result.data !== undefined) {
      merged[stage] = result.data;
    }
    // Merge injected skills (only present on the primary read).
    if (result.injected_skills) {
      merged["injected_skills"] = result.injected_skills;
    }
  }
  return merged;
}

/**
 * Send the agent's assembled context to the language model and return
 * the parsed JSON output.
 */
async function runAgentLM(
  model: vscode.LanguageModelChat,
  agentPrompt: string,
  context: Record<string, unknown>,
  token: vscode.CancellationToken,
): Promise<unknown> {
  const contextText = JSON.stringify(context, null, 2);
  const messages = [
    vscode.LanguageModelChatMessage.User(
      // System-level instructions: agent role + "extension drives the tools" notice.
      agentPrompt +
        "\n\n---\n\n" +
        "IMPORTANT — you are being driven by the CopilotHarness VS Code extension.\n" +
        "The extension has already called harness_read_stage to retrieve your input context below.\n" +
        "The extension will call harness_write_stage with your output automatically.\n" +
        "Your ONLY task: analyse the context and respond with VALID JSON matching your output schema.\n" +
        "Do NOT call any tools. Do NOT include markdown or explanation outside the JSON.",
    ),
    vscode.LanguageModelChatMessage.User(
      `Input context from the harness:\n\n${contextText}`,
    ),
  ];

  const response = await model.sendRequest(messages, {}, token);
  let text = "";
  for await (const chunk of response.text) {
    text += chunk;
  }
  return extractJson(text);
}

async function writeStage(
  client: HarnessClient,
  sessionId: string,
  stage: string,
  agentName: string,
  output: unknown,
): Promise<void> {
  const result = (await client.callTool("harness_write_stage", {
    session_id: sessionId,
    stage,
    output: JSON.stringify(output),
    agent_name: agentName,
  })) as HarnessWriteResult;

  if (result.status !== "stored") {
    const details = result.validation_errors?.join("\n") ?? "";
    throw new Error(
      `harness_write_stage failed for '${stage}': ${result.error ?? "unknown error"}\n${details}`.trim(),
    );
  }
}

// ── Correction loop ───────────────────────────────────────────────────────────

/**
 * Retry the coder→reviewer pair after a failed review.
 * Uses harness_increment_attempt so state.py's write-once invariant is preserved.
 * Returns the final review output (pass / escalate / fail-after-max-attempts).
 */
async function runCorrectionLoop(
  model: vscode.LanguageModelChat,
  client: HarnessClient,
  sessionId: string,
  workspaceRoot: string,
  initialReview: ReviewOutput,
  codeAttempt: number,
  out: vscode.OutputChannel,
  token: vscode.CancellationToken,
): Promise<ReviewOutput> {
  let currentReview = initialReview;

  while (currentReview.status === "fail" && codeAttempt < MAX_CODE_ATTEMPTS) {
    if (token.isCancellationRequested) break;

    codeAttempt++;
    out.appendLine(
      `\n[harness] Review failed — correction loop attempt ${codeAttempt} of ${MAX_CODE_ATTEMPTS}`,
    );

    // Increment attempt counters before any writes (write-once invariant).
    await client.callTool("harness_increment_attempt", {
      session_id: sessionId,
      stage: "code",
    });
    await client.callTool("harness_increment_attempt", {
      session_id: sessionId,
      stage: "review",
    });

    // Coder reads: "design" (primary — python skill injected), "plan", "review"
    // For coder reading "review", the harness returns fix_instructions only (firewall).
    out.appendLine("[harness] Running coder with fix_instructions...");
    const coderCtx = await readAgentContext(client, sessionId, "coder", [
      "design",
      "plan",
      "review",
    ]);
    const fixedCode = await runAgentLM(
      model,
      loadAgentPrompt(workspaceRoot, "coder"),
      coderCtx,
      token,
    );
    await writeStage(client, sessionId, "code", "coder", fixedCode);

    // Reviewer reads: "code" (primary — code-review skill injected), "plan", "design"
    out.appendLine("[harness] Re-running reviewer...");
    const reviewerCtx = await readAgentContext(client, sessionId, "reviewer", [
      "code",
      "plan",
      "design",
    ]);
    const newReview = (await runAgentLM(
      model,
      loadAgentPrompt(workspaceRoot, "reviewer"),
      reviewerCtx,
      token,
    )) as ReviewOutput;
    await writeStage(client, sessionId, "review", "reviewer", newReview);

    currentReview = newReview;

    if (newReview.status === "pass") {
      out.appendLine("[harness] Review passed on retry.");
      return newReview;
    }
    if (newReview.status === "escalate") {
      out.appendLine("[harness] Reviewer escalated on retry.");
      return newReview;
    }
  }

  return currentReview;
}

// ── Main pipeline entry point ─────────────────────────────────────────────────

export async function runPipeline(
  request: string,
  workspaceRoot: string,
  client: HarnessClient,
  out: vscode.OutputChannel,
  token: vscode.CancellationToken,
): Promise<PipelineResult> {
  // Select the Copilot language model.
  const models = await vscode.lm.selectChatModels({
    vendor: "copilot",
    family: "gpt-4o",
  });
  if (!models.length) {
    throw new Error(
      "No Copilot language model found. " +
        "Ensure GitHub Copilot Chat is installed and signed in.",
    );
  }
  const model = models[0];
  out.appendLine(`[harness] Language model: ${model.name}`);

  // ── Session setup (with crash recovery) ──────────────────────────────────────

  const active = (await client.callTool(
    "harness_get_active_session",
  )) as ActiveSession;
  let sessionId: string;

  if (active.session_id) {
    sessionId = active.session_id;
    out.appendLine(
      `[harness] Resuming session ${sessionId} ` +
        `(interrupted at stage '${active.resume_stage}', attempt ${active.attempt})`,
    );
  } else {
    const session = (await client.callTool("harness_new_session", {
      request,
    })) as { session_id: string };
    sessionId = session.session_id;
    out.appendLine(`[harness] New session: ${sessionId}`);
  }

  const stageOutputs: Record<string, unknown> = {};

  // ── Run planner → designer → coder → reviewer ─────────────────────────────

  for (const agent of AGENT_PIPELINE) {
    if (token.isCancellationRequested) break;

    // Skip stages already complete (supports crash recovery / resume).
    const statusData = (await client.callTool("harness_get_status", {
      session_id: sessionId,
    })) as SessionStatus;
    if (statusData.stages[agent.writeStage]?.status === "complete") {
      out.appendLine(
        `[harness] Stage '${agent.writeStage}' already complete — skipping`,
      );
      continue;
    }

    out.appendLine(`\n[harness] ── ${agent.name.toUpperCase()} ──`);

    // For the planner, inject the request directly (no plan output exists yet).
    const context = await readAgentContext(
      client,
      sessionId,
      agent.name,
      agent.readStages,
    );
    if (agent.name === "planner") {
      context["request"] = request;
    }

    out.appendLine("[harness] Sending to language model...");
    const agentOutput = await runAgentLM(
      model,
      loadAgentPrompt(workspaceRoot, agent.name),
      context,
      token,
    );

    out.appendLine(`[harness] Writing stage '${agent.writeStage}'...`);
    await writeStage(client, sessionId, agent.writeStage, agent.name, agentOutput);
    stageOutputs[agent.writeStage] = agentOutput;
    out.appendLine(`[harness] Stage '${agent.writeStage}' stored.`);

    // ── Correction loop (triggered after reviewer) ──────────────────────────
    if (agent.name === "reviewer") {
      const review = agentOutput as ReviewOutput;

      if (review.status === "pass") {
        out.appendLine("[harness] Review passed — pipeline complete.");
        continue;
      }

      if (review.status === "escalate") {
        out.appendLine(
          `[harness] Reviewer escalated: ${review.escalate_reason ?? "no reason given"}`,
        );
        return {
          success: false,
          sessionId,
          stages: stageOutputs,
          escalated: true,
          escalation: review.escalate_reason ?? "Reviewer escalated.",
        };
      }

      // status === "fail" — enter correction loop.
      const currentCodeAttempt =
        statusData.stages["code"]?.attempt ?? 1;
      const finalReview = await runCorrectionLoop(
        model,
        client,
        sessionId,
        workspaceRoot,
        review,
        currentCodeAttempt,
        out,
        token,
      );
      stageOutputs["review"] = finalReview;

      if (finalReview.status !== "pass") {
        const reason =
          finalReview.status === "escalate"
            ? (finalReview.escalate_reason ?? "Reviewer escalated.")
            : `Max correction attempts (${MAX_CODE_ATTEMPTS}) reached without passing review.`;
        out.appendLine(`[harness] Escalating: ${reason}`);
        return {
          success: false,
          sessionId,
          stages: stageOutputs,
          escalated: true,
          escalation: reason,
        };
      }
    }
  }

  return { success: true, sessionId, stages: stageOutputs, escalated: false };
}
