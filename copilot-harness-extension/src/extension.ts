/**
 * extension.ts — VS Code extension entry point for CopilotHarness.
 *
 * On activation:
 *   1. Registers the MCP server in the user-level mcp.json so harness_* tools
 *      are available in every VS Code workspace — no per-project setup needed.
 *   2. Registers the @harness chat participant.
 *
 * Usage in Copilot Chat:
 *   @harness add a login endpoint with JWT authentication
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { runPipeline } from "./pipeline";

// ── MCP server auto-registration ──────────────────────────────────────────────

function getUserMcpPath(): string {
  if (process.platform === "win32") {
    return path.join(
      process.env["APPDATA"] ?? os.homedir(),
      "Code", "User", "mcp.json",
    );
  }
  if (process.platform === "darwin") {
    return path.join(
      os.homedir(),
      "Library", "Application Support", "Code", "User", "mcp.json",
    );
  }
  return path.join(os.homedir(), ".config", "Code", "User", "mcp.json");
}

function registerMcpServer(extensionPath: string): void {
  const launcherJs = path.join(extensionPath, "bin", "launch.js");

  // contributes.mcpServers in package.json handles registration automatically
  // when "node" is on PATH. As a fallback, write user-level mcp.json using the
  // absolute path to the current Node.js binary so the server still starts even
  // if "node" is not on the system PATH.
  if (!fs.existsSync(launcherJs)) {
    return; // Dev mode without built assets — nothing to do.
  }

  const mcpPath = getUserMcpPath();
  let config: Record<string, unknown> = {};
  if (fs.existsSync(mcpPath)) {
    try {
      config = JSON.parse(fs.readFileSync(mcpPath, "utf-8")) as Record<string, unknown>;
    } catch { /* corrupt file — start fresh */ }
  }

  const servers = (config["servers"] as Record<string, unknown> | undefined) ?? {};
  const existing = servers["copilot-harness"] as Record<string, unknown> | undefined;

  // Already registered with the same launcher — nothing to do.
  if (existing?.["args"] instanceof Array && existing["args"][0] === launcherJs) {
    return;
  }

  // Use process.execPath (absolute path to VS Code's Node.js) so the server
  // starts even when "node" is not on PATH. This is the fallback path;
  // contributes.mcpServers is the primary registration mechanism.
  servers["copilot-harness"] = {
    type: "stdio",
    command: process.execPath,
    args: [launcherJs, "serve"],
    env: { HARNESS_ROOT: extensionPath },
  };
  config["servers"] = servers;

  fs.mkdirSync(path.dirname(mcpPath), { recursive: true });
  fs.writeFileSync(mcpPath, JSON.stringify(config, null, 4));
}

// ── Extension lifecycle ───────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext): void {
  registerMcpServer(context.extensionPath);

  const participant = vscode.chat.createChatParticipant(
    "copilot-harness.harness",
    handler,
  );
  participant.iconPath = new vscode.ThemeIcon("robot");
  context.subscriptions.push(participant);
}

export function deactivate(): void {}

// ── Chat participant handler ───────────────────────────────────────────────────

async function handler(
  request: vscode.ChatRequest,
  _context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<vscode.ChatResult> {
  const userRequest = request.prompt.trim();

  if (!userRequest) {
    stream.markdown(
      "Describe the feature or task to implement.\n\n" +
      "**Usage:** `@harness add a login endpoint with JWT authentication`",
    );
    return {};
  }

  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    stream.markdown("**Error:** No workspace folder open.");
    return {};
  }

  try {
    const result = await runPipeline(
      userRequest,
      workspaceRoot,
      stream,
      token,
      request.toolInvocationToken,
    );

    if (result.escalated) {
      stream.markdown(`\n---\n**Escalated:** ${result.escalation}`);
    } else {
      stream.markdown(
        `\n---\n**Pipeline complete.** Session: \`${result.sessionId}\``,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    stream.markdown(`\n**Error:** ${msg}`);
  }

  return {};
}
