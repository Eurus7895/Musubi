/**
 * extension.ts — VS Code extension entry point for CopilotHarness.
 *
 * Commands (via @harness in Copilot Chat):
 *   @harness <task>          → new session, run planner, then pause for review
 *   @harness continue        → run next pending agent in active session
 *   @harness planner <task>  → run planner only (new session)
 *   @harness designer        → run designer on active session
 *   @harness coder           → run coder on active session
 *   @harness reviewer        → run reviewer on active session
 *   @harness full <task>     → run all agents automatically (no pausing)
 *   @harness status          → show active session stage table
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { McpClient } from "./mcpClient";
import { runPipeline, runStep, StepResult } from "./pipeline";
import { loadSlashCommand, listSlashCommands } from "./slashCommands";
import { HarnessTasksProvider } from "./tasksView";

let out: vscode.OutputChannel;

// ── Extension lifecycle ───────────────────────────────────────────────────────

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  out = vscode.window.createOutputChannel("CopilotHarness");
  context.subscriptions.push(out);
  out.show(true);
  out.appendLine("CopilotHarness v0.2.0 activating...");
  out.appendLine(`Extension path: ${context.extensionPath}`);

  const serverBin = resolveServerBinary(context.extensionPath);
  if (!serverBin) {
    const msg = "Server binary not found in extension bin/. Run `npm run package` to build.";
    out.appendLine(`ERROR: ${msg}`);
    out.show();
    vscode.window.showWarningMessage(`CopilotHarness: ${msg}`);
    return;
  }
  out.appendLine(`Server binary: ${serverBin}`);

  let client: McpClient;
  try {
    out.appendLine("Starting MCP server...");
    client = await McpClient.create(serverBin, ["serve"], {
      HARNESS_ROOT: context.extensionPath,
    });
    out.appendLine("MCP server started. Listing tools...");
    const tools = await client.listTools();
    out.appendLine(`Tools available (${tools.length}): ${tools.map(t => t.name).join(", ")}`);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    out.appendLine(`ERROR starting server: ${msg}`);
    out.show();
    vscode.window.showErrorMessage(`CopilotHarness: failed to start server — ${msg}`);
    return;
  }

  context.subscriptions.push({ dispose: () => client.dispose() });

  // ── Tasks sidebar view (v0.4.0) ───────────────────────────────────────────
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? context.extensionPath;
  const tasksProvider = new HarnessTasksProvider(client, workspaceRoot, (msg) => out.appendLine(msg));
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("copilotHarness.tasks", tasksProvider),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("copilot-harness.refreshTasks", () => tasksProvider.refresh()),
    vscode.commands.registerCommand(
      "copilot-harness.openSessionArtifact",
      async (sessionId: string, stage: string) => {
        const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!ws) return;
        // Prefer latest attempt file; fall back to base.
        const dir = path.join(ws, ".harness", "sessions", sessionId);
        let candidate = path.join(dir, `${stage}.md`);
        try {
          const files = fs.readdirSync(dir)
            .filter(f => new RegExp(`^${stage}(?:\\.attempt\\d+)?\\.md$`).test(f))
            .sort();
          if (files.length > 0) candidate = path.join(dir, files[files.length - 1]);
        } catch { /* use base candidate */ }
        try {
          const doc = await vscode.workspace.openTextDocument(candidate);
          await vscode.window.showTextDocument(doc, { preview: false });
        } catch {
          vscode.window.showWarningMessage(`CopilotHarness: cannot open ${candidate}`);
        }
      },
    ),
    vscode.commands.registerCommand("copilot-harness.showTasks", async () => {
      // Focus our view container so the Tasks tree becomes visible.
      // This is what the in-chat "Show Tasks" button routes to.
      await vscode.commands.executeCommand("workbench.view.extension.copilotHarness");
    }),
  );

  // Refreshing the tree is the only signal pipeline.ts sends out. Debounced
  // so rapid stage transitions don't thrash the TreeView.
  let refreshTimer: NodeJS.Timeout | undefined;
  const refreshTasks = (): void => {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => tasksProvider.refresh(), 150);
  };

  const participant = vscode.chat.createChatParticipant(
    "copilot-harness.harness",
    (req, ctx, stream, token) => handler(req, ctx, stream, token, client, refreshTasks, context.extensionPath),
  );
  // Use our own harness mark for the chat avatar rather than the generic
  // robot codicon. iconPath accepts a Uri; VS Code theming the SVG works
  // because harness.svg uses currentColor throughout.
  participant.iconPath = vscode.Uri.joinPath(
    context.extensionUri, "media", "icons", "harness.svg",
  );
  context.subscriptions.push(participant);

  // Initial refresh — if a session was interrupted, it shows up immediately.
  refreshTasks();

  out.appendLine("CopilotHarness ready. Use @harness in Copilot Chat.");
}

