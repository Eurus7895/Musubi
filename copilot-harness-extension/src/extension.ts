/**
 * extension.ts — VS Code extension entry point for CopilotHarness Phase 2.
 *
 * Registers two commands:
 *   copilotHarness.runPipeline    — prompt for a request, run full pipeline
 *   copilotHarness.resumePipeline — resume the last interrupted session
 *
 * Both commands:
 *   1. Spawn the local MCP server (copilot-harness/server.py) via HarnessClient
 *   2. Drive the 5-agent pipeline (planner→designer→coder→reviewer) via pipeline.ts
 *   3. Report results to the CopilotHarness output channel
 */

import * as vscode from "vscode";
import { HarnessClient } from "./client";
import { runPipeline } from "./pipeline";

let outputChannel: vscode.OutputChannel | undefined;

export function activate(context: vscode.ExtensionContext): void {
  outputChannel = vscode.window.createOutputChannel("CopilotHarness");

  context.subscriptions.push(
    outputChannel,
    vscode.commands.registerCommand(
      "copilotHarness.runPipeline",
      () => commandRunPipeline(false),
    ),
    vscode.commands.registerCommand(
      "copilotHarness.resumePipeline",
      () => commandRunPipeline(true),
    ),
  );
}

export function deactivate(): void {
  outputChannel?.dispose();
}

// ── Command implementation ─────────────────────────────────────────────────────

async function commandRunPipeline(resume: boolean): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    vscode.window.showErrorMessage("CopilotHarness: No workspace folder open.");
    return;
  }

  let request: string;

  if (resume) {
    // For resume, use a placeholder — pipeline.ts will detect the active session
    // and use its stored request. We surface it to the user via the output channel.
    request = "__resume__";
  } else {
    const input = await vscode.window.showInputBox({
      title: "CopilotHarness: Run Pipeline",
      prompt: "Describe the feature or task",
      placeHolder:
        "e.g. Add a user login endpoint with JWT authentication",
      ignoreFocusOut: true,
      validateInput: (v) =>
        v.trim() ? undefined : "Request cannot be empty",
    });
    if (!input?.trim()) return;
    request = input.trim();
  }

  outputChannel!.clear();
  outputChannel!.show(true);
  outputChannel!.appendLine(
    `CopilotHarness Pipeline — ${new Date().toLocaleString()}`,
  );
  if (!resume) {
    outputChannel!.appendLine(`Request: ${request}`);
  }
  outputChannel!.appendLine("─".repeat(60));

  const client = new HarnessClient(workspaceRoot);

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "CopilotHarness",
      cancellable: true,
    },
    async (progress, token) => {
      try {
        progress.report({ message: "Starting MCP server…" });
        await client.start();
        outputChannel!.appendLine("[harness] MCP server started.");

        progress.report({ message: "Running pipeline…" });
        const result = await runPipeline(
          request,
          workspaceRoot,
          client,
          outputChannel!,
          token,
        );

        outputChannel!.appendLine("\n" + "─".repeat(60));

        if (token.isCancellationRequested) {
          outputChannel!.appendLine("Pipeline cancelled by user.");
          vscode.window.showWarningMessage("CopilotHarness: Pipeline cancelled.");
          return;
        }

        if (result.escalated) {
          outputChannel!.appendLine(`ESCALATED: ${result.escalation}`);
          const action = await vscode.window.showWarningMessage(
            `CopilotHarness: Pipeline escalated — ${result.escalation}`,
            "Show Output",
          );
          if (action === "Show Output") outputChannel!.show(true);
        } else {
          outputChannel!.appendLine(
            `Pipeline complete. Session: ${result.sessionId}`,
          );
          const action = await vscode.window.showInformationMessage(
            `CopilotHarness: Pipeline complete (session ${result.sessionId})`,
            "Show Output",
          );
          if (action === "Show Output") outputChannel!.show(true);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        outputChannel!.appendLine(`\nERROR: ${msg}`);
        const action = await vscode.window.showErrorMessage(
          `CopilotHarness error: ${msg}`,
          "Show Output",
        );
        if (action === "Show Output") outputChannel!.show(true);
      } finally {
        client.dispose();
        outputChannel!.appendLine("[harness] MCP server stopped.");
      }
    },
  );
}
