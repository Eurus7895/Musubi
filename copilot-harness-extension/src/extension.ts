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

const USAGE = `
**CopilotHarness** — direct mode + governed pipeline

| Command | Action |
|---|---|
| \`@harness <question>\` | Direct mode — single Copilot call, no pipeline |
| \`@harness /feature-dev <task>\` | Full governed pipeline (plan → design → code → review) |
| \`@harness /continue\` | Run next pending agent in active session |
| \`@harness /planner <task>\` | Run planner only |
| \`@harness /designer\` | Run designer on active session |
| \`@harness /coder\` | Run coder on active session |
| \`@harness /reviewer\` | Run reviewer on active session |
| \`@harness /status\` | Show active session progress |
| \`@harness <task> --pipeline\` | Force pipeline mode for non-slash input |

Bare keywords (\`continue\`, \`status\`, \`full\`, agent names) still work but are
deprecated — use the slash form instead. Slash commands are defined in
\`.github/commands/\`.
`.trim();

// ── Direct mode ───────────────────────────────────────────────────────────────

async function runDirect(
  prompt: string,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  const models = await vscode.lm.selectChatModels({ vendor: "copilot", family: "gpt-4o" });
  if (!models.length) {
    stream.markdown("**Error:** No Copilot language model available.");
    return;
  }
  const messages = [vscode.LanguageModelChatMessage.User(prompt)];
  const response = await models[0].sendRequest(messages, {}, token);
  for await (const chunk of response.text) {
    stream.markdown(chunk);
  }
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
        stream.markdown(USAGE);
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
        await runDirect(cmd.prompt, stream, token);
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
  }
}
