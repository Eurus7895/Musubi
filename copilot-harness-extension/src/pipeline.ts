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
import {
  extractFileDiff, isResolveError, resolveCodeReviewInput,
} from "./codeReviewInput";
export { extractFileDiff, resolveCodeReviewInput } from "./codeReviewInput";
export type {
  CodeReviewInput, CodeReviewResolveError, CodeReviewResolveResult,
} from "./codeReviewInput";
import { McpClient } from "./mcpClient";
import { selectModelForAgent } from "./modelSelector";
import {
  spawnAndRunSubagent,
  type RunSubagentResult,
} from "./runners/subagentRunner";
import type { SubagentRoleId } from "./runners/subagentRunnerCore";
import {
  StageSpawnBudget,
  SubagentBudgetExhausted,
} from "./runners/pipelineSubagentBudget";
import { runStageReviewGate, type StageGateOutcome } from "./pipelineGateUi";
import { preSpawnAndSplice } from "./subagentDispatcherRun";
import {
  coerceToEscalation,
  parseEscalationRules,
  shouldEscalate,
  type EscalationRules,
} from "./correctionRules";

// Re-export so external callers can import budget primitives + helper
// from a single module (the pipeline runner is the public surface).
export { StageSpawnBudget, SubagentBudgetExhausted };

/**
 * Phase G.1.6 — default per-stage spawn budget. Pipeline.yaml may
 * override per-pipeline later (Phase G.2 schema work). For now these
 * are the values the team agreed on while planning the gate:
 *
 *   - planner: 0   (plans don't need lookups; the request describes intent)
 *   - designer: 1  (rare design-time clarification)
 *   - coder:    5  (the stage that benefits most from spawns)
 *   - reviewer: 3  (one reviewer-aux per file in typical small chunks)
 *
 * Used by pipeline.ts when constructing a StageSpawnBudget for each
 * stage (or chunk-stage) attempt.
 */
export const DEFAULT_STAGE_SUBAGENT_BUDGET: Readonly<Record<string, number>> = {
  plan:     0,
  design:   1,
  code:     5,
  review:   3,
  // Phase H.1 — /code-review pipeline. The synthesis stage fans out one
  // reviewer-aux per high/medium-priority file from scope. Cap at 20
  // so a runaway scope (e.g. 100-file refactor) doesn't burn the LM.
  scope:     0,
  findings:  0,
  synthesis: 20,
};

/** Resolve the budget cap for a stage; defaults to 0 when unknown. */
export function defaultBudgetFor(stage: string): number {
  return DEFAULT_STAGE_SUBAGENT_BUDGET[stage] ?? 0;
}

// ── Stage tag presets — mirrors the "push-not-pull" injection contract ────
// The harness pushes these to each stage; we render them so the user can
// see the governance at a glance.
interface StageTags {
  skill?: string;
  memory?: string;
  firewall?: string;
  schema?: string;
  policy?: string;
}

const STAGE_TAGS: Record<string, StageTags> = {
  plan:   { memory: "MEMORY.md", policy: "Read·Grep·Glob" },
  design: { skill: "api-design", schema: "design.json" },
  code:   { skill: "python", policy: "Read·Write·Edit·Bash" },
  review: { skill: "code-review", firewall: "code only" },
  // Phase H.1 — /code-review pipeline.
  scope:     { skill: "pr-scope-detection", policy: "Read·Grep·Glob" },
  findings:  { skill: "per-file-review", policy: "Read·Grep·Glob" },
  synthesis: { skill: "code-review", firewall: "findings only" },
};
function tagsForRetry(): StageTags {
  return { skill: "python", firewall: "fix_instructions only" };
}

function renderTags(tags: StageTags): string {
  const parts: string[] = [];
  if (tags.memory)   parts.push(`◆ memory: \`${tags.memory}\``);
  if (tags.skill)    parts.push(`◈ skill: \`${tags.skill}\``);
  if (tags.schema)   parts.push(`{ } schema: \`${tags.schema}\``);
  if (tags.firewall) parts.push(`⟡ firewall: \`${tags.firewall}\``);
  if (tags.policy)   parts.push(`◇ policy: \`${tags.policy}\``);
  return parts.join(" · ");
}

function fmtSeconds(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = ms / 1000;
  return s < 10 ? s.toFixed(1) + "s" : Math.round(s) + "s";
}

function summarizeStageOutput(stage: string, output: unknown): string {
  if (typeof output !== "object" || output === null) return "schema ✓";
  const o = output as Record<string, unknown>;
  switch (stage) {
    case "plan": {
      const n = Array.isArray(o.tasks) ? o.tasks.length : 0;
      return `${n}-step plan, schema ✓`;
    }
    case "design": {
      const n = Array.isArray(o.modules) ? o.modules.length : 0;
      return `${n} module${n === 1 ? "" : "s"}, schema ✓`;
    }
    case "code": {
      const n = Array.isArray(o.files_modified) ? o.files_modified.length : 0;
      return `${n} file${n === 1 ? "" : "s"}, schema ✓`;
    }
    case "review": {
      const status = typeof o.status === "string" ? o.status : "unknown";
      return `review: ${status}`;
    }
    default: return "schema ✓";
  }
}

function firstFixInstruction(review: { issues?: Array<{ fix_instruction?: string }> }): string {
  if (!review.issues || !review.issues.length) return "";
  for (const i of review.issues) {
    if (i && typeof i.fix_instruction === "string" && i.fix_instruction) return i.fix_instruction;
  }
  return "";
}

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

// ── Observability — lets the user confirm the LLM actually ran ────────────────

let _pipelineLogger: vscode.OutputChannel | undefined;
function logger(): vscode.OutputChannel {
  if (!_pipelineLogger) {
    _pipelineLogger = vscode.window.createOutputChannel("CopilotHarness Pipeline");
  }
  return _pipelineLogger;
}

function logLine(msg: string): void {
  const ts = new Date().toISOString().substring(11, 23);
  logger().appendLine(`[${ts}] ${msg}`);
}

function dumpRawResponse(
  workspaceRoot: string,
  sessionId: string,
  stage: string,
  attempt: number,
  text: string,
): string | null {
  try {
    const dir = path.join(workspaceRoot, ".harness", "sessions", sessionId);
    fs.mkdirSync(dir, { recursive: true });
    const suffix = attempt > 1 ? `.attempt${attempt}` : "";
    const file = path.join(dir, `${stage}${suffix}_raw.txt`);
    fs.writeFileSync(file, text, "utf-8");
    return file;
  } catch (err) {
    logLine(`  (failed to dump raw response: ${err instanceof Error ? err.message : String(err)})`);
    return null;
  }
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
    '  "modules"          — array of { file, purpose, public_interface, task_id }',
    '                        task_id (Phase G.1.7): the SINGLE plan task ID this',
    '                        module implements (e.g. "T1"). Used to chunk large',
    '                        designs so the coder runs once per task instead of',
    '                        once over all modules. If a module legitimately',
    '                        implements multiple tasks, omit task_id; the harness',
    '                        falls back to extracting it from `purpose` text.',
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
    '  "issues"  — array of { severity, category, description, fix_instruction, checklist_item }',
    '             severity ∈ "critical" | "high" | "medium" | "low"',
    '             category ∈ "security" | "data-loss" | "performance" | "style" |',
    '                       "correctness" | "breaking-change" | "other"',
    '             (Phase G.2: category is REQUIRED. Used by correction.escalate_on_categories',
    '              to halt retries on critical findings in specific categories.)',
    '  "escalate_reason" — string describing the escalation or wrong_plan reason, or null',
  ].join("\n"),
  // Phase H.1 — /code-review pipeline agents.
  scoper: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "summary"     — string: one-line overview of what this PR/branch changes',
    '  "files"       — array of { path, kind, priority, size_lines, reason }',
    '                  kind ∈ "source" | "test" | "config" | "docs" | "generated" | "lockfile"',
    '                  priority ∈ "high" | "medium" | "low" | "skip"',
    '  "scope_notes" — array of strings (cross-cutting concerns)',
  ].join("\n"),
  finder: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "summary"               — string: one-line overview of cross-cutting concerns',
    '  "raw_findings"          — array of { severity, category, files, description, evidence }',
    '                            severity ∈ "critical" | "high" | "medium" | "low"',
    '                            category ∈ "architecture" | "contract" | "intent" | "risk" | "other"',
    '                            files: array of paths the finding touches (1+)',
    '  "per_file_priorities"   — array of { path, ask_reviewer_aux_to_focus_on } (hint to fan-out)',
  ].join("\n"),
  synthesizer: [
    'Produce a JSON object with exactly these top-level keys:',
    '  "status"   — "pass" | "fail" | "escalate"',
    '              pass: no critical/high issues, advisory only',
    '              fail: critical/high present, surfaced to user inline (no retry)',
    '              escalate: sub-agent outputs disagree and synthesis cannot reconcile',
    '  "summary"  — string: one-paragraph overall assessment',
    '  "report"   — { issues: [{severity, category, file, line, description, fix_suggestion, source}],',
    '                stats: {files_reviewed, files_skipped, critical_count, high_count, medium_count, low_count} }',
    '                source ∈ "finder" | "reviewer-aux" | "both"',
  ].join("\n"),
};

// ── Agent pipeline definition ─────────────────────────────────────────────────

