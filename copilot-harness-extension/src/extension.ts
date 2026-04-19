/**
 * extension.ts — VS Code extension entry point for CopilotHarness.
 *
 * Registers the @harness chat participant. Usage in Copilot Chat:
 *   @harness add a login endpoint with JWT authentication
 *
 * The participant drives the full 5-agent pipeline automatically:
 *   planner → designer → coder → reviewer (+ correction loop)
 * All harness_* tools are called via vscode.lm.invokeTool() on the
 * single MCP server VS Code already manages via .vscode/mcp.json.
 */

import * as vscode from "vscode";
import { runPipeline } from "./pipeline";

export function activate(context: vscode.ExtensionContext): void {
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
