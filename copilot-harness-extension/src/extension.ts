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
  const binName = process.platform === "win32"
    ? "copilot-harness.exe"
    : "copilot-harness";
  const serverBin = path.join(extensionPath, "bin", binName);

  // Dev mode: binary not built yet — skip automatic registration.
  if (!fs.existsSync(serverBin)) {
    return;
  }

  const mcpPath = getUserMcpPath();

  let config: Record<string, unknown> = {};
  if (fs.existsSync(mcpPath)) {
    try {
      config = JSON.parse(fs.readFileSync(mcpPath, "utf-8")) as Record<string, unknown>;
    } catch {
      // Corrupt file — start fresh.
    }
  }

  const servers = (config["servers"] as Record<string, unknown> | undefined) ?? {};
  const existing = servers["copilot-harness"] as Record<string, unknown> | undefined;

  // Already registered with the correct binary path — nothing to do.
  if (existing?.["command"] === serverBin) {
    return;
  }

  servers["copilot-harness"] = {
    type: "stdio",
    command: serverBin,
    args: ["serve"],
    env: {
      // Tells skill_loader.py and context_builder.py where to find
      // .github/skills/ and .github/agents/ bundled inside the extension.
      HARNESS_ROOT: extensionPath,
    },
  };
  config["servers"] = servers;

  fs.mkdirSync(path.dirname(mcpPath), { recursive: true });
  fs.writeFileSync(mcpPath, JSON.stringify(config, null, 4));

  // MCP servers are loaded at VS Code startup — prompt once to reload.
  vscode.window
    .showInformationMessage(
      "CopilotHarness: MCP server registered. Reload window to activate harness_* tools.",
      "Reload Window",
    )
    .then((choice) => {
      if (choice === "Reload Window") {
        vscode.commands.executeCommand("workbench.action.reloadWindow");
      }
    });
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