export function deactivate(): void {}

// ── Helpers ───────────────────────────────────────────────────────────────────

function resolveServerBinary(extensionPath: string): string | null {
  const candidates = [
    path.join(extensionPath, "bin", "copilot-harness.exe"),
    path.join(extensionPath, "bin", "copilot-harness"),
  ];
  for (const c of candidates) {
    out.appendLine(`Checking: ${c} — ${fs.existsSync(c) ? "found" : "not found"}`);
    if (fs.existsSync(c)) { return c; }
  }
  return null;
}

// ── Command parsing ───────────────────────────────────────────────────────────

const AGENT_NAMES = new Set(["planner", "designer", "coder", "reviewer"]);
const PIPELINE_FLAG = "--pipeline";

type AgentName = "planner" | "designer" | "coder" | "reviewer";

type ParsedCommand =
  | { type: "slash";    name: string; args: string }
  | { type: "direct";   prompt: string }
  | { type: "pipelineForced"; request: string }
  | { type: "continue" }
  | { type: "agent";    agentName: AgentName; request?: string }
  | { type: "full";     request: string }
  | { type: "status" }
  | { type: "help" };

function stripPipelineFlag(text: string): { stripped: string; hadFlag: boolean } {
  // Remove a standalone --pipeline token (space-delimited). Never strips a
  // substring of a longer word.
  const tokens = text.split(/\s+/).filter(t => t.length > 0);
  const out: string[] = [];
  let hadFlag = false;
  for (const tok of tokens) {
    if (tok === PIPELINE_FLAG) { hadFlag = true; continue; }
    out.push(tok);
  }
  return { stripped: out.join(" "), hadFlag };
}

/**
 * Routing rules (Week 3c — zero LLM cost):
 *   1. Starts with `/` → slash command (parse; unknown commands error).
 *   2. Contains `--pipeline` token → force pipeline mode on the remainder.
 *   3. Legacy bare keywords (`continue`, `status`, `full`, agent names)
 *      keep working for one release cycle.
 *   4. Everything else → direct mode (single Copilot call, no harness).
 */
function parseCommand(text: string): ParsedCommand {
  const trimmed = text.trim();
  if (!trimmed) { return { type: "help" }; }

  // 1. Slash commands.
  if (trimmed.startsWith("/")) {
    const body = trimmed.slice(1);
    const spaceIdx = body.indexOf(" ");
    const name = (spaceIdx === -1 ? body : body.slice(0, spaceIdx)).toLowerCase();
    const args = spaceIdx === -1 ? "" : body.slice(spaceIdx + 1).trim();
    if (!name) { return { type: "help" }; }
    return { type: "slash", name, args };
  }

  // 2. --pipeline flag forces pipeline mode.
  const { stripped, hadFlag } = stripPipelineFlag(trimmed);
  if (hadFlag) {
    return { type: "pipelineForced", request: stripped || trimmed };
  }

  // 3. Legacy bare keywords (deprecated — slash commands preferred).
  const spaceIdx = trimmed.indexOf(" ");
  const first = (spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx)).toLowerCase();
  const rest  = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();

  if (first === "continue") { return { type: "continue" }; }
  if (first === "status")   { return { type: "status" }; }
  if (first === "full")     { return { type: "full", request: rest || trimmed }; }
  if (AGENT_NAMES.has(first)) {
    return { type: "agent", agentName: first as AgentName, request: rest || undefined };
  }

  // 4. Default: direct mode — free-form question to Copilot, no harness.
  return { type: "direct", prompt: trimmed };
}

// ── Status display ────────────────────────────────────────────────────────────

