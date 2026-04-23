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
import { loadSlashCommand, listSlashCommands, SlashCommand } from "./slashCommands";

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

  const participant = vscode.chat.createChatParticipant(
    "copilot-harness.harness",
    (req, ctx, stream, token) => handler(req, ctx, stream, token, client),
  );
  participant.iconPath = new vscode.ThemeIcon("robot");
  context.subscriptions.push(participant);

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

// ── Output rendering ──────────────────────────────────────────────────────────

function renderAgentOutput(agentName: string, output: unknown, stream: vscode.ChatResponseStream): void {
  if (!output || typeof output !== "object") { return; }
  const o = output as Record<string, unknown>;

  switch (agentName) {
    case "planner": {
      if (o.summary) { stream.markdown(`**Summary:** ${o.summary}\n\n`); }
      const tasks = Array.isArray(o.tasks) ? o.tasks : [];
      if (tasks.length) {
        stream.markdown("**Tasks:**\n");
        for (const t of tasks) {
          if (typeof t === "object" && t !== null) {
            const task = t as Record<string, unknown>;
            stream.markdown(`- \`${task.id ?? "?"}\` — ${task.description ?? ""}\n`);
          }
        }
      }
      break;
    }
    case "designer": {
      if (o.summary) { stream.markdown(`**Summary:** ${o.summary}\n\n`); }
      const modules = Array.isArray(o.modules) ? o.modules : [];
      if (modules.length) {
        stream.markdown("**Modules:**\n");
        for (const m of modules) {
          if (typeof m === "object" && m !== null) {
            const mod = m as Record<string, unknown>;
            stream.markdown(`- \`${mod.file ?? "?"}\` — ${mod.purpose ?? ""}\n`);
          }
        }
      }
      break;
    }
    case "coder": {
      if (o.summary) { stream.markdown(`**Summary:** ${o.summary}\n\n`); }
      const files = Array.isArray(o.files_modified) ? o.files_modified : [];
      if (files.length) {
        stream.markdown(`**Files modified:** ${files.map(f => `\`${f}\``).join(", ")}\n`);
      }
      break;
    }
    case "reviewer": {
      const status = o.status as string;
      const icon = status === "pass" ? "✅" : status === "escalate" ? "🚨" : "⚠️";
      stream.markdown(`**Review:** ${icon} ${status.toUpperCase()}\n\n`);
      const issues = Array.isArray(o.issues) ? o.issues : [];
      if (issues.length) {
        stream.markdown("**Issues:**\n");
        for (const issue of issues) {
          if (typeof issue === "object" && issue !== null) {
            const i = issue as Record<string, unknown>;
            stream.markdown(`- [${i.severity ?? "?"}] ${i.description ?? ""}\n`);
          }
        }
      }
      break;
    }
  }
}

function renderStepResult(result: StepResult, stream: vscode.ChatResponseStream): void {
  stream.markdown(`\n✓ **${result.completedAgent}** complete\n\n`);
  renderAgentOutput(result.completedAgent, result.output, stream);

  stream.markdown("\n---\n");

  if (result.escalated) {
    stream.markdown(`⚠️ **Escalated:** ${result.escalation}`);
  } else if (result.pipelineComplete) {
    stream.markdown(`✅ **Pipeline complete.** Session: \`${result.sessionId}\``);
  } else if (result.nextAgent) {
    stream.markdown(
      `**Next:** \`${result.nextAgent}\` is ready.\n\n` +
      `Type \`@harness continue\` or \`@harness ${result.nextAgent}\` to proceed.`,
    );
  }
}

// ── Status display ────────────────────────────────────────────────────────────

