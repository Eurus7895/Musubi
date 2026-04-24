/**
 * tasksView.ts — CopilotHarness Tasks sidebar TreeView.
 *
 * Native vscode.TreeDataProvider. No webview, no HTML/CSS — just TreeItems
 * styled with codicons and the user's VS Code theme.
 *
 * Tree shape:
 *   CopilotHarness
 *     ├── Active session  (only if a session is live)
 *     │     ├── ✓ planner  · 3.1s
 *     │     ├── ✓ designer · 4.8s
 *     │     ├── ↻ coder    · attempt 2/3
 *     │     └── ○ reviewer · pending
 *     └── History
 *           ├── ✓ s/9f3a2c  · /feature-dev  · 18s  · 1 retry
 *           │     ├── ✓ planner
 *           │     ├── ✓ designer
 *           │     ├── ✓ coder
 *           │     └── ✓ reviewer
 *           ├── ⚠ s/8b21a4  · /feature-dev  · escalated
 *           └── …
 *
 * Clicks route to:
 *   - Stage rows → open .harness/sessions/<sid>/<stage>.md
 *   - Session rows → expand; double-click opens plan.md
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { McpClient } from "./mcpClient";

// ── Node types ──────────────────────────────────────────────────────────────

type TaskNode =
  | { kind: "section"; section: "active" | "history" }
  | { kind: "active-stage"; stage: string; status: string; attempt: number }
  | {
      kind: "session";
      id: string;
      command: string;
      outcome: "complete" | "escalated" | "in_progress" | "partial";
      summary: string;
      mtime: number;
    }
  | { kind: "session-stage"; sessionId: string; stage: string; file: string };

const STAGE_ORDER = ["plan", "design", "code", "review"] as const;
const STAGE_TO_AGENT: Record<string, string> = {
  plan: "planner", design: "designer", code: "coder", review: "reviewer",
};

// ── Provider ────────────────────────────────────────────────────────────────

export class HarnessTasksProvider implements vscode.TreeDataProvider<TaskNode> {
  private _onDidChangeTreeData = new vscode.EventEmitter<TaskNode | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(
    private readonly client: McpClient,
    private readonly workspaceRoot: string,
    private readonly log: (msg: string) => void,
  ) {}

  refresh(node?: TaskNode): void {
    this._onDidChangeTreeData.fire(node);
  }

  getTreeItem(node: TaskNode): vscode.TreeItem {
    switch (node.kind) {
      case "section": {
        const label = node.section === "active" ? "Active session" : "History";
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Expanded);
        item.contextValue = "section";
        item.iconPath = new vscode.ThemeIcon(node.section === "active" ? "pulse" : "history");
        return item;
      }
      case "active-stage": {
        const agent = STAGE_TO_AGENT[node.stage] ?? node.stage;
        const item = new vscode.TreeItem(agent, vscode.TreeItemCollapsibleState.None);
        item.iconPath = stageIcon(node.status);
        item.description = stageDescription(node.status, node.attempt);
        item.tooltip = `stage: ${node.stage}\nstatus: ${node.status}\nattempt: ${node.attempt}`;
        item.contextValue = "active-stage";
        return item;
      }
      case "session": {
        const label = node.id;
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Collapsed);
        item.description = node.summary;
        item.iconPath = outcomeIcon(node.outcome);
        item.tooltip = `session ${node.id}\ncommand: ${node.command}\noutcome: ${node.outcome}\n${new Date(node.mtime).toLocaleString()}`;
        item.contextValue = "session";
        // Single-click opens plan.md (the first meaningful artifact).
        item.command = {
          command: "copilot-harness.openSessionArtifact",
          title: "Open",
          arguments: [node.id, "plan"],
        };
        return item;
      }
      case "session-stage": {
        const agent = STAGE_TO_AGENT[node.stage] ?? node.stage;
        const item = new vscode.TreeItem(agent, vscode.TreeItemCollapsibleState.None);
        item.description = path.basename(node.file);
        item.iconPath = new vscode.ThemeIcon("symbol-file");
        item.contextValue = "session-stage";
        item.command = {
          command: "copilot-harness.openSessionArtifact",
          title: "Open",
          arguments: [node.sessionId, node.stage],
        };
        return item;
      }
    }
  }

  async getChildren(node?: TaskNode): Promise<TaskNode[]> {
    try {
      if (!node) {
        const children: TaskNode[] = [];
        if (await this.hasActiveSession()) {
          children.push({ kind: "section", section: "active" });
        }
        children.push({ kind: "section", section: "history" });
        return children;
      }
      if (node.kind === "section" && node.section === "active") {
        return await this.loadActiveStages();
      }
      if (node.kind === "section" && node.section === "history") {
        return await this.loadHistory();
      }
      if (node.kind === "session") {
        return this.loadSessionStages(node.id);
      }
      return [];
    } catch (err) {
      this.log(`tasksView getChildren error: ${err instanceof Error ? err.message : String(err)}`);
      return [];
    }
  }

  // ── Data sources ────────────────────────────────────────────────────────

  private async hasActiveSession(): Promise<boolean> {
    try {
      const raw = await this.client.callTool("harness_get_active_session", {});
      const active = JSON.parse(raw) as { session_id: string | null };
      return !!active.session_id;
    } catch {
      return false;
    }
  }

  private async loadActiveStages(): Promise<TaskNode[]> {
    try {
      const activeRaw = await this.client.callTool("harness_get_active_session", {});
      const active = JSON.parse(activeRaw) as { session_id: string | null };
      if (!active.session_id) return [];
      const statusRaw = await this.client.callTool("harness_get_status", { session_id: active.session_id });
      const status = JSON.parse(statusRaw) as {
        stages: Record<string, { status: string; attempt: number }>;
      };
      return STAGE_ORDER.map(stage => {
        const info = status.stages?.[stage];
        return {
          kind: "active-stage" as const,
          stage,
          status: info?.status ?? "pending",
          attempt: info?.attempt ?? 0,
        };
      });
    } catch (err) {
      this.log(`tasksView loadActiveStages error: ${err instanceof Error ? err.message : String(err)}`);
      return [];
    }
  }

  /**
   * History is read from .harness/sessions/<sid>/ on disk so this works
   * even if the MCP server isn't running. Outcome is inferred from which
   * artifacts exist and — for `review.md` — from its status field.
   */
  private async loadHistory(): Promise<TaskNode[]> {
    const sessionsDir = path.join(this.workspaceRoot, ".harness", "sessions");
    if (!fs.existsSync(sessionsDir)) return [];
    const entries = fs.readdirSync(sessionsDir, { withFileTypes: true });
    const sessions: TaskNode[] = [];

    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const id = entry.name;
      const dir = path.join(sessionsDir, id);
      const node = this.summarizeSession(id, dir);
      if (node) sessions.push(node);
    }
    // Most-recent first.
    sessions.sort((a, b) => {
      if (a.kind !== "session" || b.kind !== "session") return 0;
      return b.mtime - a.mtime;
    });
    return sessions;
  }

  private summarizeSession(id: string, dir: string): TaskNode | null {
    try {
      const files = fs.readdirSync(dir);
      // plan.md / design.md / code.md / review.md (+ .attempt2 etc.)
      const hasPlan = files.some(f => f === "plan.md" || /^plan\.attempt\d+\.md$/.test(f));
      const hasDesign = files.some(f => f === "design.md" || /^design\.attempt\d+\.md$/.test(f));
      const hasCode = files.some(f => f === "code.md" || /^code\.attempt\d+\.md$/.test(f));
      const reviewFiles = files.filter(f => f === "review.md" || /^review\.attempt\d+\.md$/.test(f));

      let outcome: "complete" | "escalated" | "in_progress" | "partial" = "partial";
      let summary = "";

      // Pick the latest review.md attempt (by suffix).
      if (reviewFiles.length > 0) {
        const latest = reviewFiles.sort().pop()!;
        const reviewContent = fs.readFileSync(path.join(dir, latest), "utf-8");
        const statusMatch = reviewContent.match(/\*\*Status:\*\*\s+\S+\s+(\w+)/);
        const reviewStatus = statusMatch?.[1] ?? "unknown";
        if (reviewStatus === "pass")         outcome = "complete";
        else if (reviewStatus === "escalate") outcome = "escalated";
        else if (reviewStatus === "fail")     outcome = "partial";
        else                                  outcome = "partial";
        summary = `review: ${reviewStatus}`;
      } else if (hasCode) {
        outcome = "in_progress";
        summary = "code written, no review";
      } else if (hasDesign) {
        outcome = "in_progress";
        summary = "design only";
      } else if (hasPlan) {
        outcome = "in_progress";
        summary = "plan only";
      } else {
        return null; // empty dir
      }

      const stat = fs.statSync(dir);
      const command = inferCommand(dir);
      return {
        kind: "session",
        id,
        command,
        outcome,
        summary: command ? `${command} · ${summary}` : summary,
        mtime: stat.mtimeMs,
      };
    } catch (err) {
      this.log(`summarizeSession(${id}) error: ${err instanceof Error ? err.message : String(err)}`);
      return null;
    }
  }

  private loadSessionStages(sessionId: string): TaskNode[] {
    const dir = path.join(this.workspaceRoot, ".harness", "sessions", sessionId);
    if (!fs.existsSync(dir)) return [];
    const files = fs.readdirSync(dir);
    const out: TaskNode[] = [];
    for (const stage of STAGE_ORDER) {
      // Prefer the latest attempt's file if multiple exist.
      const matches = files
        .filter(f => f === `${stage}.md` || new RegExp(`^${stage}\\.attempt\\d+\\.md$`).test(f))
        .sort();
      const file = matches[matches.length - 1];
      if (file) {
        out.push({ kind: "session-stage", sessionId, stage, file });
      }
    }
    return out;
  }
}