async function showStatus(
  client: McpClient,
  stream: vscode.ChatResponseStream,
): Promise<void> {
  const activeRaw = await client.callTool("harness_get_active_session", {});
  let active: { session_id: string | null; request?: string };
  try { active = JSON.parse(activeRaw); } catch { active = { session_id: null }; }

  if (!active.session_id) {
    stream.markdown("No active session. Start with `@harness <task>`.");
    return;
  }

  const statusRaw = await client.callTool("harness_get_status", { session_id: active.session_id });
  let status: { stages: Record<string, { status: string; attempt: number }> };
  try { status = JSON.parse(statusRaw); } catch {
    stream.markdown("Could not retrieve session status.");
    return;
  }

  const STAGE_ORDER = ["plan", "design", "code", "review"];
  const STAGE_ICON: Record<string, string> = {
    complete: "✅", in_progress: "⏳", pending: "⬜",
  };

  let md = `**Session:** \`${active.session_id}\`\n\n`;
  if (active.request) { md += `**Request:** ${active.request}\n\n`; }
  md += "| Stage | Status | Attempt |\n|---|---|---|\n";

  for (const stage of STAGE_ORDER) {
    const info = status.stages?.[stage];
    if (info) {
      const icon = STAGE_ICON[info.status] ?? "❓";
      md += `| ${stage} | ${icon} ${info.status} | ${info.attempt} |\n`;
    } else {
      md += `| ${stage} | ⬜ pending | — |\n`;
    }
  }

  stream.markdown(md);
}

// ── Usage text ────────────────────────────────────────────────────────────────

const USAGE_HEADER = "**CopilotHarness** — direct mode + governed pipeline";

const USAGE_FOOTER = [
  "",
  "**Non-slash entry points:**",
  "",
  "- `@harness <question>` — direct mode (single Copilot call, no pipeline).",
  "- `@harness <task> --pipeline` — force pipeline mode on free-form input.",
  "- Bare keywords (`continue`, `status`, `full`, `planner`, `designer`, ",
  "  `coder`, `reviewer`) still work but are deprecated — use the slash form.",
  "",
  "Slash commands are defined in `.github/commands/`. Type `@harness /help` ",
  "any time to see the current list.",
].join("\n");

/** Build the /help body by listing every on-disk slash command. */
function buildHelpMarkdown(roots: string[]): string {
  const commands = listSlashCommands(roots).sort((a, b) => a.name.localeCompare(b.name));
  const rows: string[] = [
    USAGE_HEADER,
    "",
    "| Command | Action | Description |",
    "|---|---|---|",
  ];
  for (const cmd of commands) {
    const target =
      cmd.action === "pipeline" ? `pipeline \`${cmd.pipeline ?? "?"}\`` :
      cmd.action === "step"     ? `step \`${cmd.agent ?? "?"}\`` :
      cmd.action;
    rows.push(`| \`/${cmd.name}\` | ${target} | ${cmd.description || "—"} |`);
  }
  if (commands.length === 0) {
    rows.push("| _(no commands found)_ | — | check `.github/commands/` |");
  }
  rows.push("", USAGE_FOOTER);
  return rows.join("\n");
}

// ── Direct mode ───────────────────────────────────────────────────────────────

interface SkillCatalogEntry { skill_id: string; title: string; }
interface SkillCatalog { agent_name: string; skills: SkillCatalogEntry[]; }

interface MemoryContext {
  tier1_index?: string;
  tier2_available?: string[];
}

const DIRECT_AGENT_NAME = "direct";
const MAX_PULL_ROUNDS = 3;
const MAX_HISTORY_TURNS = 10;  // caps the prior-conversation context we replay

async function fetchDirectCatalog(client: McpClient): Promise<SkillCatalog> {
  try {
    const raw = await client.callTool("harness_list_skills", { agent_name: DIRECT_AGENT_NAME });
    return JSON.parse(raw) as SkillCatalog;
  } catch (err) {
    out.appendLine(`[direct] harness_list_skills failed: ${err instanceof Error ? err.message : String(err)}`);
    return { agent_name: DIRECT_AGENT_NAME, skills: [] };
  }
}

