/**
 * dashboard.ts — Harness Dashboard sidebar webview view.
 *
 * Implements vscode.WebviewViewProvider so VS Code hosts the dashboard as a
 * docked view in the auxiliary sidebar (alongside CHAT and CLAUDE CODE in
 * the user's layout). Users can drag the view to any container.
 *
 * The extension posts typed events (session_start / stage_* / correction_retry
 * / pipeline_complete / tick / direct_*) as the pipeline runs; the webview
 * mutates the DOM in response. No LLM calls — the dashboard is a pure
 * renderer of pipeline instrumentation.
 *
 * In-chat rendering lives in parallel in pipeline.ts — both surfaces receive
 * the same information; the user can watch whichever they prefer.
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
  | { type: "tick"; sessionId: string; elapsedMs: number }
  | { type: "direct_start"; prompt: string }
  | { type: "direct_pull_skill"; skillId: string; title: string }
  | { type: "reset" };

type DashboardAction =
  | { type: "ready" }
  | { type: "action_cancel"; sessionId?: string }
  | { type: "action_status" }
  | { type: "action_view_file"; relPath: string };

export type DashboardActionHandler = (action: DashboardAction) => void | Promise<void>;

// ── WebviewViewProvider ────────────────────────────────────────────────────

export class HarnessDashboard implements vscode.WebviewViewProvider, vscode.Disposable {
  public static readonly viewType = "copilotHarness.dashboard";

  private view: vscode.WebviewView | undefined;
  private ready = false;

  // Kept so a freshly-resolved webview can redraw the in-flight session.
  // Capped to keep memory bounded across long-lived sessions.
  private replayLog: DashboardEvent[] = [];
  private static readonly REPLAY_CAP = 500;

  private tickInterval: NodeJS.Timeout | undefined;
  private currentSessionId: string | undefined;
  private sessionStartedAt: number | undefined;

  private actionListeners: DashboardActionHandler[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: (msg: string) => void,
  ) {}

  dispose(): void {
    this.stopTick();
    this.view = undefined;
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

  /** Focus the dashboard view (equivalent to the user clicking the tab). */
  async show(): Promise<void> {
    try {
      await vscode.commands.executeCommand(`${HarnessDashboard.viewType}.focus`);
    } catch (err) {
      this.log(`dashboard.show failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** Post an event to the webview. Buffers in replay log if the view isn't
   * ready yet — it'll be flushed when the user first reveals the view. */
  post(event: DashboardEvent): void {
    // Book-keeping: track the in-flight session for the tick driver.
    if (event.type === "session_start") {
      this.currentSessionId = event.sessionId;
      this.sessionStartedAt = Date.now();
      this.startTick();
    }
    if (event.type === "pipeline_complete") {
      this.stopTick();
    }

    // Add to replay log (cap size so long sessions don't leak).
    this.replayLog.push(event);
    if (this.replayLog.length > HarnessDashboard.REPLAY_CAP) {
      this.replayLog.splice(0, this.replayLog.length - HarnessDashboard.REPLAY_CAP);
    }

    if (this.view && this.ready) {
      this.view.webview.postMessage(event);
    }
  }

  // ── WebviewViewProvider implementation ───────────────────────────────────

  resolveWebviewView(
    view: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    this.view = view;
    this.ready = false;

    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    view.webview.html = this.renderHtml(view.webview);

    view.webview.onDidReceiveMessage((raw) => {
      const action = raw as DashboardAction;
      if (action?.type === "ready") {
        this.ready = true;
        // Replay prior events so the newly-opened view shows current state.
        for (const ev of this.replayLog) {
          view.webview.postMessage(ev);
        }
        return;
      }
      for (const handler of this.actionListeners) {
        Promise.resolve(handler(action)).catch(err =>
          this.log(`dashboard handler error: ${err}`),
        );
      }
    });

    view.onDidDispose(() => {
      this.view = undefined;
      this.ready = false;
    });
  }

  // ── Internals ────────────────────────────────────────────────────────────

  private startTick(): void {
    this.stopTick();
    this.tickInterval = setInterval(() => {
      if (!this.currentSessionId || !this.sessionStartedAt) return;
      if (!this.view || !this.ready) return;
      const elapsedMs = Date.now() - this.sessionStartedAt;
      // Tick events are NOT added to replayLog — they'd grow unbounded.
      this.view.webview.postMessage({
        type: "tick",
        sessionId: this.currentSessionId,
        elapsedMs,
      } as DashboardEvent);
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
    const raw = fs.readFileSync(path.join(mediaDir.fsPath, "index.html"), "utf-8");

    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(mediaDir, "style.css"));
    const appUri   = webview.asWebviewUri(vscode.Uri.joinPath(mediaDir, "app.js"));
    const nonce    = randomNonce();

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
