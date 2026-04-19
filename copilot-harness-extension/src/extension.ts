/**
 * extension.ts — VS Code extension entry point for CopilotHarness.
 *
 * On activation:
 *   1. Spawns the bundled harness server binary as a child process.
 *   2. Registers all harness_* tools via vscode.lm.registerTool() — no MCP
 *      server trust prompt, no user action needed.
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

// ── Extension lifecycle ───────────────────────────────────────────────────────

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const serverBin = resolveServerBinary(context.extensionPath);
  if (!serverBin) {
    vscode.window.showWarningMessage(
      "CopilotHarness: server binary not found in extension. Run `npm run package` to build.",
    );
    return;
  }

  let client: McpClient;
  try {
    client = await McpClient.create(serverBin, ["serve"], {
      HARNESS_ROOT: context.extensionPath,
    });
  } catch (err) {
    vscode.window.showErrorMessage(
      `CopilotHarness: failed to start server — ${err instanceof Error ? err.message : String(err)}`,
    );
    return;
  }

  context.subscriptions.push({ dispose: () => client.dispose() });

  // Register every harness_* tool with VS Code's language model API so
  // vscode.lm.invokeTool() works immediately — no MCP trust prompt needed.
  const tools = await client.listTools();
  for (const tool of tools) {
    const toolName = tool.name;
    const reg = vscode.lm.registerTool(toolName, {
      async invoke(
        options: vscode.LanguageModelToolInvocationOptions<Record<string, unknown>>,
        _token: vscode.CancellationToken,
      ): Promise<vscode.LanguageModelToolResult> {
        const text = await client.callTool(toolName, options.input ?? {});
        return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(text)]);
      },
    });
    context.subscriptions.push(reg);
  }

  const participant = vscode.chat.createChatParticipant("copilot-harness.harness", handler);
  participant.iconPath = new vscode.ThemeIcon("robot");
  context.subscriptions.push(participant);
}

export function deactivate(): void {}

// ── Helpers ───────────────────────────────────────────────────────────────────

function resolveServerBinary(extensionPath: string): string | null {
  const candidates = [
    path.join(extensionPath, "bin", "copilot-harness.exe"),
    path.join(extensionPath, "bin", "copilot-harness"),
  ];
  for (const c of candidates) {
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
      stream.markdown(`\n---\n**Pipeline complete.** Session: \`${result.sessionId}\``);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    stream.markdown(`\n**Error:** ${msg}`);
  }

  return {};
}
