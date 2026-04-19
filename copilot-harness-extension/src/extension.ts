/**
 * extension.ts — VS Code extension entry point for CopilotHarness.
 *
 * On activation:
 *   1. Spawns the bundled harness server binary as a child process.
 *   2. Calls harness tools directly via McpClient (no vscode.lm.invokeTool).
 *   3. Registers the @harness chat participant.
 *
 * Usage in Copilot Chat:
 *   @harness add a login endpoint with JWT authentication
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { McpClient } from "./mcpClient";
import { runPipeline } from "./pipeline";

let out: vscode.OutputChannel;

// ── Extension lifecycle ───────────────────────────────────────────────────────

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  out = vscode.window.createOutputChannel("CopilotHarness");
  context.subscriptions.push(out);
  out.appendLine("CopilotHarness activating...");
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

// ── Chat participant handler ──────────────────────────────────────────────────

async function handler(
  request: vscode.ChatRequest,
  _context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
  client: McpClient,
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
    const result = await runPipeline(client, userRequest, workspaceRoot, stream, token);

    if (result.escalated) {
      stream.markdown(`\n---\n**Escalated:** ${result.escalation}`);
    } else {
      stream.markdown(`\n---\n**Pipeline complete.** Session: \`${result.sessionId}\``);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    out.appendLine(`Pipeline error: ${msg}`);
    stream.markdown(`\n**Error:** ${msg}`);
  }

  return {};
}