async function showStatus(client: McpClient, stream: vscode.ChatResponseStream): Promise<void> {
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
function buildHelpMarkdown(workspaceRoot: string): string {
  const commands = listSlashCommands(workspaceRoot).sort((a, b) => a.name.localeCompare(b.name));
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

const DIRECT_AGENT_NAME = "direct";
const MAX_PULL_ROUNDS = 3;

/**
 * Week 4 Day 3 — Direct-mode pull-on-demand skills.
 *
 * Flow:
 *   1. One MCP round-trip: harness_list_skills("direct") → catalog
 *   2. Inject the catalog into the system prompt so the LLM can name
 *      skill_ids it wants to load.
 *   3. Ask the LLM to answer. If it needs a skill it emits a JSON fenced
 *      block {"action":"pull_skill","skill_id":"python"} as the FIRST
 *      line of its response, then stops.
 *   4. Extension fulfils the pull via harness_get_skill, appends the
 *      skill content to the conversation, loops up to MAX_PULL_ROUNDS.
 *   5. On any round with no pull marker the response streams to chat.
 *
 * Pipeline mode is unchanged — push-only, firewall intact. Direct mode
 * is the only caller with allowlist-union access and an evaluator-free
 * execution path.
 */
async function runDirect(
  prompt: string,
  client: McpClient,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot", family: "gpt-4o" });
  if (!models.length) {
    stream.markdown("**Error:** No Copilot language model available.");
    return;
  }
  const model = models[0];

  // 1. One-shot catalog fetch.
  let catalog: SkillCatalog = { agent_name: DIRECT_AGENT_NAME, skills: [] };
  try {
    const raw = await client.callTool("harness_list_skills", { agent_name: DIRECT_AGENT_NAME });
    catalog = JSON.parse(raw) as SkillCatalog;
  } catch (err) {
    out.appendLine(`[direct] harness_list_skills failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  const catalogLines = catalog.skills.length
    ? catalog.skills.map(s => `  - ${s.skill_id}: ${s.title}`).join("\n")
    : "  (none)";

  const systemMsg =
    `You are answering a developer's question inside the CopilotHarness VS Code extension (direct mode — ` +
    `no pipeline, no evaluator).\n\n` +
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
  _context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  client: McpClient,
): Promise<vscode.ChatResult> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    stream.markdown("**Error:** No workspace folder open.");
    return {};
  }

  const cmd = parseCommand(request.prompt);

  try {
    switch (cmd.type) {

      case "help":
        stream.markdown(buildHelpMarkdown(workspaceRoot));
        break;

      case "status":
        await showStatus(client, stream);
        break;

      case "full":
      case "pipelineForced": {
        const result = await runPipeline(client, cmd.request, workspaceRoot, stream, token);
        stream.markdown("\n---\n");
        if (result.escalated) {
          stream.markdown(`⚠️ **Escalated:** ${result.escalation}`);
        } else {
          stream.markdown(`✅ **Pipeline complete.** Session: \`${result.sessionId}\``);
        }
        break;
      }

      case "continue": {
        const result = await runStep(client, workspaceRoot, stream, token, {});
        renderStepResult(result, stream);
        break;
      }

      case "agent": {
        const result = await runStep(client, workspaceRoot, stream, token, {
          agentName: cmd.agentName,
          request: cmd.request,
        });
        renderStepResult(result, stream);
        break;
      }

      case "direct":
        await runDirect(cmd.prompt, client, stream, token);
        break;

      case "slash":
        await runSlash(cmd.name, cmd.args, client, workspaceRoot, stream, token);
        break;
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    out.appendLine(`Pipeline error: ${msg}`);
    stream.markdown(`\n**Error:** ${msg}`);
  }

  return {};
}

// ── Slash command dispatch ────────────────────────────────────────────────────

async function runSlash(
  name: string,
  args: string,
  client: McpClient,
  workspaceRoot: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  const cmd = loadSlashCommand(workspaceRoot, name);
  if (!cmd) {
    const available = listSlashCommands(workspaceRoot).map(c => `/${c.name}`).join(", ");
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
      const result = await runPipeline(client, args, workspaceRoot, stream, token);
      stream.markdown("\n---\n");
      if (result.escalated) {
        stream.markdown(`⚠️ **Escalated:** ${result.escalation}`);
      } else {
        stream.markdown(`✅ **Pipeline complete.** Session: \`${result.sessionId}\``);
      }
      return;
    }
    case "step": {
      if (!cmd.agent || !AGENT_NAMES.has(cmd.agent)) {
        stream.markdown(`**Error:** \`/${cmd.name}\` is missing a valid \`agent\` in its frontmatter.`);
        return;
      }
      const result = await runStep(client, workspaceRoot, stream, token, {
        agentName: cmd.agent as AgentName,
        request: args || undefined,
      });
      renderStepResult(result, stream);
      return;
    }
    case "continue": {
      const result = await runStep(client, workspaceRoot, stream, token, {});
      renderStepResult(result, stream);
      return;
    }
    case "status":
      await showStatus(client, stream);
      return;
    case "help":
      stream.markdown(buildHelpMarkdown(workspaceRoot));
      return;
  }
}
