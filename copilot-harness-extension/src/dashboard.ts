/**
 * dashboard.ts — Harness Dashboard webview panel.
 *
 * Renders the pipeline card (HTML mockup) in a VS Code webview. The extension
 * posts typed events as the pipeline runs; the webview mutates the DOM.
 *
 * Invariants:
 *   - Events are queued before the webview posts "ready", then flushed in order.
 *   - The panel is lazily created on first post — never spawned for direct
 *     mode unless the user opens it manually.
 *   - Cancel button aborts the in-flight pipeline via a CancellationTokenSource
 *     linked to the current chat request.
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

// ── Event protocol (extension → webview) ───────────────────────────────────

export interface StageTags {
  skill?: string;
  memory?: string;
  firewall?: string;
  schema?: string;
  policy?: string;
}

export type DashboardEvent =
  | {
      type: "session_start";
      sessionId: string;
      request: string;
      route: string;
      pipelineName: string;
      level: number;
      agents: Array<{ name: string; stage: string; tags: StageTags }>;
    }
  | { type: "stage_start"; sessionId: string; stage: string; attempt: number; maxAttempts: number; tags: StageTags }
  | { type: "stage_progress"; sessionId: string; stage: string; detail: string }
  | { type: "stage_complete"; sessionId: string; stage: string; durationMs: number; summary: string }
  | { type: "stage_failed"; sessionId: string; stage: string; durationMs: number; reason: string }
  | {
      type: "correction_retry";
      sessionId: string;
      stage: "code";
      attempt: number;
      maxAttempts: number;
      reviewerVerdict: "fail" | "escalate" | "wrong_plan";
      issuesCount: number;
      fixInstructions: string;
    }
  | { type: "pipeline_complete"; sessionId: string; success: boolean; escalated: boolean; escalation?: string }
  | {
      type: "hook_event";
      sessionId: string;
      hook: "PreToolUse" | "PostToolUse" | "SessionStart";
      status: "ok" | "fail";
      auditRows?: number;
    }
  | { type: "tick"; sessionId: string; elapsedMs: number }
  | { type: "direct_start"; prompt: string; skillCatalog: Array<{ id: string; title: string }>; memory?: unknown }
  | { type: "direct_pull_skill"; skillId: string; title: string }
  | { type: "direct_complete" }
  | { type: "reset" };

// ── Event protocol (webview → extension) ───────────────────────────────────

type DashboardAction =
  | { type: "ready" }
  | { type: "action_cancel"; sessionId?: string }
  | { type: "action_status" }
  | { type: "action_view_file"; relPath: string }
  | { type: "action_run_slash"; name: string; args?: string }
  | { type: "action_open_chat" };

// ── Listener types ─────────────────────────────────────────────────────────

export type DashboardActionHandler = (action: DashboardAction) => void | Promise<void>;

// ── Class ──────────────────────────────────────────────────────────────────

export class HarnessDashboard implements vscode.Disposable {
  private panel: vscode.WebviewPanel | undefined;
  private ready = false;
  private pending: DashboardEvent[] = [];
  private replayLog: DashboardEvent[] = [];
  private tickInterval: NodeJS.Timeout | undefined;
  private sessionStartedAt: number | undefined;
  private currentSessionId: string | undefined;
  private actionListeners: DashboardActionHandler[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: (msg: string) => void,
  ) {}

  dispose(): void {
    this.stopTick();
    this.panel?.dispose();
    this.panel = undefined;
  }

  onAction(handler: DashboardActionHandler): vscode.Disposable {
    this.actionListeners.push(handler);
    return {
      dispose: () => {
        const i = this.actionListeners.indexOf(handler);
        if (i >= 0) this.actionListeners.splice(i, 1);
      },
    };
  }

  /** Open or focus the panel (user-initiated via command palette / chat button). */
  show(): void {
    this.ensurePanel();
    this.panel?.reveal(vscode.ViewColumn.Beside, true);
  }

  /** Post an event. Creates the panel on-demand for session_start. */
  post(event: DashboardEvent): void {
    // Keep replay log for panel reopen (cap ~500 events per session to bound memory).
    this.replayLog.push(event);
    if (this.replayLog.length > 500) this.replayLog.splice(0, this.replayLog.length - 500);

    if (event.type === "session_start") {
      this.currentSessionId = event.sessionId;
      this.sessionStartedAt = Date.now();
      this.startTick();
      this.ensurePanel();
      this.panel?.reveal(vscode.ViewColumn.Beside, true);
    }
    if (event.type === "pipeline_complete") {
      this.stopTick();
    }
    if (event.type === "direct_start") {
      // Don't auto-open for direct mode — only if the panel is already visible.
    }

    if (!this.panel || !this.ready) {
      this.pending.push(event);
      return;
    }
    this.panel.webview.postMessage(event);
  }

  // ── Internals ────────────────────────────────────────────────────────────

  private ensurePanel(): void {
    if (this.panel) return;
    this.panel = vscode.window.createWebviewPanel(
      "copilotHarness.dashboard",
      "CopilotHarness Dashboard",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
      },
    );
    this.panel.iconPath = new vscode.ThemeIcon("dashboard");
    this.panel.webview.html = this.renderHtml(this.panel.webview);

    this.panel.onDidDispose(() => {
      this.panel = undefined;
      this.ready = false;
      this.pending = [];
      this.stopTick();
    });

    this.panel.webview.onDidReceiveMessage((raw) => {
      const action = raw as DashboardAction;
      if (action?.type === "ready") {
        this.ready = true;
        // Replay any prior session events so a freshly opened panel redraws.
        for (const ev of this.replayLog) this.panel!.webview.postMessage(ev);
        // Flush events buffered before ready (usually subset of replayLog).
        this.pending = [];
        return;
      }
      for (const h of this.actionListeners) {
        Promise.resolve(h(action)).catch((err) => this.log(`dashboard handler error: ${err}`));
      }
    });
  }

  private startTick(): void {
    this.stopTick();
    this.tickInterval = setInterval(() => {
      if (!this.currentSessionId || !this.sessionStartedAt) return;
      const elapsedMs = Date.now() - this.sessionStartedAt;
      // Tick events are NOT added to replayLog — they'd grow unbounded.
      if (this.panel && this.ready) {
        this.panel.webview.postMessage({
          type: "tick",
          sessionId: this.currentSessionId,
          elapsedMs,
        } as DashboardEvent);
      }
    }, 1000);
  }
  private stopTick(): void {
    if (this.tickInterval) {
      clearInterval(this.tickInterval);
      this.tickInterval = undefined;
    }
  }

  private renderHtml(webview: vscode.Webview): string {
    const mediaDir = vscode.Uri.joinPath(this.extensionUri, "media", "dashboard");
    const htmlPath = path.join(mediaDir.fsPath, "index.html");
    const raw = fs.readFileSync(htmlPath, "utf-8");

    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(mediaDir, "style.css"));
    const appUri = webview.asWebviewUri(vscode.Uri.joinPath(mediaDir, "app.js"));
    const nonce = randomNonce();

    // CSP — restrict to our nonce'd script, our styles, and our images.
    const csp =
      `default-src 'none'; ` +
      `img-src ${webview.cspSource} data:; ` +
      `style-src ${webview.cspSource} 'unsafe-inline'; ` +
      `script-src 'nonce-${nonce}'; ` +
      `font-src ${webview.cspSource};`;

    return raw
      .replace("__STYLE_URI__", styleUri.toString())
      .replace("__APP_URI__", appUri.toString())
      .replace(/__NONCE__/g, nonce)
      .replace("__CSP__", csp);
  }
}

function randomNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 32; i++) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}