const AGENT_PIPELINE = [
  { name: "planner"  as const, readStages: ["plan"]           as const, writeStage: "plan"   },
  { name: "designer" as const, readStages: ["plan"]           as const, writeStage: "design" },
  { name: "coder"    as const, readStages: ["design", "plan"] as const, writeStage: "code"   },
  // Reviewer is an evaluator — sees only the code artifact. The Python
  // firewall (context_builder._STAGE_PERMISSIONS["reviewer"] = {"code"})
  // blocks plan/design reads regardless; listing only "code" here avoids
  // two wasted MCP round-trips per reviewer invocation.
  { name: "reviewer" as const, readStages: ["code"]           as const, writeStage: "review" },
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

function loadAgentPrompt(
  roots: string | string[],
  pipelineName: string,
  agentName: string,
): string {
  // Agents live in a flat shared catalog at .github/agents/. Pipelines
  // compose them by reference. Resolution order, per root (workspace first,
  // extension bundle as fallback):
  //   1. .github/agents/<pipelineName>-<agentName>.agent.md
  //        (pipeline-specific variant — e.g. pipeline-builder-planner)
  //   2. .github/agents/<agentName>.agent.md
  //        (canonical / feature-dev / shared agents like skill-builder)
  // The first hit wins. Without the extension-bundle fallback, opening the
  // extension in any other workspace would silently drop to the generic
  // placeholder prompt below.
  const rootList = Array.isArray(roots) ? roots.filter(Boolean) : [roots];
  for (const root of rootList) {
    const candidates: string[] = [];
    if (pipelineName && pipelineName !== "feature-dev") {
      candidates.push(path.join(root, ".github", "agents", `${pipelineName}-${agentName}.agent.md`));
    }
    candidates.push(path.join(root, ".github", "agents", `${agentName}.agent.md`));
    for (const filePath of candidates) {
      try {
        return fs.readFileSync(filePath, "utf-8");
      } catch {
        // try next candidate
      }
    }
  }
  return (
    `You are the ${agentName} agent in the CopilotHarness pipeline. ` +
    `Analyse the provided input context and produce valid JSON output matching your output schema.`
  );
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
 * Walk the workspace root and return a list of file paths (relative to root)
 * the planner / designer can use to ground their module choices in the real
 * project layout. Without this, agents fall back to placeholders like
 * "path/to/test_file.py" because they have no view of the workspace.
 *
 * Skips heavy / irrelevant trees (node_modules, .git, dist, build, __pycache__,
 * venv, .venv, .harness, .vscode) and caps the result at MAX_ENTRIES to keep
 * the prompt bounded in large repos. Directories are listed before their
 * contents so the agent sees structure even when the cap truncates files.
 */
function readWorkspaceTree(workspaceRoot: string): string[] {
  const SKIP = new Set([
    "node_modules", ".git", "dist", "build", "__pycache__",
    "venv", ".venv", ".harness", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "out", "target",
  ]);
  const MAX_ENTRIES = 400;
  const MAX_DEPTH = 6;

  const entries: string[] = [];
  const walk = (abs: string, rel: string, depth: number): void => {
    if (entries.length >= MAX_ENTRIES || depth > MAX_DEPTH) { return; }
    let children: fs.Dirent[];
    try {
      children = fs.readdirSync(abs, { withFileTypes: true });
    } catch {
      return;
    }
    children.sort((a, b) => {
      if (a.isDirectory() !== b.isDirectory()) return a.isDirectory() ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const child of children) {
      if (entries.length >= MAX_ENTRIES) { return; }
      if (child.name.startsWith(".") && child.name !== ".github") { continue; }
      if (SKIP.has(child.name)) { continue; }
      const childRel = rel ? `${rel}/${child.name}` : child.name;
      if (child.isDirectory()) {
        entries.push(childRel + "/");
        walk(path.join(abs, child.name), childRel, depth + 1);
      } else if (child.isFile()) {
        entries.push(childRel);
      }
    }
  };
  walk(workspaceRoot, "", 0);
  return entries;
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
  chunkId?: string,
): Promise<Record<string, unknown>> {
  const merged: Record<string, unknown> = {};
  for (const stage of readStages) {
    const args: Record<string, unknown> = {
      session_id: sessionId, stage, agent_name: agentName,
    };
    if (chunkId) { args.chunk_id = chunkId; }
    const result = (await callHarness(client, "harness_read_stage", args)) as HarnessReadResult & { user_hint?: string };
    if (result.data !== null && result.data !== undefined) {
      merged[stage] = result.data;
    }
    if (result.injected_skills) {
      merged["injected_skills"] = result.injected_skills;
    }
    if (typeof result.user_hint === "string" && result.user_hint.trim()) {
      merged["user_hint"] = result.user_hint;
    }
  }
  return merged;
}

interface AgentObs {
  workspaceRoot: string;
  sessionId: string;
  stage: string;
  attempt: number;
  // G.3: when set, runAgentLM writes a stage_metrics row after each
  // sendRequest. Fire-and-forget; failures don't abort the pipeline.
  client?: McpClient;
  chunkId?: string;
}

async function runAgentLM(
  roots: readonly string[],
  agentName: string,
  agentPrompt: string,
  context: Record<string, unknown>,
  token: vscode.CancellationToken,
  obs?: AgentObs,
): Promise<unknown> {
  // Honor the agent's `model:` frontmatter — and let any skill the harness
  // already injected into this context override it via `model:` in its
  // own SKILL.md. First skill with a declared model wins; otherwise the
  // agent default applies. See modelSelector.ts for the full chain.
  const injected = context["injected_skills"];
  const activeSkills =
    injected && typeof injected === "object"
      ? Object.keys(injected as Record<string, unknown>)
      : [];
  const model = await selectModelForAgent({
    roots, agentName, skills: activeSkills, log: logLine,
  });
  const schemaHint = AGENT_OUTPUT_HINTS[agentName] ?? "Produce a JSON object matching your Output Contract schema.";
  const systemMsg =
    agentPrompt +
    "\n\n---\n\n" +
    "IMPORTANT — you are being driven by the CopilotHarness VS Code extension.\n" +
    "Your Input Contract tool calls (harness_get_active_session, harness_new_session,\n" +
    "harness_read_stage) have already been executed by the extension.\n" +
    "The results are in the input context below — do NOT call any tools.\n\n" +
    "Your ONLY task: produce VALID JSON matching your Output Contract.\n" +
    "Output ONLY the raw JSON object — no markdown fences, no explanation, nothing else.\n\n" +
    schemaHint;
  const contextMsg = `Input context from the harness:\n\n${JSON.stringify(context, null, 2)}`;
  const messages = [
    vscode.LanguageModelChatMessage.User(systemMsg),
    vscode.LanguageModelChatMessage.User(contextMsg),
  ];

  const promptChars = systemMsg.length + contextMsg.length;
  logLine(`→ ${agentName}: sending ${promptChars.toLocaleString()} chars to ${model.id} (family=${model.family})`);

  const t0 = Date.now();
  const response = await model.sendRequest(messages, {}, token);
  let text = "";
  let chunks = 0;
  let firstChunkMs: number | null = null;
  for await (const chunk of response.text) {
    if (firstChunkMs === null) { firstChunkMs = Date.now() - t0; }
    text += chunk;
    chunks++;
  }
  const elapsed = Date.now() - t0;

  logLine(
    `← ${agentName}: received ${text.length.toLocaleString()} chars in ${chunks} chunk(s), ` +
    `first-chunk=${firstChunkMs ?? "n/a"}ms, total=${elapsed}ms`,
  );

  if (text.length === 0) {
    logLine(`  WARNING: empty response from ${agentName} — model may be unauthorized, rate-limited, or cancelled`);
  }

  if (obs) {
    const dumped = dumpRawResponse(obs.workspaceRoot, obs.sessionId, obs.stage, obs.attempt, text);
    if (dumped) { logLine(`  raw response dumped → ${dumped}`); }

    // G.3: append a stage_metrics row. Fire-and-forget — observability
    // writes must never block / abort a pipeline run. Token counts are
    // estimates (chars/4 heuristic mirrored from orchestratorCore).
    if (obs.client) {
      const startedAt = t0 / 1000;
      const endedAt = Date.now() / 1000;
      const tokensIn = Math.max(1, Math.floor(promptChars / 4));
      const tokensOut = Math.max(0, Math.floor(text.length / 4));
      const args: Record<string, unknown> = {
        session_id: obs.sessionId,
        stage: obs.stage,
        attempt: obs.attempt,
        started_at: startedAt,
        ended_at: endedAt,
        tokens_in_estimate: tokensIn,
        tokens_out_estimate: tokensOut,
        lm_ms: elapsed,
      };
      if (obs.chunkId) { args.chunk_id = obs.chunkId; }
      obs.client.callTool("harness_record_stage_metric", args).catch(err => {
        const msg = err instanceof Error ? err.message : String(err);
        logLine(`  (stage_metric write failed: ${msg})`);
      });
    }
  }

  return extractJson(text);
}

async function writeStage(
  client: McpClient,
  sessionId: string,
  stage: string,
  agentName: string,
  output: unknown,
  chunkId?: string,
): Promise<HarnessWriteResult> {
  const args: Record<string, unknown> = {
    session_id: sessionId, stage, output: JSON.stringify(output), agent_name: agentName,
  };
  if (chunkId) { args.chunk_id = chunkId; }
  return (await callHarness(client, "harness_write_stage", args)) as HarnessWriteResult;
}

/**
 * Run an agent and write its output, retrying on validation failure.
 *
 * harness_write_stage can reject output for schema/secrets/contract reasons
 * (e.g. coder writes a file not in design.modules). Without a retry loop,
 * the user sees "Output rejected" with no recovery — they have to abandon
 * the session and start over. This function feeds validation_errors back
 * into the agent context so it can self-correct, sharing the 3-attempt
 * budget with the reviewer loop.
 */
async function runAgentWithValidationRetry(
  client: McpClient,
  roots: readonly string[],
  agentName: string,
  agentPrompt: string,
  baseContext: Record<string, unknown>,
  sessionId: string,
  stage: string,
  initialAttempt: number,
  workspaceRoot: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  onChange?: () => void,
  chunkId?: string,
): Promise<{ output: unknown; finalAttempt: number }> {
  let attempt = initialAttempt;
  let context = baseContext;

  for (;;) {
    if (token.isCancellationRequested) {
      throw new Error("cancelled");
    }
    const output = await runAgentLM(
      roots, agentName, agentPrompt, context, token,
      { workspaceRoot, sessionId, stage, attempt, client, chunkId },
    );
    const result = await writeStage(client, sessionId, stage, agentName, output, chunkId);
    if (result.status === "stored") {
      return { output, finalAttempt: attempt };
    }

    const details = result.validation_errors?.join("\n") ?? "";
    const errLine = `${result.error ?? "unknown"}${details ? "\n" + details : ""}`;

    if (attempt >= MAX_CODE_ATTEMPTS) {
      throw new Error(
        `harness_write_stage failed for '${stage}' after ${attempt} attempt(s): ${errLine}`.trim(),
      );
    }

    stream.markdown(
      `\n> ⚠️ **${agentName} → validation failed** (attempt ${attempt}/${MAX_CODE_ATTEMPTS})` +
      (details ? `\n>\n> ${details.split("\n").join("\n> ")}` : "") +
      `\n\n`,
    );

    const incArgs: Record<string, unknown> = { session_id: sessionId, stage };
    if (chunkId) { incArgs.chunk_id = chunkId; }
    await callHarness(client, "harness_increment_attempt", incArgs);
    attempt += 1;
    onChange?.();

    context = {
      ...baseContext,
      validation_feedback: {
        previous_attempt: attempt - 1,
        error: result.error,
        validation_errors: result.validation_errors ?? [],
        instruction:
          "Your previous output was rejected by the harness validator. " +
          "Fix the listed errors and produce a corrected output that conforms to your Output Contract.",
      },
    };
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

// ── /code-review pipeline renderers (Phase H.1) ──────────────────────────────

function scopeToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const summary = _str(o["summary"], "(no summary)");
  const files = _list(o["files"]);
  const notes = _list(o["scope_notes"]);
  const lines: string[] = [
    `# Scope`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    summary,
    ``,
    `## Files (${files.length})`,
    ``,
    `| Priority | Path | Kind | Lines | Reason |`,
    `|----------|------|------|-------|--------|`,
  ];
  for (const f of files) {
    const r = _obj(f);
    lines.push(
      `| ${_str(r["priority"], "?")} ` +
      `| \`${_str(r["path"], "")}\` ` +
      `| ${_str(r["kind"], "?")} ` +
      `| ${_str(r["size_lines"], "?")} ` +
      `| ${_str(r["reason"], "")} |`,
    );
  }
  if (notes.length > 0) {
    lines.push(``, `## Scope notes`, ``);
    for (const n of notes) { lines.push(`- ${_str(n, "")}`); }
  }
  return lines.join("\n");
}

function findingsToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const summary = _str(o["summary"], "(no summary)");
  const raw = _list(o["raw_findings"]);
  const focuses = _list(o["per_file_priorities"]);
  const lines: string[] = [
    `# Findings (cross-cutting)`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    summary,
    ``,
    `## Raw findings (${raw.length})`,
    ``,
  ];
  for (const f of raw) {
    const r = _obj(f);
    const files = _list(r["files"]).map(p => `\`${_str(p, "")}\``).join(", ");
    lines.push(
      `- **${_str(r["severity"], "?")}** [${_str(r["category"], "?")}] ${files}: ` +
      _str(r["description"], ""),
    );
    const evidence = _str(r["evidence"], "");
    if (evidence) { lines.push(`  > ${evidence}`); }
  }
  if (focuses.length > 0) {
    lines.push(``, `## Per-file reviewer-aux focuses`, ``);
    for (const f of focuses) {
      const r = _obj(f);
      lines.push(`- \`${_str(r["path"], "")}\` — ${_str(r["ask_reviewer_aux_to_focus_on"], "")}`);
    }
  }
  return lines.join("\n");
}

function synthesisToMarkdown(o: Record<string, unknown>, sessionId: string, attempt: number): string {
  const statusIcon: Record<string, string> = { pass: "✅", fail: "❌", escalate: "🚨" };
  const status = _str(o["status"], "unknown");
  const summary = _str(o["summary"], "(no summary)");
  const report = _obj(o["report"]);
  const issues = _list(report["issues"]);
  const stats = _obj(report["stats"]);
  const lines: string[] = [
    `# Code Review`,
    `> ${sessionId} | attempt ${attempt}`,
    ``,
    `**Status:** ${statusIcon[status] ?? "❓"} ${status}`,
    ``,
    summary,
    ``,
    `## Stats`,
    ``,
    `- files reviewed: ${_str(stats["files_reviewed"], "?")} ` +
    `(skipped: ${_str(stats["files_skipped"], "?")})`,
    `- critical: ${_str(stats["critical_count"], "0")} · ` +
    `high: ${_str(stats["high_count"], "0")} · ` +
    `medium: ${_str(stats["medium_count"], "0")} · ` +
    `low: ${_str(stats["low_count"], "0")}`,
    ``,
    `## Issues (${issues.length})`,
    ``,
    `| Severity | Category | File:Line | Description | Fix |`,
    `|----------|----------|-----------|-------------|-----|`,
  ];
  for (const i of issues) {
    const r = _obj(i);
    const file = _str(r["file"], "");
    const line = _str(r["line"], "");
    const loc = file ? (line && line !== "0" ? `\`${file}:${line}\`` : `\`${file}\``) : "—";
    lines.push(
      `| ${_str(r["severity"], "?")} ` +
      `| ${_str(r["category"], "?")} ` +
      `| ${loc} ` +
      `| ${_str(r["description"], "")} ` +
      `| ${_str(r["fix_suggestion"], "")} |`,
    );
  }
  return lines.join("\n");
}

const _STAGE_RENDERER: Record<
  string,
  (o: Record<string, unknown>, sid: string, attempt: number) => string
> = {
  plan:      planToMarkdown,
  design:    designToMarkdown,
  code:      codeToMarkdown,
  // Phase H.1 — /code-review pipeline.
  scope:     scopeToMarkdown,
  findings:  findingsToMarkdown,
  synthesis: synthesisToMarkdown,
  // feature-dev's review stage (kept after the H.1 inserts to keep
  // the legacy entry adjacent to its renderer).
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
  sessionId: string,
  workspaceRoot: string,
  promptRoots: string[],
  pipelineName: string,
  initialReview: ReviewOutput,
  codeAttempt: number,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  onChange?: () => void,
  chunkId?: string,
  correctionRules?: EscalationRules,
): Promise<ReviewOutput> {
  let currentReview = initialReview;
  const chunkLabel = chunkId ? ` · ${chunkId}` : "";
  // G.2: rules default to escalate_on_critical=true when caller omits them.
  const rules = correctionRules ?? parseEscalationRules(null);

  while (currentReview.status === "fail" && codeAttempt < MAX_CODE_ATTEMPTS) {
    if (token.isCancellationRequested) { break; }

    codeAttempt++;

    // Reviewer verdict + fix_instructions block — rendered as a blockquote
    // in the chat so the user sees what went wrong before the retry starts.
    const fix = firstFixInstruction(currentReview);
    const issuesCount = currentReview.issues?.length ?? 0;
    stream.markdown(
      `\n> ⚠️ **reviewer${chunkLabel} → ${currentReview.status}** · ${issuesCount} issue${issuesCount === 1 ? "" : "s"}` +
      (fix ? `\n>\n> Fix: ${fix}` : "") +
      `\n\n`,
    );

    const incArgs: Record<string, unknown> = { session_id: sessionId };
    if (chunkId) { incArgs.chunk_id = chunkId; }
    await callHarness(client, "harness_increment_attempt", { ...incArgs, stage: "code" });
    await callHarness(client, "harness_increment_attempt", { ...incArgs, stage: "review" });

    const coderCtx = await readAgentContext(
      client, sessionId, "coder", ["design", "plan", "review"], chunkId,
    );

    // Filter design to current chunk's modules when running chunked.
    if (chunkId && typeof coderCtx["design"] === "object" && coderCtx["design"] !== null) {
      coderCtx["design"] = filterDesignForChunkPaths(
        coderCtx["design"] as Record<string, unknown>,
        _chunkFilePathsCache.get(`${sessionId}/${chunkId}`) ?? [],
      );
    }

    // Re-read workspace files after the previous coder attempt materialised them,
    // so the retry sees the current (possibly partially correct) state on disk.
    const existingFiles = readWorkspaceFilesForCoder(workspaceRoot, coderCtx["design"]);
    if (Object.keys(existingFiles).length > 0) {
      coderCtx["existing_file_contents"] = existingFiles;
    }

    emitStageStart(stream, `coder${chunkLabel}`, codeAttempt, MAX_CODE_ATTEMPTS, tagsForRetry());
    onChange?.();
    const coderT0 = Date.now();
    const { output: fixedCode, finalAttempt: coderFinalAttempt } = await runAgentWithValidationRetry(
      client, promptRoots, "coder", loadAgentPrompt(promptRoots, pipelineName, "coder"),
      coderCtx, sessionId, "code", codeAttempt, workspaceRoot, stream, token, onChange, chunkId,
    );
    codeAttempt = coderFinalAttempt;
    materializeCoderFiles(workspaceRoot, fixedCode, stream);
    if (!chunkId) {
      materializeStageOutput(workspaceRoot, sessionId, "code", codeAttempt, fixedCode);
    }
    emitStageComplete(stream, `coder${chunkLabel}`, Date.now() - coderT0, summarizeStageOutput("code", fixedCode));
    emitStageOutputDetails(stream, "code", fixedCode);
    if (!chunkId) {
      emitStageArtifactAnchor(stream, workspaceRoot, sessionId, "code", codeAttempt);
    }
    onChange?.();

    // Evaluator firewall: reviewer sees only the (new) code artifact.
    const reviewerCtx = await readAgentContext(client, sessionId, "reviewer", ["code"], chunkId);
    emitStageStart(stream, `reviewer${chunkLabel}`, codeAttempt, MAX_CODE_ATTEMPTS, STAGE_TAGS["review"]);
    onChange?.();
    const reviewerT0 = Date.now();
    const { output: newReviewOutput, finalAttempt: reviewerFinalAttempt } = await runAgentWithValidationRetry(
      client, promptRoots, "reviewer", loadAgentPrompt(promptRoots, pipelineName, "reviewer"),
      reviewerCtx, sessionId, "review", codeAttempt, workspaceRoot, stream, token, onChange, chunkId,
    );
    const newReviewRaw = newReviewOutput as ReviewOutput;
    // G.2: apply escalate-on-rules BEFORE materialising / rendering so
    // the chat marker reflects the coerced status.
    const newReview = applyCorrectionRules(newReviewRaw, rules, stream, chunkLabel);
    if (!chunkId) {
      materializeStageOutput(workspaceRoot, sessionId, "review", reviewerFinalAttempt, newReview);
    }
    emitStageComplete(stream, `reviewer${chunkLabel}`, Date.now() - reviewerT0, summarizeStageOutput("review", newReview));
    emitStageOutputDetails(stream, "review", newReview);
    if (!chunkId) {
      emitStageArtifactAnchor(stream, workspaceRoot, sessionId, "review", reviewerFinalAttempt);
    }
    onChange?.();

    currentReview = newReview;
    if (newReview.status === "pass" || newReview.status === "escalate") { break; }
  }

  return currentReview;
}

// ── Phase G.1.7: chunked execution ─────────────────────────────────────

/**
 * One per-task chunk returned by `harness_compute_chunks`. Mirrors the
 * Python `Chunk` dataclass; kept TS-side as a plain interface so tests
 * don't have to spin up the MCP boundary.
 */
export interface ChunkInfo {
  chunk_id: string;
  task_label: string;
  file_paths: string[];
}

interface RunChunkedOptions {
  client: McpClient;
  workspaceRoot: string;
  promptRoots: string[];
  meta: { route: string; pipelineName: string; level: number };
  sessionId: string;
  chunks: ChunkInfo[];
  stageOutputs: Record<string, unknown>;
  stream: vscode.ChatResponseStream;
  token: vscode.CancellationToken;
  onChange?: () => void;
  pipelineT0: number;
}

/**
 * Run coder + reviewer + correction loop once per chunk, with a stage
 * review gate after each chunk. Returns a `PipelineResult` so the
 * caller can exit `runPipeline` early — the AGENT_PIPELINE iteration
 * never advances past the coder slot when chunked execution kicks in.
 *
 * Per-chunk semantics:
 *   - Coder reads `design` filtered to the chunk's modules.
 *   - Reviewer sees only the chunk's code attempt (existing firewall
 *     pass through `harness_read_stage` with `chunk_id`).
 *   - Correction loop is shared by reviewer + coder for the chunk.
 *   - Gate asks the user to approve / retry / abort / auto-approve-rest;
 *     retry bumps `attempt` for code+review of THIS chunk only.
 *
 * Aborted or escalated chunks halt the whole pipeline (Phase G.1.7
 * default — downstream chunks may import upstream code so partial
 * success isn't safe).
 */
async function runChunkedCodeAndReview(
  opts: RunChunkedOptions,
): Promise<PipelineResult> {
  const {
    client, workspaceRoot, promptRoots, meta,
    sessionId, chunks, stageOutputs, stream, token, onChange, pipelineT0,
  } = opts;

  stream.markdown(
    `\n📦 **Chunked execution** — ${chunks.length} task chunks. ` +
    `Each runs coder + reviewer scoped to its task; gate fires between chunks.\n`,
  );

  // G.2: load correction rules once for the whole chunked run. All
  // chunks share the same pipeline.yaml correction config.
  const chunkRules = await fetchCorrectionRules(client, meta.pipelineName);

  for (let chunkIdx = 0; chunkIdx < chunks.length; chunkIdx++) {
    if (token.isCancellationRequested) { break; }
    const chunk = chunks[chunkIdx];
    _chunkFilePathsCache.set(`${sessionId}/${chunk.chunk_id}`, chunk.file_paths);

    stream.markdown(
      `\n### 📦 Chunk ${chunkIdx + 1}/${chunks.length} · **${chunk.chunk_id}** — ${chunk.task_label}\n` +
      `*${chunk.file_paths.length} module${chunk.file_paths.length === 1 ? "" : "s"}*\n`,
    );

    let chunkSatisfied = false;
    while (!chunkSatisfied) {
      if (token.isCancellationRequested) { break; }

      // Ensure attempt rows exist for code + review for this chunk.
      const codeRowRaw = await callHarness(client, "harness_ensure_chunk_row", {
        session_id: sessionId, stage: "code", chunk_id: chunk.chunk_id,
      });
      await callHarness(client, "harness_ensure_chunk_row", {
        session_id: sessionId, stage: "review", chunk_id: chunk.chunk_id,
      });
      const codeAttempt = (codeRowRaw as { attempt?: number }).attempt ?? 1;

      // ── Coder for this chunk ──────────────────────────────────────────────
      const coderCtx = await readAgentContext(
        client, sessionId, "coder", ["plan", "design"], chunk.chunk_id,
      );
      if (typeof coderCtx["design"] === "object" && coderCtx["design"] !== null) {
        coderCtx["design"] = filterDesignForChunkPaths(
          coderCtx["design"] as Record<string, unknown>,
          chunk.file_paths,
        );
      }
      coderCtx["chunk"] = {
        chunk_id: chunk.chunk_id,
        task_label: chunk.task_label,
        files: chunk.file_paths,
      };
      const existingFiles = readWorkspaceFilesForCoder(workspaceRoot, coderCtx["design"]);
      if (Object.keys(existingFiles).length > 0) {
        coderCtx["existing_file_contents"] = existingFiles;
      }

      // ── Phase G.1.6: pre-spawn explorer scan for existing callers ────────
      const coderBudget = new StageSpawnBudget(
        sessionId, `code:${chunk.chunk_id}`, codeAttempt,
        defaultBudgetFor("code"),
      );
      const coderCtxAugmented = await preSpawnAndSplice({
        client,
        workspaceRoot,
        parentSessionId: sessionId,
        parentAgentName: "coder",
        budget: coderBudget,
        stage: "coder",
        chunkFilePaths: chunk.file_paths,
        chunkId: chunk.chunk_id,
        design: (coderCtx["design"] as Record<string, unknown> | undefined) ?? null,
        baseContext: coderCtx,
        roots: promptRoots,
        log: logLine,
        token,
        stream,
      });

      emitStageStart(
        stream, `coder · ${chunk.chunk_id}`, codeAttempt, MAX_CODE_ATTEMPTS,
        STAGE_TAGS["code"] ?? {},
      );
      onChange?.();
      const coderT0 = Date.now();
      let coderOutput: unknown;
      try {
        const r = await runAgentWithValidationRetry(
          client, promptRoots, "coder",
          loadAgentPrompt(promptRoots, meta.pipelineName, "coder"),
          coderCtxAugmented, sessionId, "code", codeAttempt,
          workspaceRoot, stream, token, onChange, chunk.chunk_id,
        );
        coderOutput = r.output;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: `Chunk ${chunk.chunk_id}: coder failed — ${msg}`,
        };
      }
      materializeCoderFiles(workspaceRoot, coderOutput, stream);
      emitStageComplete(
        stream, `coder · ${chunk.chunk_id}`, Date.now() - coderT0,
        summarizeStageOutput("code", coderOutput),
      );
      emitStageOutputDetails(stream, "code", coderOutput);
      onChange?.();

      // ── Reviewer for this chunk ────────────────────────────────────────────
      const reviewerCtx = await readAgentContext(
        client, sessionId, "reviewer", ["code"], chunk.chunk_id,
      );

      // ── Phase G.1.6: pre-spawn reviewer-aux per file when chunk has > 2 ────
      const reviewerBudget = new StageSpawnBudget(
        sessionId, `review:${chunk.chunk_id}`, codeAttempt,
        defaultBudgetFor("review"),
      );
      const reviewerCtxAugmented = await preSpawnAndSplice({
        client,
        workspaceRoot,
        parentSessionId: sessionId,
        parentAgentName: "reviewer",
        budget: reviewerBudget,
        stage: "reviewer",
        chunkFilePaths: chunk.file_paths,
        chunkId: chunk.chunk_id,
        design: null,  // reviewer firewall: no design access
        baseContext: reviewerCtx,
        roots: promptRoots,
        log: logLine,
        token,
        stream,
      });

      emitStageStart(
        stream, `reviewer · ${chunk.chunk_id}`, codeAttempt, MAX_CODE_ATTEMPTS,
        STAGE_TAGS["review"] ?? {},
      );
      onChange?.();
      const reviewerT0 = Date.now();
      let reviewOutput: ReviewOutput;
      try {
        const r = await runAgentWithValidationRetry(
          client, promptRoots, "reviewer",
          loadAgentPrompt(promptRoots, meta.pipelineName, "reviewer"),
          reviewerCtxAugmented, sessionId, "review", codeAttempt,
          workspaceRoot, stream, token, onChange, chunk.chunk_id,
        );
        // G.2: apply escalate-on-rules to the chunk's first review pass.
        reviewOutput = applyCorrectionRules(
          r.output as ReviewOutput, chunkRules, stream, ` · ${chunk.chunk_id}`,
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: `Chunk ${chunk.chunk_id}: reviewer failed — ${msg}`,
        };
      }
      emitStageComplete(
        stream, `reviewer · ${chunk.chunk_id}`, Date.now() - reviewerT0,
        summarizeStageOutput("review", reviewOutput),
      );
      emitStageOutputDetails(stream, "review", reviewOutput);
      onChange?.();

      // ── Correction loop for this chunk ────────────────────────────────────
      let finalReview: ReviewOutput = reviewOutput;
      if (reviewOutput.status === "fail") {
        finalReview = await runCorrectionLoop(
          client, sessionId, workspaceRoot, promptRoots, meta.pipelineName,
          reviewOutput, codeAttempt, stream, token, onChange, chunk.chunk_id,
          chunkRules,
        );
      }

      // ── Reviewer didn't pass: render an escalation gate so the user
      //    can retry-with-hint, force-approve, or abort. Without this,
      //    a reviewer escalation halted the chunked pipeline silently
      //    with no UI affordance.
      const reviewerNotOk = finalReview.status !== "pass";
      if (reviewerNotOk) {
        const reason =
          finalReview.status === "escalate"
            ? (finalReview.escalate_reason ?? "reviewer escalated")
            : "max correction attempts exhausted";
        stream.markdown(
          `\n> ⚠️ **chunk ${chunk.chunk_id} reviewer → ${finalReview.status}** — ${reason}\n`,
        );
        const escGate = await runStageReviewGate({
          client, stream, sessionId,
          pipelineName: meta.pipelineName,
          stage: "code",
          attempt: codeAttempt,
          elapsedMs: Date.now() - coderT0,
          log: logLine,
          chunkId: chunk.chunk_id,
          chunkLabel: `${chunk.task_label}  ·  reviewer ${finalReview.status}`,
        });
        if (escGate.kind === "aborted") {
          return {
            success: false, sessionId, stages: stageOutputs, escalated: true,
            escalation: `User aborted chunk ${chunk.chunk_id} after reviewer ${finalReview.status}.`,
          };
        }
        if (escGate.kind === "retry") {
          const incArgs: Record<string, unknown> = {
            session_id: sessionId, chunk_id: chunk.chunk_id,
          };
          await callHarness(client, "harness_increment_attempt", {
            ...incArgs, stage: "code", user_hint: escGate.userHint ?? null,
          });
          await callHarness(client, "harness_increment_attempt", {
            ...incArgs, stage: "review",
          });
          const hint = escGate.userHint ? ` with hint: ${escGate.userHint}` : "";
          stream.markdown(`\n↻ retrying chunk **${chunk.chunk_id}**${hint}\n`);
          continue;  // re-run this chunk
        }
        // approved | auto_approved → user explicitly accepts the
        // not-passing result and moves on. Surface a one-line warning so
        // the trail makes it obvious in the chat history.
        stream.markdown(
          `\n_⚠️ chunk ${chunk.chunk_id} accepted despite reviewer ${finalReview.status} (user override)._\n`,
        );
        chunkSatisfied = true;
        continue;
      }

      // ── G.1.5 gate per chunk ──────────────────────────────────────────────
      const gate = await runStageReviewGate({
        client, stream, sessionId,
        pipelineName: meta.pipelineName,
        stage: "code",
        attempt: codeAttempt,
        elapsedMs: Date.now() - coderT0,
        log: logLine,
        chunkId: chunk.chunk_id,
        chunkLabel: chunk.task_label,
      });

      if (gate.kind === "aborted") {
        stream.markdown(`\n_Pipeline aborted at chunk **${chunk.chunk_id}** gate._\n`);
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: `User aborted at chunk ${chunk.chunk_id} gate.`,
        };
      }
      if (gate.kind === "retry") {
        const incArgs: Record<string, unknown> = {
          session_id: sessionId, chunk_id: chunk.chunk_id,
        };
        await callHarness(client, "harness_increment_attempt", {
          ...incArgs, stage: "code", user_hint: gate.userHint ?? null,
        });
        await callHarness(client, "harness_increment_attempt", {
          ...incArgs, stage: "review",
        });
        const hint = gate.userHint ? ` with hint: ${gate.userHint}` : "";
        stream.markdown(`\n↻ retrying chunk **${chunk.chunk_id}**${hint}\n`);
        // Inner while loop: re-run THIS chunk.
        continue;
      }

      // approved | auto_approved → next chunk.
      chunkSatisfied = true;
    }
  }

  stream.markdown(
    `\n*total: ${fmtSeconds(Date.now() - pipelineT0)} · ${chunks.length} chunks*\n`,
  );
  return { success: true, sessionId, stages: stageOutputs, escalated: false };
}

// ── Phase G.3: pipeline_runs finalizer ──────────────────────────────

/**
 * Close out the pipeline_runs row for a session. Called from every
 * runPipeline return path (success, escalation, abort) and from the
 * chunked branch's terminal returns. Fire-and-forget — observability
 * write failures must never abort a pipeline run.
 *
 * `final_status` derivation:
 *   - escalated=true ⇒ "escalated"
 *   - success=true   ⇒ "success"
 *   - else           ⇒ "aborted"
 */
async function emitFinalize(
  client: McpClient,
  sessionId: string,
  result: { escalated: boolean; success: boolean },
  chunkCount: number,
): Promise<void> {
  const finalStatus =
    result.escalated ? "escalated" :
    result.success   ? "success"   :
    "aborted";
  try {
    // correction_attempts is auto-derived by the harness from
    // stage_outputs; the runner doesn't need to track it.
    await callHarness(client, "harness_finalize_pipeline_run", {
      session_id: sessionId,
      final_status: finalStatus,
      escalated: result.escalated,
      chunked: chunkCount > 1,
      chunk_count: chunkCount,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logLine(`(finalize_pipeline_run failed: ${msg})`);
  }
}

// ── Phase G.2: correction-rule helpers ──────────────────────────────

/**
 * Fetch the pipeline's `correction.escalate_on_*` rules via the
 * harness MCP tool. Returns DEFAULT_ESCALATION_RULES on any error
 * (the helper itself never raises — pipelines must keep running
 * even when the rules file is malformed).
 */
async function fetchCorrectionRules(
  client: McpClient, pipelineName: string,
): Promise<EscalationRules> {
  try {
    const raw = await callHarness(client, "harness_get_correction_rules", {
      pipeline_name: pipelineName,
    });
    const parsed = (raw as { rules?: unknown }).rules;
    return parseEscalationRules(parsed);
  } catch {
    return parseEscalationRules(null);
  }
}

/**
 * Apply correction rules to a fresh reviewer output. When a rule
 * matches, log the reason to chat and return a coerced review with
 * status='escalate'. Otherwise return the input unchanged.
 *
 * Called immediately after each reviewer LM round-trip — once on
 * the initial review, once per iteration of the correction loop.
 */
function applyCorrectionRules(
  review: ReviewOutput,
  rules: EscalationRules,
  stream: vscode.ChatResponseStream,
  chunkLabel: string = "",
): ReviewOutput {
  const decision = shouldEscalate(review, rules);
  if (!decision.shouldEscalate) { return review; }
  const issuesNote = decision.matchingIssues.length === 1
    ? "1 matching issue"
    : `${decision.matchingIssues.length} matching issues`;
  stream.markdown(
    `\n> ⛔ **escalate-on-rule fired${chunkLabel}**` +
    ` · ${issuesNote} · ${decision.reason}\n`,
  );
  return coerceToEscalation(review, decision) as ReviewOutput;
}

// ── Phase G.1.7: chunk filtering helpers ──────────────────────────────
//
// `filterDesignForChunkPaths` is the TS-side mirror of the Python
// helper in session/chunks.py. It trims `design.modules` to those whose
// `file` path is in `keep`. The non-chunked branch never calls this.
//
// `_chunkFilePathsCache` is keyed `<sessionId>/<chunkId>` and populated
// by the chunked execution branch in runPipeline before correction
// loops fire. The cache is process-local — the harness DB is the
// authoritative store; this avoids re-fetching the same chunk
// definition on every retry.

const _chunkFilePathsCache = new Map<string, ReadonlyArray<string>>();

/**
 * Phase G.1.6 — extract every `modules[].file` from a design.
 * Used as the pre-spawn dispatcher's chunkFilePaths input on the
 * non-chunked path; chunked path passes `chunk.file_paths` directly.
 * Returns `null` when the design isn't shaped right (no modules array)
 * so the caller can fall through to "no pre-spawns" cleanly.
 */
function filePathsFromDesign(
  design: Record<string, unknown> | null,
): ReadonlyArray<string> | null {
  if (!design || !Array.isArray(design.modules)) { return null; }
  const out: string[] = [];
  for (const m of design.modules as unknown[]) {
    if (!m || typeof m !== "object") { continue; }
    const f = (m as { file?: unknown }).file;
    if (typeof f === "string" && f.length > 0) { out.push(f); }
  }
  return out;
}

function filterDesignForChunkPaths(
  design: Record<string, unknown>,
  keep: ReadonlyArray<string>,
): Record<string, unknown> {
  if (!Array.isArray(design.modules) || keep.length === 0) { return design; }
  const keepSet = new Set(keep);
  const filtered = (design.modules as unknown[]).filter(m => {
    if (!m || typeof m !== "object") { return false; }
    const path = (m as { file?: unknown }).file;
    return typeof path === "string" && keepSet.has(path);
  });
  return { ...design, modules: filtered };
}

// ── Chat rendering helpers ──────────────────────────────────────────────────
// Each agent stage renders as:
//   ### ⏳ planner  (attempt 2/3 when retrying)
//   ◆ memory: `MEMORY.md` · ◇ policy: `Read·Grep·Glob`
//   ✓ **planner** — 3.1s — 5-step plan, schema ✓

function emitStageStart(
  stream: vscode.ChatResponseStream,
  agentName: string,
  attempt: number,
  maxAttempts: number,
  tags: StageTags,
): void {
  const emoji = attempt > 1 ? "↻" : "⏳";
  const attemptStr = attempt > 1 ? `  *(attempt ${attempt}/${maxAttempts})*` : "";
  stream.markdown(`\n### ${emoji} ${agentName}${attemptStr}\n`);
  const tagLine = renderTags(tags);
  if (tagLine) stream.markdown(tagLine + "\n\n");
}

function emitStageComplete(
  stream: vscode.ChatResponseStream,
  agentName: string,
  durationMs: number,
  summary: string,
): void {
  stream.markdown(`\n✓ **${agentName}** — ${fmtSeconds(durationMs)} — ${summary}\n`);
}

/**
 * Render the stage's structured output as an indented markdown block.
 *
 * Copilot Chat does NOT render raw <details>/<summary> HTML as a
 * collapsible — the tags appear as literal text. We use a blockquote
 * instead so the structured fields render cleanly without HTML.
 */
function emitStageOutputDetails(
  stream: vscode.ChatResponseStream,
  stage: string,
  output: unknown,
): void {
  if (typeof output !== "object" || output === null) return;
  const body = formatStageOutput(stage, output as Record<string, unknown>);
  if (!body) return;
  // Prefix every line with "> " so the entire block renders as one
  // continuous blockquote. Empty lines need "> " too or the quote breaks.
  const quoted = body.split("\n").map(l => `> ${l}`).join("\n");
  stream.markdown(`\n${quoted}\n`);
}

/**
 * Emit a clickable anchor pointing at the materialised stage MD file
 * (`.harness/sessions/<sessionId>/<stage>[.attemptN].md`). The file is
 * written by `materializeStageOutput`; this is the chat-side
 * affordance to open it without leaving the panel.
 *
 * No-ops when the file isn't on disk yet (e.g. a stage that didn't
 * have a renderer) — `stream.anchor` would point at a 404.
 */
function emitStageArtifactAnchor(
  stream: vscode.ChatResponseStream,
  workspaceRoot: string,
  sessionId: string,
  stage: string,
  attempt: number,
): void {
  if (!_STAGE_RENDERER[stage]) { return; }  // no MD produced for this stage
  const suffix = attempt > 1 ? `.attempt${attempt}` : "";
  const fileName = `${stage}${suffix}.md`;
  const absPath = path.join(workspaceRoot, ".harness", "sessions", sessionId, fileName);
  if (!fs.existsSync(absPath)) { return; }
  const uri = vscode.Uri.file(absPath);
  try {
    stream.anchor(uri, `View ${fileName}`);
  } catch {
    // stream.anchor throws on older VS Code; the file is still on
    // disk for the user to open manually via the Tasks tree.
  }
}

function formatStageOutput(stage: string, o: Record<string, unknown>): string {
  const lines: string[] = [];
  switch (stage) {
    case "plan": {
      if (typeof o.summary === "string") lines.push(`**Summary:** ${o.summary}`, "");
      const tasks = Array.isArray(o.tasks) ? o.tasks : [];
      if (tasks.length) {
        lines.push("**Tasks:**");
        for (const t of tasks) {
          if (typeof t !== "object" || t === null) continue;
          const task = t as Record<string, unknown>;
          const id = task.id ?? "?";
          const desc = task.description ?? "";
          lines.push(`- \`${id}\` — ${desc}`);
        }
      }
      const skills = Array.isArray(o.required_skills) ? o.required_skills : [];
      if (skills.length) lines.push("", `**Required skills:** ${skills.map(s => `\`${s}\``).join(", ")}`);
      const confidence = typeof o.confidence === "string" ? o.confidence : "";
      if (confidence) lines.push("", `**Confidence:** ${confidence}`);
      return lines.join("\n");
    }
    case "design": {
      if (typeof o.summary === "string") lines.push(`**Summary:** ${o.summary}`, "");
      const modules = Array.isArray(o.modules) ? o.modules : [];
      if (modules.length) {
        lines.push("**Modules:**");
        for (const m of modules) {
          if (typeof m !== "object" || m === null) continue;
          const mod = m as Record<string, unknown>;
          lines.push(`- \`${mod.file ?? "?"}\` — ${mod.purpose ?? ""}`);
        }
      }
      const confidence = typeof o.confidence === "string" ? o.confidence : "";
      if (confidence) lines.push("", `**Confidence:** ${confidence}`);
      return lines.join("\n");
    }
    case "code": {
      if (typeof o.summary === "string") lines.push(`**Summary:** ${o.summary}`, "");
      const files = Array.isArray(o.files_modified) ? o.files_modified : [];
      if (files.length) {
        lines.push(`**Files modified:** ${files.map(f => `\`${f}\``).join(", ")}`);
      }
      const notes = typeof o.implementation_notes === "string" ? o.implementation_notes : "";
      if (notes.trim()) lines.push("", `**Notes:** ${notes.trim()}`);
      const confidence = typeof o.confidence === "string" ? o.confidence : "";
      if (confidence) lines.push("", `**Confidence:** ${confidence}`);
      return lines.join("\n");
    }
    case "review": {
      const status = typeof o.status === "string" ? o.status : "unknown";
      const icon = status === "pass" ? "✅" : status === "escalate" ? "🚨" : "⚠️";
      lines.push(`**Status:** ${icon} ${status}`);
      const issues = Array.isArray(o.issues) ? o.issues : [];
      if (issues.length) {
        lines.push("", "**Issues:**");
        for (const i of issues) {
          if (typeof i !== "object" || i === null) continue;
          const issue = i as Record<string, unknown>;
          const sev = issue.severity ?? "?";
          const desc = issue.description ?? "";
          lines.push(`- [${sev}] ${desc}`);
          if (typeof issue.fix_instruction === "string" && issue.fix_instruction) {
            lines.push(`  - Fix: ${issue.fix_instruction}`);
          }
        }
      }
      const reason = typeof o.escalate_reason === "string" ? o.escalate_reason : "";
      if (reason) lines.push("", `**Escalation reason:** ${reason}`);
      return lines.join("\n");
    }
    default:
      return "";
  }
}

// ── /code-review pipeline runner (Phase H.1) ─────────────────────────────────
//
// Bespoke runner for the /code-review pipeline. Parallel to the feature-dev
// body inside runPipeline (B′ from the design discussion). Reuses every
// shared helper (callHarness, readAgentContext, runAgentWithValidationRetry,
// spawnSubAgent, materializeStageOutput, emitStage*) but the orchestration
// is pipeline-specific: scope → findings → reviewer-aux fan-out → synthesis.
//
// Why not generic: feature-dev's runner has stage-specific transforms
// (filterDesignForChunkPaths, chunked code/review execution, materializeCoderFiles
// writing files to disk) that don't map onto code-review's flow. A generic
// yaml-interpreter would either re-encode all of that, or push it down into
// per-pipeline transform helpers — which is what runCodeReviewBody already
// IS, just inline instead of registry-indexed. With N=2 pipelines the
// inline form is more readable.

interface CodeReviewBodyOpts {
  client: McpClient;
  request: string;
  workspaceRoot: string;
  promptRoots: string[];
  stream: vscode.ChatResponseStream;
  token: vscode.CancellationToken;
  sessionId: string;
  onChange?: () => void;
  route: string;
}

async function runCodeReviewBody(opts: CodeReviewBodyOpts): Promise<PipelineResult> {
  const { client, request, workspaceRoot, promptRoots, stream, token, sessionId, onChange } = opts;
  const stageOutputs: Record<string, unknown> = {};

  // ── Stage 0: resolve input → diff ─────────────────────────────────────────
  // The slash command passes the raw "branch" or "#PR" string as request.
  // Resolve to a unified diff before any agent sees it.
  const resolved = resolveCodeReviewInput(request, workspaceRoot);
  if (isResolveError(resolved)) {
    stream.markdown(`\n**Error:** ${resolved.error}\n`);
    if (resolved.hint) { stream.markdown(`> ${resolved.hint}\n`); }
    return { success: false, sessionId, stages: stageOutputs, escalated: false };
  }
  if (resolved.empty) {
    stream.markdown(
      `\n**No diff** between \`${resolved.base}\` and \`${resolved.ref}\` — nothing to review.\n`,
    );
    return { success: true, sessionId, stages: stageOutputs, escalated: false };
  }
  stream.markdown(
    `\n📄 Resolved \`${resolved.ref}\` against \`${resolved.base}\` ` +
    `— ${resolved.diff.split("\n").length} diff lines\n`,
  );
  const diffText = resolved.diff;

  // ── Stage 1: scope ────────────────────────────────────────────────────────
  // scoper has no prior stage, so harness_read_stage's auto-injection chain
  // doesn't fire. Pre-load the pr-scope-detection skill manually and supply
  // the request (the diff) via the runner-constructed context.
  emitStageStart(stream, "scoper", 1, MAX_CODE_ATTEMPTS, STAGE_TAGS["scope"] ?? {});
  const scoperPrompt = loadAgentPrompt(promptRoots, "code-review", "scoper");
  const scoperCtx: Record<string, unknown> = {
    request: { diff: diffText, ref: resolved.ref, base: resolved.base },
  };
  try {
    const skill = (await callHarness(client, "harness_get_skill", {
      skill_id: "pr-scope-detection", agent_name: "scoper",
    })) as { content?: string; error?: string };
    if (skill && typeof skill.content === "string") {
      scoperCtx["injected_skills"] = { "pr-scope-detection": skill.content };
    }
  } catch {
    // soft-fail — agent prompt summarises the procedure inline
  }
  const scopeT0 = Date.now();
  const { output: scope, finalAttempt: scopeAttempt } = await runAgentWithValidationRetry(
    client, promptRoots, "scoper", scoperPrompt, scoperCtx, sessionId, "scope",
    1, workspaceRoot, stream, token, onChange,
  );
  stageOutputs["scope"] = scope;
  emitStageComplete(stream, "scoper", Date.now() - scopeT0, summarizeStageOutput("scope", scope));
  emitStageOutputDetails(stream, "scope", scope);
  materializeStageOutput(workspaceRoot, sessionId, "scope", scopeAttempt, scope);
  emitStageArtifactAnchor(stream, workspaceRoot, sessionId, "scope", scopeAttempt);

  if (token.isCancellationRequested) {
    return { success: false, sessionId, stages: stageOutputs, escalated: false };
  }

  // ── Stage 2: findings ─────────────────────────────────────────────────────
  // finder reads `scope` via harness_read_stage; the per-file-review skill
  // is auto-injected on that read by composer.injected_skill_ids. The
  // runner also passes the raw diff so the finder can quote evidence lines.
  emitStageStart(stream, "finder", 1, MAX_CODE_ATTEMPTS, STAGE_TAGS["findings"] ?? {});
  const finderPrompt = loadAgentPrompt(promptRoots, "code-review", "finder");
  const finderCtx = await readAgentContext(client, sessionId, "finder", ["scope"]);
  finderCtx["request"] = { diff: diffText, ref: resolved.ref, base: resolved.base };
  const findT0 = Date.now();
  const { output: findings, finalAttempt: findingsAttempt } =
    await runAgentWithValidationRetry(
      client, promptRoots, "finder", finderPrompt, finderCtx, sessionId, "findings",
      1, workspaceRoot, stream, token, onChange,
    );
  stageOutputs["findings"] = findings;
  emitStageComplete(stream, "finder", Date.now() - findT0, summarizeStageOutput("findings", findings));
  emitStageOutputDetails(stream, "findings", findings);
  materializeStageOutput(workspaceRoot, sessionId, "findings", findingsAttempt, findings);

  if (token.isCancellationRequested) {
    return { success: false, sessionId, stages: stageOutputs, escalated: false };
  }

  // ── Fan-out: reviewer-aux per high/medium-priority file ───────────────────
  // The scoper's `files` array is the source of truth for what to review.
  // The finder's `per_file_priorities` maps onto file paths to give each
  // reviewer-aux a focus hint. Fan-out is bounded by
  // DEFAULT_STAGE_SUBAGENT_BUDGET.synthesis so a 100-file PR doesn't burn
  // the LM.
  const scopeFiles = _list((scope as Record<string, unknown> | null)?.["files"]);
  const focuses = _list(
    (findings as Record<string, unknown> | null)?.["per_file_priorities"],
  );
  const focusMap = new Map<string, string>();
  for (const f of focuses) {
    const r = _obj(f);
    const p = _str(r["path"], "");
    const focus = _str(r["ask_reviewer_aux_to_focus_on"], "");
    if (p && focus) { focusMap.set(p, focus); }
  }

  const filesToReview = scopeFiles
    .map(f => _obj(f))
    .filter(f => {
      const pri = _str(f["priority"], "");
      return pri === "high" || pri === "medium";
    });

  type AuxOutput = {
    role: "reviewer-aux"; file: string;
    summary: string | null; ok: boolean; reason?: string;
  };
  const subAgentOutputs: AuxOutput[] = [];

  if (filesToReview.length > 0) {
    const fanoutBudget = new StageSpawnBudget(
      sessionId, "synthesis", 1, defaultBudgetFor("synthesis"),
    );
    stream.markdown(
      `\n🔍 **Reviewer-aux fan-out:** spawning up to ${fanoutBudget.limit} ` +
      `for ${filesToReview.length} high/medium-priority file(s)\n`,
    );
    for (const f of filesToReview) {
      if (token.isCancellationRequested) { break; }
      if (fanoutBudget.exhausted) {
        stream.markdown(
          `\n  ⚠️ fan-out budget (${fanoutBudget.limit}) exhausted, ` +
          `${filesToReview.length - subAgentOutputs.length} file(s) not reviewed\n`,
        );
        break;
      }
      const filePath = _str(f["path"], "");
      if (!filePath) { continue; }
      const priority = _str(f["priority"], "");
      const focus = focusMap.get(filePath) ?? "general per-file review";
      const fileDiff = extractFileDiff(diffText, filePath);
      const brief = [
        `Review the file '${filePath}' (priority: ${priority}).`,
        `Focus: ${focus}.`,
        `Apply the per-file-review skill checklist.`,
        ``,
        `File diff:`,
        '```diff',
        fileDiff || "(no diff section found for this file in the input)",
        '```',
      ].join("\n");
      try {
        const result = await spawnSubAgent({
          client,
          parentSessionId: sessionId,
          parentAgentName: "synthesizer",
          budget: fanoutBudget,
          role: "reviewer-aux",
          brief,
          roots: promptRoots,
          log: logLine,
          token,
        });
        subAgentOutputs.push({
          role: "reviewer-aux",
          file: filePath,
          summary: result.summary,
          ok: result.ok,
          reason: result.reason,
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        logLine(`reviewer-aux fan-out for ${filePath} failed: ${msg}`);
        subAgentOutputs.push({
          role: "reviewer-aux",
          file: filePath,
          summary: null,
          ok: false,
          reason: msg,
        });
      }
    }
    const okCount = subAgentOutputs.filter(o => o.ok).length;
    stream.markdown(
      `\n  ✓ fan-out: ${okCount}/${subAgentOutputs.length} reviewer-aux returned ok\n`,
    );
  } else {
    stream.markdown(
      `\n🔍 **Reviewer-aux fan-out:** no high/medium priority files to spawn\n`,
    );
  }

  if (token.isCancellationRequested) {
    return { success: false, sessionId, stages: stageOutputs, escalated: false };
  }

  // ── Stage 3: synthesis ────────────────────────────────────────────────────
  // The synthesizer reads `findings` (evaluator firewall — see
  // _STAGE_PERMISSIONS["synthesizer"] = {"findings"}). The reviewer-aux
  // outputs reach it via the runner-augmented sub_agent_outputs field
  // since stage permissions don't permit reading scope.
  emitStageStart(stream, "synthesizer", 1, MAX_CODE_ATTEMPTS, STAGE_TAGS["synthesis"] ?? {});
  const synthPrompt = loadAgentPrompt(promptRoots, "code-review", "synthesizer");
  const synthCtx = await readAgentContext(client, sessionId, "synthesizer", ["findings"]);
  synthCtx["sub_agent_outputs"] = subAgentOutputs;
  const synthT0 = Date.now();
  const { output: synthesis, finalAttempt: synthAttempt } =
    await runAgentWithValidationRetry(
      client, promptRoots, "synthesizer", synthPrompt, synthCtx, sessionId, "synthesis",
      1, workspaceRoot, stream, token, onChange,
    );
  stageOutputs["synthesis"] = synthesis;
  emitStageComplete(stream, "synthesizer", Date.now() - synthT0, summarizeStageOutput("synthesis", synthesis));
  emitStageOutputDetails(stream, "synthesis", synthesis);
  materializeStageOutput(workspaceRoot, sessionId, "synthesis", synthAttempt, synthesis);
  emitStageArtifactAnchor(stream, workspaceRoot, sessionId, "synthesis", synthAttempt);

  const synthData = _obj(synthesis);
  const status = _str(synthData["status"], "pass");
  const escalated = status === "escalate";
  return {
    success: !escalated,
    sessionId,
    stages: stageOutputs,
    escalated,
    escalation: escalated ? _str(synthData["summary"], "synthesis escalated") : undefined,
  };
}

// ── Main entry point ──────────────────────────────────────────────────────────

export async function runPipeline(
  client: McpClient,
  request: string,
  workspaceRoot: string,
  promptRoots: string[],
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  pipelineMeta?: { route: string; pipelineName: string; level: number },
  onChange?: () => void,
): Promise<PipelineResult> {
  // Per-agent model selection happens inside runAgentLM via
  // selectModelForAgent — each stage runs against its declared `model:`
  // frontmatter (gpt-4o, gpt-4o-mini, etc). No global pre-selection.
  logger().show(true);

  // ── Session setup ────────────────────────────────────────────────────────────
  // A slash-command invocation with an explicit request is always a new session.
  // Resume is the job of /continue (runStep without a request) — folding crash
  // recovery into runPipeline silently inherited stale stages from the prior
  // run and skipped planner/designer with "already complete".
  const meta = pipelineMeta ?? { route: "/feature-dev", pipelineName: "feature-dev", level: 2 };
  // G.3: pass pipeline_name so harness_new_session opens a pipeline_runs
  // row tagged with the right pipeline. Defaults to feature-dev on the
  // harness side, but be explicit here so future routes (code-review,
  // refactor, etc.) appear correctly in stats aggregates.
  const session = (await callHarness(client, "harness_new_session", {
    request, pipeline_name: meta.pipelineName,
  })) as { session_id: string };
  const sessionId = session.session_id;

  const stageOutputs: Record<string, unknown> = {};
  const pipelineT0 = Date.now();
  // G.3: chunkCount is 0 unless the chunked branch fires (then = chunks.length).
  // `correctionAttempts` is derived from stage_outputs by the harness at
  // finalize time, so the runner doesn't track it.
  let chunkCount = 0;

  // Pipeline header — one line so the user knows what's running.
  stream.markdown(
    `🎛 **${meta.route}** — ${meta.pipelineName} · level ${meta.level} · session \`${sessionId}\`\n`,
  );
  // Jump-to-sidebar button so users can watch the live run in the Tasks view.
  stream.button({ command: "copilot-harness.showTasks", title: "$(checklist) Show Tasks" });

  // Notify the Tasks view so the new session appears under "Active session".
  onChange?.();

  // G.3: wrap the body in an IIFE so every existing `return X` short-
  // circuits to the outer finalize call below. chunkCount is captured
  // by closure and set inside the chunked branch before its return.
  const result: PipelineResult = await (async (): Promise<PipelineResult> => {

  // Phase H.1 — pipeline dispatcher. The /code-review pipeline runs a
  // bespoke 3-stage flow (scope → findings → synthesis) with reviewer-aux
  // fan-out at synthesis. B′ from the design discussion: a parallel runner
  // function rather than a generic yaml-interpreter, until N>=3 pipelines
  // gives evidence for what the right abstraction looks like.
  if (meta.pipelineName === "code-review") {
    return runCodeReviewBody({
      client, request, workspaceRoot, promptRoots,
      stream, token, sessionId, onChange, route: meta.route,
    });
  }

  // ── Run planner → designer → coder → reviewer ────────────────────────────────
  //
  // Indexed while-loop (instead of for-of) so the Phase G.1.5 review gate
  // can request a stage retry without advancing to the next agent. The
  // gate fires after planner/designer/coder write their output; the
  // reviewer is gated by its own correction loop, not the user-facing
  // gate.

  for (let agentIdx = 0; agentIdx < AGENT_PIPELINE.length; agentIdx++) {
    const agent = AGENT_PIPELINE[agentIdx];
    if (token.isCancellationRequested) { break; }

    const statusData = (await callHarness(
      client, "harness_get_status", { session_id: sessionId },
    )) as SessionStatus;

    if (statusData.stages[agent.writeStage]?.status === "complete") {
      stream.markdown(`\n✓ **${agent.name}** — already complete (skipped)\n`);
      continue;
    }

    // ── Phase G.1.7 chunked execution ────────────────────────────────────────
    //
    // When the design has multiple plan-task groups, run the coder + reviewer
    // once per chunk so each LM round-trip stays under the output cap.
    // Triggered ONLY at the coder iteration; chunks were computed from the
    // already-written design. If chunks come back empty, fall through to the
    // existing single-shot flow.
    if (agent.name === "coder") {
      const chunksRaw = await callHarness(client, "harness_compute_chunks", {
        session_id: sessionId,
      });
      const chunkList = (chunksRaw as { chunks?: ChunkInfo[] }).chunks ?? [];
      if (chunkList.length > 0) {
        // G.3: stash chunk count for the finalize call below.
        chunkCount = chunkList.length;
        return await runChunkedCodeAndReview({
          client, workspaceRoot, promptRoots, meta,
          sessionId, chunks: chunkList, stageOutputs,
          stream, token, onChange, pipelineT0,
        });
      }
    }

    const context = await readAgentContext(client, sessionId, agent.name, agent.readStages);
    if (agent.name === "planner") {
      context["request"] = request;
    }

    // Inject the workspace file tree into planner/designer context so they
    // ground module paths in the actual project layout instead of falling
    // back to placeholders like "path/to/test_file.py" that the coder then
    // dutifully creates on disk.
    if (agent.name === "planner" || agent.name === "designer") {
      context["workspace_tree"] = readWorkspaceTree(workspaceRoot);
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

    const attempt = statusData.stages[agent.writeStage]?.attempt ?? 1;

    // ── Phase G.1.6: heuristic pre-spawns (coder / reviewer only) ────────────
    let stageContext = context;
    if (agent.name === "coder" || agent.name === "reviewer") {
      const designForDispatcher =
        agent.name === "coder"
          ? ((context["design"] as Record<string, unknown> | undefined) ?? null)
          : null;  // reviewer firewall — no design access
      const filePaths = filePathsFromDesign(designForDispatcher) ?? [];
      const stageBudget = new StageSpawnBudget(
        sessionId, agent.writeStage, attempt, defaultBudgetFor(agent.writeStage),
      );
      stageContext = await preSpawnAndSplice({
        client,
        workspaceRoot,
        parentSessionId: sessionId,
        parentAgentName: agent.name,
        budget: stageBudget,
        stage: agent.name,
        chunkFilePaths: filePaths,
        chunkId: null,
        design: designForDispatcher,
        baseContext: context,
        roots: promptRoots,
        log: logLine,
        token,
        stream,
      });
    }

    emitStageStart(stream, agent.name, attempt, MAX_CODE_ATTEMPTS, STAGE_TAGS[agent.writeStage] ?? {});
    onChange?.();
    const stageT0 = Date.now();
    const { output: agentOutput, finalAttempt } = await runAgentWithValidationRetry(
      client, promptRoots, agent.name, loadAgentPrompt(promptRoots, meta.pipelineName, agent.name),
      stageContext, sessionId, agent.writeStage, attempt, workspaceRoot, stream, token, onChange,
    );
    stageOutputs[agent.writeStage] = agentOutput;
    materializeStageOutput(workspaceRoot, sessionId, agent.writeStage, finalAttempt, agentOutput);
    if (agent.name === "coder") {
      materializeCoderFiles(workspaceRoot, agentOutput, stream);
    }
    emitStageComplete(stream, agent.name, Date.now() - stageT0, summarizeStageOutput(agent.writeStage, agentOutput));
    emitStageOutputDetails(stream, agent.writeStage, agentOutput);
    emitStageArtifactAnchor(stream, workspaceRoot, sessionId, agent.writeStage, finalAttempt);
    onChange?.();

    // ── Phase G.1.5 review gate (planner / designer / coder only) ────────────
    //
    // Reviewer is gated by its own correction loop below, not the user
    // gate. For the others, persist a pause row + render the four-button
    // gate (or auto-approve through if the user setting / per-run flag
    // says so). On `retry`: increment the attempt and re-run THIS stage.
    // On `abort`: surface as escalation.
    if (agent.name !== "reviewer") {
      const gateOutcome: StageGateOutcome = await runStageReviewGate({
        client,
        stream,
        sessionId,
        pipelineName: meta.pipelineName,
        stage: agent.writeStage,
        attempt: finalAttempt,
        elapsedMs: Date.now() - stageT0,
        log: logLine,
      });

      if (gateOutcome.kind === "aborted") {
        stream.markdown("\n\n_Pipeline aborted at stage gate._\n");
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: "User aborted at stage review gate.",
        };
      }
      if (gateOutcome.kind === "retry") {
        // Bump attempt with the optional hint (passed straight through to
        // harness_increment_attempt; surfaces in the next read context as
        // `user_hint`). Re-run THIS agent by holding agentIdx steady.
        await callHarness(client, "harness_increment_attempt", {
          session_id: sessionId,
          stage: agent.writeStage,
          user_hint: gateOutcome.userHint ?? null,
        });
        const hintMsg = gateOutcome.userHint
          ? ` with hint: ${gateOutcome.userHint}`
          : "";
        stream.markdown(`\n↻ retrying **${agent.name}**${hintMsg}\n`);
        agentIdx -= 1;  // counter the loop's `i++`
        continue;
      }
      // approved | auto_approved → fall through to next agent.
    }

    // ── Correction loop (after reviewer) ─────────────────────────────────────
    if (agent.name === "reviewer") {
      // G.2: load escalate-on-rules and apply to the initial review.
      const correctionRules = await fetchCorrectionRules(client, meta.pipelineName);
      const review = applyCorrectionRules(agentOutput as ReviewOutput, correctionRules, stream);
      stageOutputs["review"] = review;

      if (review.status === "pass") { continue; }

      if (review.status === "escalate") {
        return {
          success: false, sessionId, stages: stageOutputs, escalated: true,
          escalation: review.escalate_reason ?? "Reviewer escalated.",
        };
      }

      const currentAttempt = statusData.stages["code"]?.attempt ?? 1;
      const finalReview = await runCorrectionLoop(
        client, sessionId, workspaceRoot, promptRoots, meta.pipelineName, review, currentAttempt, stream, token, onChange,
        undefined, correctionRules,
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

    // Pipeline footer — total elapsed. Caller (extension.ts) emits the
    // pass/fail summary line + action buttons.
    stream.markdown(`\n*total: ${fmtSeconds(Date.now() - pipelineT0)}*\n`);
    return { success: true, sessionId, stages: stageOutputs, escalated: false };
  })();

  // G.3: close out the pipeline_runs row regardless of which return
  // path the body took. fire-and-forget on the harness side.
  await emitFinalize(client, sessionId, result, chunkCount);
  return result;
}

// ── Single-step entry point ───────────────────────────────────────────────────

export async function runStep(
  client: McpClient,
  workspaceRoot: string,
  promptRoots: string[],
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  options: {
    request?: string;    // provided → create new session; omitted → resume active
    agentName?: string;  // run this specific agent instead of the next pending one
  },
  onChange?: () => void,
): Promise<StepResult> {
  // Per-agent model selection happens inside runAgentLM (see runPipeline).
  logger().show(true);

  // ── Session setup ─────────────────────────────────────────────────────────────

  const active = (await callHarness(client, "harness_get_active_session", {})) as ActiveSession;
  let sessionId: string;
  let sessionRequest: string;

  if (options.request) {
    const session = (await callHarness(client, "harness_new_session", { request: options.request })) as { session_id: string };
    sessionId = session.session_id;
    sessionRequest = options.request;
    stream.markdown(`🎛 **step** — session \`${sessionId}\`\n`);
  } else if (active.session_id) {
    sessionId = active.session_id;
    sessionRequest = active.request ?? "";
    stream.markdown(`↻ **resuming** — session \`${sessionId}\`\n`);
  } else {
    throw new Error("No active session. Start a new task with `@harness <your task description>`");
  }
  stream.button({ command: "copilot-harness.showTasks", title: "$(checklist) Show Tasks" });

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

  const context = await readAgentContext(client, sessionId, agentDef.name, agentDef.readStages);
  if (agentDef.name === "planner") {
    context["request"] = sessionRequest;
  }
  if (agentDef.name === "planner" || agentDef.name === "designer") {
    context["workspace_tree"] = readWorkspaceTree(workspaceRoot);
  }

  const stepAttempt = statusData.stages[agentDef.writeStage]?.attempt ?? 1;
  emitStageStart(stream, agentDef.name, stepAttempt, MAX_CODE_ATTEMPTS, STAGE_TAGS[agentDef.writeStage] ?? {});
  onChange?.();
  const stepT0 = Date.now();
  // runStep has no pipeline context — legacy bare keywords (planner/designer/...)
  // and `continue` always operate on feature-dev. The slash command path uses
  // runPipeline, which threads pipelineName explicitly.
  const stepPipeline = "feature-dev";
  const { output: agentOutput, finalAttempt: stepFinalAttempt } = await runAgentWithValidationRetry(
    client, promptRoots, agentDef.name, loadAgentPrompt(promptRoots, stepPipeline, agentDef.name),
    context, sessionId, agentDef.writeStage, stepAttempt, workspaceRoot, stream, token, onChange,
  );

  materializeStageOutput(workspaceRoot, sessionId, agentDef.writeStage, stepFinalAttempt, agentOutput);
  if (agentDef.name === "coder") {
    materializeCoderFiles(workspaceRoot, agentOutput, stream);
  }
  emitStageComplete(stream, agentDef.name, Date.now() - stepT0, summarizeStageOutput(agentDef.writeStage, agentOutput));
  emitStageOutputDetails(stream, agentDef.writeStage, agentOutput);
  emitStageArtifactAnchor(stream, workspaceRoot, sessionId, agentDef.writeStage, stepFinalAttempt);
  onChange?.();

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
        client, sessionId, workspaceRoot, promptRoots, stepPipeline, review, currentAttempt, stream, token, onChange,
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
  //
  // Start the search AFTER the agent we just ran. Walking from index 0 made
  // /coder suggest "Next: planner" when the user jumped ahead — the message
  // implied planner runs after coder, which is nonsense. The natural
  // successor (planner → designer → coder → reviewer) is what users expect.

  let nextAgent: string | null = null;
  let pipelineComplete = false;

  if (!escalated) {
    const updated = (await callHarness(
      client, "harness_get_status", { session_id: sessionId },
    )) as SessionStatus;
    const justRanIdx = AGENT_PIPELINE.findIndex(a => a.name === agentDef.name);
    for (let i = justRanIdx + 1; i < AGENT_PIPELINE.length; i++) {
      const agent = AGENT_PIPELINE[i];
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

// ── One-shot agent — single LLM call, no harness session, no stage validation
// ──────────────────────────────────────────────────────────────────────────────
//
// Used by slash commands with `action: agent`. Loads the agent prompt, sends
// the user's request as context, materialises any file_contents the agent
// produces. Aligns with the project's "don't invent agents speculatively"
// rule: low-frequency dev tools (e.g. /pipeline-builder) don't need a
// 4-stage pipeline.

export interface OneShotResult {
  success: boolean;
  agentName: string;
  output: unknown;
}

export async function runOneShotAgent(
  workspaceRoot: string,
  promptRoots: string[],
  agentName: string,
  request: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<OneShotResult> {
  // Model selection (frontmatter-honored) happens inside runAgentLM.
  const agentPrompt = loadAgentPrompt(promptRoots, "", agentName);
  const context: Record<string, unknown> = {
    request,
    workspace_tree: readWorkspaceTree(workspaceRoot),
  };

  stream.markdown(`\n### ⏳ ${agentName}\n*one-shot · no pipeline state*\n\n`);
  const t0 = Date.now();
  const output = await runAgentLM(promptRoots, agentName, agentPrompt, context, token);
  const elapsed = Date.now() - t0;

  // Agents that emit file_contents (like pipeline-builder) get materialised
  // to disk via the same helper the coder uses inside the pipeline.
  if (typeof output === "object" && output !== null && "file_contents" in output) {
    materializeCoderFiles(workspaceRoot, output, stream);
  }

  stream.markdown(`\n✓ **${agentName}** — ${fmtSeconds(elapsed)}\n`);
  emitStageOutputDetails(stream, "code", output);

  return { success: true, agentName, output };
}

// ── Pipeline-side sub-agent spawn helper (Phase G.1) ─────────────────────────
//
// `spawnSubAgent` lets a pipeline stage dispatch an explorer / investigator /
// reviewer-aux mid-execution. It enforces two harness invariants before the
// sub-agent runs:
//
//   1. Per-stage spawn budget — caps how many spawns a single stage attempt
//      may issue. Without it, a stuck stage can spam the same lookup until
//      it exhausts its context window. Tracked by `StageSpawnBudget`.
//
//   2. Depth ceiling — a sub-agent's own runner does not advertise
//      harness_spawn_subagent as an LM tool, so depth-2 is structurally
//      impossible today. The `maxDepth` config field is the forward-looking
//      knob: when sub-agents-spawn-sub-agents lands, this caps it.
//
// The escalation UX (ask the user when the budget is exhausted) is Phase
// G.1.5 work — for G.1 the helper throws a typed error and the caller
// decides what to do. Today no caller exists; feature-dev opts in during
// G.1.6, at which point the stage runner wires this to the chat gate.

export interface SpawnSubAgentOptions {
  client: McpClient;
  /** Parent context — the harness uses this to set parent_* on the spawn row. */
  parentSessionId: string;
  parentAgentName: string;
  /** Stage-attempt budget; mutated on a successful spawn. */
  budget: StageSpawnBudget;
  /** Role to spawn — one of the runners shipped in this PR. */
  role: SubagentRoleId;
  /** One-sentence task description. The sub-agent sees only this. */
  brief: string;
  /** Optional spawn-time tool narrowing (subset of the role's allow-list). */
  allowedTools?: readonly string[];
  /** Optional JSON schema for the structured payload. */
  outputSchema?: Record<string, unknown>;
  /** Workspace + extension roots passed to the runner. */
  roots: string[];
  log: (msg: string) => void;
  token: vscode.CancellationToken;
  toolInvocationToken?: vscode.ChatParticipantToolToken;
}

/**
 * Spawn a sub-agent on behalf of the current pipeline stage. Returns the
 * harness-verified summary (and structured echo, when present).
 *
 * Throws `SubagentBudgetExhausted` when the stage's budget is already
 * full. All other failure modes — LM unavailable, policy denial,
 * verification rejection — surface as a `RunSubagentResult` with
 * `ok=false`, so callers can degrade gracefully rather than crash.
 */
export async function spawnSubAgent(
  opts: SpawnSubAgentOptions,
): Promise<RunSubagentResult> {
  if (opts.budget.exhausted) {
    throw new SubagentBudgetExhausted(opts.budget);
  }
  opts.budget.consume();
  opts.log(
    `[pipeline] spawn ${opts.role} for ${opts.budget.stageKey} ` +
    `(budget ${opts.budget.used}/${opts.budget.limit})`,
  );
  return spawnAndRunSubagent({
    client: opts.client,
    parentSessionId: opts.parentSessionId,
    parentAgentName: opts.parentAgentName,
    role: opts.role,
    brief: opts.brief,
    allowedTools: opts.allowedTools,
    outputSchema: opts.outputSchema,
    roots: opts.roots,
    log: opts.log,
    token: opts.token,
    toolInvocationToken: opts.toolInvocationToken,
  });
}
