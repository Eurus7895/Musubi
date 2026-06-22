/**
 * loggerService.ts — single Output channel for the extension.
 *
 * Previously the extension created two channels ("CopilotHarness" in
 * extension.ts, "CopilotHarness Pipeline" in pipeline.ts). They emitted
 * the same kind of diagnostic, so users had to switch between them to
 * follow a session that crossed agent + pipeline boundaries.
 *
 * This module exposes a lazy singleton — first call creates the channel,
 * subsequent calls return the same instance. Both extension.ts and
 * pipeline.ts call `getLogger()` instead of creating their own.
 */

import * as vscode from "vscode";

let _channel: vscode.OutputChannel | undefined;

export function getLogger(): vscode.OutputChannel {
  if (!_channel) {
    _channel = vscode.window.createOutputChannel("CopilotHarness");
  }
  return _channel;
}

/**
 * Dispose the channel. Called from extension deactivate(). Test-only
 * callers can also use this to reset state between cases.
 */
export function disposeLogger(): void {
  _channel?.dispose();
  _channel = undefined;
}
