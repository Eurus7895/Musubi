/**
 * extension.ts — VS Code extension entry point for CopilotHarness.
 *
 * Routing (zero LLM cost):
 *   /<pipeline-name> <task>   → pipeline (full guardrails + evaluator firewall)
 *   /<other-slash> [args]     → step / agent / status / help / orchestrator
 *   anything else             → orchestrator (persistent chat, sub-agents on demand)
 *
 * Legacy bare keywords (`continue`, `status`, `full`, `planner`, `designer`,
 * `coder`, `reviewer`) still route directly to their pipeline-step shortcuts
 * for muscle-memory; prefer the slash form.
 */

import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { McpClient } from "./mcpClient";
import { runOneShotAgent, runPipeline, runStep, StepResult } from "./pipeline";
import { registerOrchestratorTools, runOrchestrator } from "./runners/orchestrator";
import { registerGateCommands } from "./pipelineGateUi";
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
    client = await McpClient.create(
      serverBin,
      ["serve"],
      { HARNESS_ROOT: context.extensionPath },
      {
        // Pipe the server's stderr into our output channel so Python
        // tracebacks are visible during activation instead of being lost
        // to VS Code's main stderr.
        //
        // Filter out the MCP SDK's per-tool-call heartbeat ("Processing
        // request of type CallToolRequest") — every harness_* call emits
        // one, which drowns out actual errors. The orchestrator runner
        // already logs each tool call with name + duration, so the
        // heartbeat adds nothing diagnostic.
        onStderr: (line) => {
          if (/Processing request of type CallToolRequest/.test(line)) { return; }
          out.appendLine(`[server] ${line}`);
        },
      },
    );
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

  // Phase B.2 — register the orchestrator's vscode.lm tools (spawn / await /
  // list). Must run before any chat turn invokes runOrchestrator. Failures
  // are logged but non-fatal; the runner gracefully degrades to no-tool turns.
  context.subscriptions.push(registerOrchestratorTools((m) => out.appendLine(m)));

  // Phase G.1.5 — register the review-gate commands (resume + auto-approve
  // toggle). Buttons rendered in chat by pipelineGateUi point at these.
  context.subscriptions.push(registerGateCommands({
    client,
    log: (m) => out.appendLine(m),
  }));

  // ── Tasks sidebar view (v0.4.0) ───────────────────────────────────────────
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? context.extensionPath;
  const tasksProvider = new HarnessTasksProvider(client, workspaceRoot, (msg) => out.appendLine(msg));
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("copilotHarness.tasks", tasksProvider),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("copilot-harness.refreshTasks", () => tasksProvider.refresh()),
    vscode.commands.registerCommand("copilot-harness.clearActiveSession", async () => {
      // Confirmation isn't strictly necessary — the operation only resets
      // the pointer, all stage outputs and audit rows survive — but the
      // user types this from a TreeView click without a way to undo, so
      // a single Yes/No prompt is worth the friction.
      const choice = await vscode.window.showWarningMessage(
        "Clear the active pipeline session? Stage outputs and audit logs are preserved; only the pointer that crash-recovery reads is reset.",
        { modal: true },
        "Clear",
      );
      if (choice !== "Clear") { return; }
      try {
        await client.callTool("harness_clear_active_session", {});
        tasksProvider.refresh();
        vscode.window.showInformationMessage("CopilotHarness: active session cleared.");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`CopilotHarness: clear failed — ${msg}`);
      }
    }),
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

  const log = (msg: string): void => out.appendLine(msg);

  // Per-extension-activation salt threaded into resolveChatId so identical
  // first prompts in distinct chat panels mint distinct chat_ids. Closing
  // and reopening VS Code resets the salt — by design, "new VS Code session
  // = new chat state". Multi-turn within an activation stays stable because
  // the salt is unchanged.
  const sessionSalt = crypto.randomBytes(8).toString("hex");
  log(`Session salt: ${sessionSalt}`);
  log("[cache-probe] modelOptions.cache_control sent on every sendRequest. " +
      "If Copilot's proxy honours it, expect lm= timings on later turns of " +
      "the same chat to be noticeably faster than the first turn (cache hit).");

  const participant = vscode.chat.createChatParticipant(
    "copilot-harness.harness",
    (req, ctx, stream, token) => handler(
      req, ctx, stream, token, client, refreshTasks, context.extensionPath, log, sessionSalt,
    ),
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

type AgentName = "planner" | "designer" | "coder" | "reviewer";

type ParsedCommand =
  | { type: "slash";        name: string; args: string }
  | { type: "orchestrator"; prompt: string }
  | { type: "continue" }
  | { type: "agent";        agentName: AgentName; request?: string }
  | { type: "full";         request: string }
  | { type: "status" }
  | { type: "help" };

/**
 * Routing rules (Phase D — zero LLM cost):
 *   1. Starts with `/` → slash command (parse; unknown commands error).
 *   2. Legacy bare keywords (`continue`, `status`, `full`, agent names)
 *      keep working for muscle memory; slash commands preferred.
 *   3. Everything else → orchestrator (persistent chat, sub-agents on demand).
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

  // 2. Legacy bare keywords (deprecated — slash commands preferred).
  const spaceIdx = trimmed.indexOf(" ");
  const first = (spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx)).toLowerCase();
  const rest  = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();

  if (first === "continue") { return { type: "continue" }; }
  if (first === "status")   { return { type: "status" }; }
  if (first === "full")     { return { type: "full", request: rest || trimmed }; }
  if (AGENT_NAMES.has(first)) {
    return { type: "agent", agentName: first as AgentName, request: rest || undefined };
  }

  // 3. Default: orchestrator — persistent chat, spawns sub-agents on demand.
  return { type: "orchestrator", prompt: trimmed };
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

const USAGE_HEADER = "**CopilotHarness** — orchestrator + governed pipelines";

const USAGE_FOOTER = [
  "",
  "**Routing:**",
  "",
  "- `/<pipeline-name> <task>` — run a pipeline (e.g. `/feature-dev`). Full guardrails, evaluator firewall, and a review gate between stages.",
  "- `@harness <prompt>` — orchestrator. Persistent conversation, spawns sub-agents on demand.",
  "- Legacy bare keywords (`continue`, `status`, `full`, `planner`, `designer`, ",
  "  `coder`, `reviewer`) still work for muscle memory but are deprecated — use the slash form.",
  "",
  "**Review gate (between stages):**",
  "",
  "Pipelines pause after every non-reviewer stage and offer four buttons:",
  "**✓ Approve · ↻ Retry · ✕ Abort · ⚡ Run remaining without review**.",
  "Retry opens an optional one-line hint box that the next attempt sees.",
  "Per-pipeline auto-approve is persisted via the `copilotHarness.autoApprove.<pipeline>` setting (toggle from the chat button or VS Code settings).",
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

// ── Chat participant handler ──────────────────────────────────────────────────

async function handler(
  request: vscode.ChatRequest,
  context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  client: McpClient,
  refreshTasks: () => void,
  extensionPath: string,
  log: (msg: string) => void,
  sessionSalt: string,
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

      case "full": {
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

      case "orchestrator":
        await runOrchestrator({
          prompt: cmd.prompt,
          client,
          chatContext: context,
          stream,
          token,
          roots: slashRoots,
          log,
          sessionSalt,
          toolInvocationToken: request.toolInvocationToken,
        });
        break;

      case "slash":
        await runSlash(cmd.name, cmd.args, client, context, workspaceRoot, slashRoots, stream, token, refreshTasks, log, sessionSalt, request.toolInvocationToken);
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
  chatContext: vscode.ChatContext,
  workspaceRoot: string,
  slashRoots: string[],
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  refreshTasks: () => void,
  log: (msg: string) => void,
  sessionSalt: string,
  toolInvocationToken: vscode.ChatParticipantToolToken | undefined,
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
    case "agent": {
      if (!cmd.agent) {
        stream.markdown(`**Error:** \`/${cmd.name}\` is missing \`agent\` in its frontmatter.`);
        return;
      }
      if (!args) {
        stream.markdown(`**Error:** \`/${cmd.name}\` needs a request. Try \`@harness /${cmd.name} <your task>\`.`);
        return;
      }
      stream.markdown(`🎛 **/${cmd.name}** — one-shot \`${cmd.agent}\`\n`);
      await runOneShotAgent(workspaceRoot, slashRoots, cmd.agent, args, stream, token);
      return;
    }
    case "status":
      await showStatus(client, stream);
      return;
    case "help":
      stream.markdown(buildHelpMarkdown(slashRoots));
      return;
    case "orchestrator": {
      if (!args) {
        stream.markdown(`**Error:** \`/${cmd.name}\` needs a request. Try \`@harness /${cmd.name} <your task>\`.`);
        return;
      }
      await runOrchestrator({
        prompt: args,
        client,
        chatContext,
        stream,
        token,
        roots: slashRoots,
        log,
        sessionSalt,
        toolInvocationToken,
      });
      return;
    }
  }
}