async function fetchMemoryContext(client: McpClient): Promise<MemoryContext> {
  try {
    const raw = await client.callTool("harness_get_memory_context", {});
    const parsed = JSON.parse(raw) as MemoryContext;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (err) {
    out.appendLine(`[direct] harness_get_memory_context failed: ${err instanceof Error ? err.message : String(err)}`);
    return {};
  }
}

/**
 * Replay prior user/assistant turns from this chat thread so follow-ups in
 * direct mode remember what was just discussed. Capped at `maxTurns` pairs
 * of (request, response) to keep the prompt bounded — we always keep the
 * MOST RECENT turns because those are the ones the user is following up on.
 *
 * VS Code's ChatContext.history is an ordered list of ChatRequestTurn and
 * ChatResponseTurn. We only keep turns addressed to our own participant
 * (copilot-harness.harness) so unrelated @copilot etc. don't leak in.
 */
function extractChatHistory(
  chatContext: vscode.ChatContext,
  maxTurns: number,
): vscode.LanguageModelChatMessage[] {
  const participantId = "copilot-harness.harness";
  const relevant: vscode.ChatContext["history"][number][] = [];
  for (const turn of chatContext.history) {
    // Duck-type the participant id — both turn kinds expose it.
    const tid = (turn as { participant?: string }).participant;
    if (tid && tid !== participantId) { continue; }
    relevant.push(turn);
  }

  // Keep the last 2*maxTurns entries (each turn = 1 request + 1 response).
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

/**
 * Week 4 Day 3 — Direct-mode pull-on-demand skills.
 * Follow-up — chat history + Tier 1 memory injection.
 *
 * Flow:
 *   1. Two MCP calls in parallel: harness_list_skills("direct") + harness_get_memory_context.
 *   2. Build a system prompt with the skill catalog and (if present) Tier 1 memory.
 *   3. Replay recent chat history from vscode.ChatContext so follow-ups remember.
 *   4. Ask the LLM to answer. If it needs a skill it emits a JSON fenced
 *      block {"action":"pull_skill","skill_id":"python"} as the FIRST
 *      line of its response, then stops.
 *   5. Extension fulfils the pull via harness_get_skill, appends the
 *      skill content to the conversation, loops up to MAX_PULL_ROUNDS.
 *   6. On any round with no pull marker the response streams to chat.
 *
 * Pipeline mode is unchanged — push-only, firewall intact. Direct mode
 * is the only caller with allowlist-union access and an evaluator-free
 * execution path.
 */
async function runDirect(
  prompt: string,
  client: McpClient,
  chatContext: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot", family: "gpt-4o" });
  if (!models.length) {
    stream.markdown("**Error:** No Copilot language model available.");
    return;
  }
  const model = models[0];

  // 1. Fetch catalog + memory concurrently — one round-trip of latency, not two.
  const [catalog, memory] = await Promise.all([
    fetchDirectCatalog(client),
    fetchMemoryContext(client),
  ]);

  const catalogLines = catalog.skills.length
    ? catalog.skills.map(s => `  - ${s.skill_id}: ${s.title}`).join("\n")
    : "  (none)";

  const memoryBlock = memory.tier1_index
    ? `Project memory (Tier 1 — always injected, do not restate verbatim):\n\n` +
      memory.tier1_index.trim() + "\n\n" +
      (memory.tier2_available && memory.tier2_available.length
        ? `Tier 2 entries available on demand (ask the user if you need them): ` +
          memory.tier2_available.join(", ") + "\n\n"
        : "")
    : "";

  const systemMsg =
    `You are answering a developer's question inside the CopilotHarness VS Code extension (direct mode — ` +
    `no pipeline, no evaluator).\n\n` +
    memoryBlock +
    `You may optionally pull one of the following skills for extra knowledge before answering:\n\n` +
    catalogLines + "\n\n" +
    `If — and only if — a skill would meaningfully improve your answer, your ENTIRE response for that turn ` +
    `must be exactly one JSON object on a single line:\n` +
    `{"action":"pull_skill","skill_id":"<id>"}\n\n` +
    `You may pull at most ${MAX_PULL_ROUNDS} skills across the whole answer. After every pull the ` +
    `extension will append the skill content to the conversation and call you again. When you are ready ` +
    `to answer, produce your final answer as normal markdown (no JSON, no fences around the whole reply). ` +
    `If no skill is needed, just answer directly.`;

  const history: vscode.LanguageModelChatMessage[] = [
    vscode.LanguageModelChatMessage.User(systemMsg),
    ...extractChatHistory(chatContext, MAX_HISTORY_TURNS),
    vscode.LanguageModelChatMessage.User(prompt),
  ];

  const pulled = new Set<string>();

  for (let round = 0; round <= MAX_PULL_ROUNDS; round++) {
    if (token.isCancellationRequested) { return; }

    const response = await model.sendRequest(history, {}, token);
    let text = "";
    for await (const chunk of response.text) { text += chunk; }

    const pull = parseSkillPullRequest(text);
    if (!pull || round === MAX_PULL_ROUNDS) {
      // Final answer or budget exhausted — stream whatever we have.
      stream.markdown(text);
      return;
    }

    if (pulled.has(pull.skill_id)) {
      // LLM re-requested the same skill — abort the loop and let it answer.
      history.push(vscode.LanguageModelChatMessage.User(
        `[harness] Skill '${pull.skill_id}' was already provided. Produce the final answer now.`,
      ));
      continue;
    }

    const allowed = catalog.skills.some(s => s.skill_id === pull.skill_id);
    if (!allowed) {
      history.push(vscode.LanguageModelChatMessage.User(
        `[harness] Skill '${pull.skill_id}' is not in the direct-mode catalog. ` +
        `Answer using the skills listed in the system message, or produce your final answer now.`,
      ));
      continue;
    }

    let skillContent = "";
    try {
      skillContent = await client.callTool("harness_get_skill", {
        skill_id: pull.skill_id, agent_name: DIRECT_AGENT_NAME,
      });
    } catch (err) {
      skillContent = `error loading skill: ${err instanceof Error ? err.message : String(err)}`;
    }

    pulled.add(pull.skill_id);
    stream.progress(`Loaded skill: ${pull.skill_id}`);

    history.push(vscode.LanguageModelChatMessage.Assistant(text));
    history.push(vscode.LanguageModelChatMessage.User(
      `[harness] Skill '${pull.skill_id}' content below. Use it to answer the original question.\n\n` +
      skillContent,
    ));
  }
}

/** Parse a single-line pull-skill JSON marker from the model's raw output. */
function parseSkillPullRequest(text: string): { skill_id: string } | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) { return null; }
  // The marker must be the ENTIRE response (or at most a single line) — we
  // deliberately accept only the strict form so a model casually mentioning
  // JSON in prose is not misread as a pull.
  const firstLine = trimmed.split("\n", 1)[0].trim();
  if (firstLine !== trimmed && !trimmed.endsWith("}")) { return null; }
  try {
    const obj = JSON.parse(trimmed) as { action?: string; skill_id?: string };
    if (obj.action === "pull_skill" && typeof obj.skill_id === "string" && obj.skill_id.length > 0) {
      return { skill_id: obj.skill_id };
    }
  } catch {
    return null;
  }
  return null;
}

