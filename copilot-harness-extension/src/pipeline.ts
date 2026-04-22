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
  memory?: Record<string, unknown>;
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

// ── Per-agent output schema hints (injected into extension-mode prompt) ───────
// Prevents Copilot from producing tool-call JSON or wrapped objects when told
// not to call tools — the Input Contract in agent files describes tool calls
// that the extension has already executed on the agent's behalf.

const AGENT_OUTPUT_HINTS: Record<string, string> = {
  planner: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "summary"        — string: one sentence describing what will be built',
    '  "tasks"          — array of { id, description, files_affected, acceptance_criteria, complexity }',
    '  "required_skills"— array of skill IDs (optional)',
    '  "open_questions" — array of strings (optional)',
    '  "confidence"     — "high" | "medium" | "low"',
  ].join("\n"),
  designer: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "summary"          — string',
    '  "tasks_addressed"  — array of task IDs from the plan (e.g. ["T1","T2"])',
    '  "modules"          — array of { file, purpose, public_interface }',
    '  "data_schemas"     — array of { name, fields } (optional)',
    '  "dependencies"     — array of strings (optional)',
    '  "integration_notes"— string (optional)',
    '  "confidence"       — "high" | "medium" | "low"',
  ].join("\n"),
  coder: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "summary"              — string: one sentence describing what was implemented',
    '  "files_modified"       — array of file paths (every file you write)',
    '  "file_contents"        — REQUIRED object mapping file path → complete file content as a string.',
    '                           Every path in files_modified MUST have an entry here.',
    '                           Write the COMPLETE file — not a stub, not a summary, not pseudo-code.',
    '                           The extension writes these strings directly to disk. If a file exists',
    '                           already its full new content must appear here. If creating a new file,',
    '                           include all imports, all functions, all classes, all tests.',
    '  "implementation_notes" — string: any deviations from the design or uncertainties',
    '  "confidence"           — "high" | "medium" | "low"',
    '',
    'CRITICAL: file_contents is not optional. An empty object or missing field means no code is',
    'written to disk and the pipeline produces no artifacts. If you cannot implement something',
    'completely, set confidence to "low" and explain in implementation_notes — but still write',
    'the best complete implementation you can in file_contents.',
  ].join("\n"),
  reviewer: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "status"  — "pass" | "fail" | "escalate" | "wrong_plan"',
    '             wrong_plan = plan is flawed, escalates back to Planner (not Coder retry)',
    '  "attempt" — integer',
    '  "issues"  — array of { severity, description, fix_instruction, checklist_item }',
    '             severity must be "critical" | "high" | "medium" | "low"',
    '  "escalate_reason" — string describing the escalation or wrong_plan reason, or null',
  ].join("\n"),
};

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

/**
 * Read workspace files listed in the design's modules array and return them
 * as { relativePath → fileContent } so the coder can modify existing code
 * rather than writing from scratch with no knowledge of what already exists.
 *
 * Files that don't exist yet (new files) are silently skipped — the coder
 * will create them from scratch, which is the correct behaviour.
 *
 * Size-limited to avoid blowing the model context: individual files > 8 KB
 * are included as a truncated excerpt with a note. The coder must still write
 * the complete file in file_contents.
 */