// ── Icons + labels ──────────────────────────────────────────────────────────

function stageIcon(status: string): vscode.ThemeIcon {
  switch (status) {
    case "complete":     return new vscode.ThemeIcon("pass-filled", new vscode.ThemeColor("charts.green"));
    case "in_progress":  return new vscode.ThemeIcon("sync~spin",   new vscode.ThemeColor("charts.blue"));
    case "failed":       return new vscode.ThemeIcon("error",       new vscode.ThemeColor("charts.red"));
    case "pending":
    default:             return new vscode.ThemeIcon("circle-outline");
  }
}

function stageDescription(status: string, attempt: number): string {
  const base = status.replace("_", " ");
  if (attempt > 1) return `${base} · attempt ${attempt}`;
  return base;
}

function outcomeIcon(outcome: string): vscode.ThemeIcon {
  switch (outcome) {
    case "complete":    return new vscode.ThemeIcon("check-all",  new vscode.ThemeColor("charts.green"));
    case "escalated":   return new vscode.ThemeIcon("warning",    new vscode.ThemeColor("charts.yellow"));
    case "in_progress": return new vscode.ThemeIcon("sync~spin",  new vscode.ThemeColor("charts.blue"));
    case "partial":
    default:            return new vscode.ThemeIcon("circle-slash");
  }
}

/** Best-effort: read the plan.md front-matter-ish header to recover the
 * slash command that started the session. Returns empty on failure. */
function inferCommand(dir: string): string {
  const planPath = path.join(dir, "plan.md");
  if (!fs.existsSync(planPath)) return "";
  try {
    const first = fs.readFileSync(planPath, "utf-8").split("\n", 8).join("\n");
    // plan.md currently writes "> sessionId | attempt N" — no command.
    // We fall back to the session id shape. If a future materialiser
    // records the command, this is where we'd parse it.
    void first;
    return "";
  } catch {
    return "";
  }
}