// ── Chat participant handler ──────────────────────────────────────────────────

async function handler(
  request: vscode.ChatRequest,
  context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  client: McpClient,
  refreshTasks: () => void,
  extensionPath: string,
): Promise<vscode.ChatResult> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    stream.markdown("**Error:** No workspace folder open.");
    return {};
  }

  // Workspace first, extension bundle as fallback. The bundle ships the
  // canonical /help, /feature-dev, /planner... set; a workspace can shadow
  // any of them by dropping a same-named file into its own .github/commands/.
  const slashRoots = [workspaceRoot, extensionPath];

  const cmd = parseCommand(request.prompt);

  try {
    switch (cmd.type) {

      case "help":
        stream.markdown(buildHelpMarkdown(slashRoots));
        break;

      case "status":
        await showStatus(client, stream);
        break;

      case "full":
      case "pipelineForced": {
        const result = await runPipeline(
          client, cmd.request, workspaceRoot, slashRoots, stream, token,
          { route: "/feature-dev", pipelineName: "feature-dev", level: 2 },
          refreshTasks,
        );
        emitPipelineSummary(stream, result);
        break;
      }

      case "continue": {
        const result = await runStep(client, workspaceRoot, slashRoots, stream, token, {}, refreshTasks);
        emitStepMarker(stream, result);
        break;
      }

      case "agent": {
        const result = await runStep(client, workspaceRoot, slashRoots, stream, token, {
          agentName: cmd.agentName,
          request: cmd.request,
        }, refreshTasks);
        emitStepMarker(stream, result);
        break;
      }

      case "direct":
        await runDirect(cmd.prompt, client, context, stream, token);
        break;

      case "slash":
        await runSlash(cmd.name, cmd.args, client, workspaceRoot, slashRoots, stream, token, refreshTasks);
        break;
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    out.appendLine(`Pipeline error: ${msg}`);
    stream.markdown(`\n**Error:** ${msg}`);
  } finally {
    // Always refresh so escalations and errors show up in the Tasks view.
    refreshTasks();
  }

  return {};
}