function readWorkspaceFilesForCoder(
  workspaceRoot: string,
  designOutput: unknown,
): Record<string, string> {
  const FILE_SIZE_LIMIT = 8 * 1024; // 8 KB per file
  const result: Record<string, string> = {};

  if (typeof designOutput !== "object" || designOutput === null) { return result; }
  const modules = (designOutput as Record<string, unknown>)["modules"];
  if (!Array.isArray(modules)) { return result; }

  for (const mod of modules) {
    if (typeof mod !== "object" || mod === null) { continue; }
    const relPath = (mod as Record<string, unknown>)["file"];
    if (typeof relPath !== "string" || !relPath) { continue; }

    const absPath = path.join(workspaceRoot, relPath);
    try {
      const stat = fs.statSync(absPath);
      if (!stat.isFile()) { continue; }
      let content = fs.readFileSync(absPath, "utf-8");
      if (content.length > FILE_SIZE_LIMIT) {
        content = content.slice(0, FILE_SIZE_LIMIT) +
          `\n\n... [truncated — file is ${stat.size} bytes. Write the complete new version in file_contents.]\n`;
      }
      result[relPath] = content;
    } catch {
      // File doesn't exist yet (new file) — skip silently.
    }
  }
  return result;
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
  agentName: string,
  agentPrompt: string,
  context: Record<string, unknown>,
  token: vscode.CancellationToken,
): Promise<unknown> {
  const schemaHint = AGENT_OUTPUT_HINTS[agentName] ?? "Produce a JSON object matching your Output Contract schema.";
  const messages = [
    vscode.LanguageModelChatMessage.User(
      agentPrompt +
      "\n\n---\n\n" +
      "IMPORTANT — you are being driven by the CopilotHarness VS Code extension.\n" +
      "Your Input Contract tool calls (harness_get_active_session, harness_new_session,\n" +
      "harness_read_stage) have already been executed by the extension.\n" +
      "The results are in the input context below — do NOT call any tools.\n\n" +
      "Your ONLY task: produce VALID JSON matching your Output Contract.\n" +
      "Output ONLY the raw JSON object — no markdown fences, no explanation, nothing else.\n\n" +
      schemaHint,
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

// ── Stage output → Markdown ───────────────────────────────────────────────────

function _str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}
function _list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function _obj(v: unknown): Record<string, unknown> {
  return (typeof v === "object" && v !== null && !Array.isArray(v))
    ? v as Record<string, unknown> : {};
}

function planToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const lines: string[] = [
    `# Plan`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    `**Summary:** ${_str(o["summary"], "_none_")}`,
    ``,
    `## Tasks`,
    ``,
    `| ID | Description | Complexity | Files Affected |`,
    `|----|-------------|------------|----------------|`,
  ];
  for (const t of _list(o["tasks"])) {
    const task = _obj(t);
    const files = _list(task["files_affected"]).join(", ") || "—";
    lines.push(
      `| ${_str(task["id"])} | ${_str(task["description"])} | ${_str(task["complexity"])} | ${files} |`,
    );
  }
  for (const t of _list(o["tasks"])) {
    const task = _obj(t);
    const criteria = _list(task["acceptance_criteria"]);
    if (criteria.length) {
      lines.push(``, `### ${_str(task["id"])} — Acceptance Criteria`, ``);
      for (const c of criteria) { lines.push(`- ${c}`); }
    }
  }
  const skills = _list(o["required_skills"]);
  if (skills.length) {
    lines.push(``, `## Required Skills`, ``, skills.map(s => `- ${s}`).join("\n"));
  }
  const questions = _list(o["open_questions"]);
  if (questions.length) {
    lines.push(``, `## Open Questions`, ``, questions.map(q => `- ${q}`).join("\n"));
  }
  lines.push(``, `**Confidence:** ${_str(o["confidence"], "—")}`);
  return lines.join("\n");
}

function designToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const lines: string[] = [
    `# Design`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    `**Summary:** ${_str(o["summary"], "_none_")}`,
    ``,
    `**Tasks Addressed:** ${_list(o["tasks_addressed"]).join(", ") || "—"}`,
    ``,
    `## Modules`,
  ];
  for (const m of _list(o["modules"])) {
    const mod = _obj(m);
    lines.push(``, `### \`${_str(mod["file"])}\``, ``, `*${_str(mod["purpose"])}*`, ``);
    const iface = _list(mod["public_interface"]);
    if (iface.length) {
      lines.push(`| Name | Signature | Description |`, `|------|-----------|-------------|`);
      for (const fn of iface) {
        const f = _obj(fn);
        lines.push(`| ${_str(f["name"])} | \`${_str(f["signature"])}\` | ${_str(f["description"])} |`);
      }
    }
  }
  const schemas = _list(o["data_schemas"]);
  if (schemas.length) {
    lines.push(``, `## Data Schemas`);
    for (const s of schemas) {
      const schema = _obj(s);
      lines.push(``, `### ${_str(schema["name"])}`, ``, `| Field | Type | Description |`, `|-------|------|-------------|`);
      for (const f of _list(schema["fields"])) {
        const field = _obj(f);
        lines.push(`| ${_str(field["name"])} | ${_str(field["type"])} | ${_str(field["description"])} |`);
      }
    }
  }
  const deps = _list(o["dependencies"]);
  if (deps.length) {
    lines.push(``, `## Dependencies`, ``, deps.map(d => `- \`${d}\``).join("\n"));
  }
  const notes = _str(o["integration_notes"]);
  if (notes) { lines.push(``, `## Integration Notes`, ``, notes); }
  lines.push(``, `**Confidence:** ${_str(o["confidence"], "—")}`);
  return lines.join("\n");
}

function codeToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const lines: string[] = [
    `# Code`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    `**Summary:** ${_str(o["summary"], "_none_")}`,
    ``,
    `## Files Modified`,
    ``,
  ];
  for (const f of _list(o["files_modified"])) { lines.push(`- \`${f}\``); }
  const notes = _str(o["implementation_notes"]);
  if (notes) { lines.push(``, `## Implementation Notes`, ``, notes); }
  lines.push(``, `**Confidence:** ${_str(o["confidence"], "—")}`);
  return lines.join("\n");
}

function reviewToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const statusIcon: Record<string, string> = {
    pass: "✅", fail: "❌", escalate: "🚨", wrong_plan: "⚠️",
  };
  const status = _str(o["status"], "unknown");
  const lines: string[] = [
    `# Review`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    `**Status:** ${statusIcon[status] ?? "❓"} ${status}`,
    ``,
    `## Issues`,
    ``,
    `| Severity | Checklist Item | Description | Fix Instruction |`,
    `|----------|---------------|-------------|-----------------|`,
  ];
  for (const i of _list(o["issues"])) {
    const issue = _obj(i);
    lines.push(
      `| ${_str(issue["severity"])} | ${_str(issue["checklist_item"])} | ${_str(issue["description"])} | ${_str(issue["fix_instruction"])} |`,
    );
  }
  const reason = _str(o["escalate_reason"]);
  if (reason) { lines.push(``, `## Escalation / Wrong Plan Reason`, ``, reason); }
  return lines.join("\n");
}

const _STAGE_RENDERER: Record<
  string,
  (o: Record<string, unknown>, sid: string, attempt: number) => string
> = {
  plan:   planToMarkdown,
  design: designToMarkdown,
  code:   codeToMarkdown,
  review: reviewToMarkdown,
};

function materializeStageOutput(
  workspaceRoot: string,
  sessionId: string,
  stage: string,
  attempt: number,
  output: unknown,
): void {
  const renderer = _STAGE_RENDERER[stage];
  if (!renderer || typeof output !== "object" || output === null) { return; }
  const md = renderer(output as Record<string, unknown>, sessionId, attempt);
  const dir = path.join(workspaceRoot, ".harness", "sessions", sessionId);
  fs.mkdirSync(dir, { recursive: true });
  // Include attempt suffix so correction-loop retries don't overwrite.
  const suffix = attempt > 1 ? `.attempt${attempt}` : "";
  fs.writeFileSync(path.join(dir, `${stage}${suffix}.md`), md, "utf-8");
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

    // Re-read workspace files after the previous coder attempt materialised them,
    // so the retry sees the current (possibly partially correct) state on disk.
    const existingFiles = readWorkspaceFilesForCoder(workspaceRoot, coderCtx["design"]);
    if (Object.keys(existingFiles).length > 0) {
      coderCtx["existing_file_contents"] = existingFiles;
    }

    const fixedCode = await runAgentLM(model, "coder", loadAgentPrompt(workspaceRoot, "coder"), coderCtx, token);
    await writeStage(client, sessionId, "code", "coder", fixedCode);
    materializeCoderFiles(workspaceRoot, fixedCode, stream);
    materializeStageOutput(workspaceRoot, sessionId, "code", codeAttempt, fixedCode);

    stream.progress(`Re-running reviewer (attempt ${codeAttempt})`);
    const reviewerCtx = await readAgentContext(client, sessionId, "reviewer", ["code", "plan", "design"]);
    const newReview = (await runAgentLM(
      model, "reviewer", loadAgentPrompt(workspaceRoot, "reviewer"), reviewerCtx, token,
    )) as ReviewOutput;
    await writeStage(client, sessionId, "review", "reviewer", newReview);
    materializeStageOutput(workspaceRoot, sessionId, "review", codeAttempt, newReview);

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

    // Inject existing workspace file contents into coder context so the model
    // can see what already exists and produce real modifications rather than
    // writing from scratch with no knowledge of the current codebase.
    if (agent.name === "coder") {
      const existingFiles = readWorkspaceFilesForCoder(workspaceRoot, stageOutputs["design"] ?? context["design"]);
      if (Object.keys(existingFiles).length > 0) {
        context["existing_file_contents"] = existingFiles;
      }
    }

    const agentOutput = await runAgentLM(model, agent.name, loadAgentPrompt(workspaceRoot, agent.name), context, token);
    await writeStage(client, sessionId, agent.writeStage, agent.name, agentOutput);
    stageOutputs[agent.writeStage] = agentOutput;

    const attempt = statusData.stages[agent.writeStage]?.attempt ?? 1;
    materializeStageOutput(workspaceRoot, sessionId, agent.writeStage, attempt, agentOutput);
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
    model, agentDef.name, loadAgentPrompt(workspaceRoot, agentDef.name), context, token,
  );
  await writeStage(client, sessionId, agentDef.writeStage, agentDef.name, agentOutput);

  const stepAttempt = statusData.stages[agentDef.writeStage]?.attempt ?? 1;
  materializeStageOutput(workspaceRoot, sessionId, agentDef.writeStage, stepAttempt, agentOutput);
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