// ── Chat-side summaries ──────────────────────────────────────────────────────
// pipeline.ts emits per-stage rich markdown. These footers close the session
// with a pass/fail line + an action button to open the materialised plan.

function emitPipelineSummary(
  stream: vscode.ChatResponseStream,
  result: { escalated: boolean; escalation?: string; sessionId: string },
): void {
  stream.markdown("\n---\n");
  if (result.escalated) {
    stream.markdown(`⚠️ **Escalated:** ${result.escalation ?? "reviewer escalated"}\n`);
  } else {
    stream.markdown(`✅ **Pipeline complete.** Session: \`${result.sessionId}\`\n`);
  }
  emitPlanAnchor(stream, result.sessionId);
}

function emitStepMarker(stream: vscode.ChatResponseStream, result: StepResult): void {
  if (result.escalated) {
    stream.markdown(`\n⚠️ **Escalated:** ${result.escalation ?? "reviewer escalated"}\n`);
    return;
  }
  if (result.pipelineComplete) {
    stream.markdown(`\n✅ **Pipeline complete.** Session: \`${result.sessionId}\`\n`);
    emitPlanAnchor(stream, result.sessionId);
    return;
  }
  if (result.nextAgent) {
    stream.markdown(
      `\n**Next:** \`${result.nextAgent}\` — type \`@harness continue\` or \`@harness /${result.nextAgent}\`.\n`,
    );
  }
}

function emitPlanAnchor(stream: vscode.ChatResponseStream, sessionId: string): void {
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!ws) return;
  const planUri = vscode.Uri.joinPath(ws, ".harness", "sessions", sessionId, "plan.md");
  try {
    stream.anchor(planUri, "View plan.md");
  } catch {
    // stream.anchor can throw on older VS Code; swallow silently — the
    // file is still on disk for the user to open manually.
  }
}

// ── Slash command dispatch ────────────────────────────────────────────────────

async function runSlash(
  name: string,
  args: string,
  client: McpClient,
  workspaceRoot: string,
  slashRoots: string[],
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  refreshTasks: () => void,
): Promise<void> {
  const cmd = loadSlashCommand(slashRoots, name);
  if (!cmd) {
    const available = listSlashCommands(slashRoots).map(c => `/${c.name}`).join(", ");
    stream.markdown(
      `**Unknown command:** \`/${name}\`` +
      (available ? `\n\nAvailable: ${available}` : ""),
    );
    return;
  }

  switch (cmd.action) {
    case "pipeline": {
      if (!args) {
        stream.markdown(`**Error:** \`/${cmd.name}\` needs a request. Try \`@harness /${cmd.name} <your task>\`.`);
        return;
      }
      const pipelineName = cmd.pipeline ?? cmd.name;
      const result = await runPipeline(
        client, args, workspaceRoot, slashRoots, stream, token,
        { route: `/${cmd.name}`, pipelineName, level: 2 },
        refreshTasks,
      );
      emitPipelineSummary(stream, result);
      return;
    }
    case "step": {
      if (!cmd.agent || !AGENT_NAMES.has(cmd.agent)) {
        stream.markdown(`**Error:** \`/${cmd.name}\` is missing a valid \`agent\` in its frontmatter.`);
        return;
      }
      const result = await runStep(client, workspaceRoot, slashRoots, stream, token, {
        agentName: cmd.agent as AgentName,
        request: args || undefined,
      }, refreshTasks);
      emitStepMarker(stream, result);
      return;
    }
    case "continue": {
      const result = await runStep(client, workspaceRoot, slashRoots, stream, token, {}, refreshTasks);
      emitStepMarker(stream, result);
      return;
    }
    case "status":
      await showStatus(client, stream);
      return;
    case "help":
      stream.markdown(buildHelpMarkdown(slashRoots));
      return;
  }
}
