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
import {
  describeChunk,
  describeSession,
  describeStage,
  summarizeSession,
  summarizeStages,
  STAGE_ORDER as CORE_STAGE_ORDER,
  type ChunkSummary,
  type SessionSummary,
  type StageMetricsRow,
  type StageStatusInfo,
  type StageSummary,
} from "./tasksViewCore";
import { snapshotActiveBudget } from "./pipelineBudgetCore";

// ── Node types ──────────────────────────────────────────────────────────────

type TaskNode =
  | { kind: "section"; section: "active" | "history" }
  // Stage 1 (MVP A.4) — session-level header carrying the live budget
  // snapshot (when an enforcer is registered) or the persisted
  // historic credits sum. Rendered as the first child of the "active"
  // section, above the per-stage rows.
  | { kind: "active-session-summary"; summary: SessionSummary }
  | { kind: "active-stage"; summary: StageSummary }
  | { kind: "active-chunk"; stage: string; chunk: ChunkSummary }
  | {
      kind: "session";
      id: string;
      command: string;
      outcome: "complete" | "escalated" | "in_progress" | "partial";
      summary: string;
      mtime: number;
    }
  | { kind: "session-stage"; sessionId: string; stage: string; file: string };

const STAGE_ORDER = CORE_STAGE_ORDER;
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
        // Differentiate active vs history so the "Clear" inline action only
        // shows on the active-session header. Bound via package.json
        // contributes.menus["view/item/context"] when=viewItem==section-active.
        item.contextValue = node.section === "active" ? "section-active" : "section-history";
        item.iconPath = new vscode.ThemeIcon(node.section === "active" ? "pulse" : "history");
        return item;
      }
      case "active-session-summary": {
        // Stage 1 (MVP A.4) — render "X / Y credits (Z%)" when live
        // budget exists, "X credits used" for paused/historic, or
        // empty when no spend has happened yet. Session id shown as
        // the secondary description so the user knows which run.
        const s = node.summary;
        const item = new vscode.TreeItem(
          s.sessionId, vscode.TreeItemCollapsibleState.None,
        );
        item.iconPath = new vscode.ThemeIcon(
          s.liveBudget ? "credit-card" : "history",
        );
        item.description = describeSession(s) || s.status;
        const tipParts = [
          `session: ${s.sessionId}`,
          `status: ${s.status}`,
        ];
        if (s.liveBudget) {
          tipParts.push(
            `live budget: ${s.liveBudget.creditsUsed.toFixed(2)} / ${s.liveBudget.maxCredits.toFixed(0)} credits`,
            `remaining: ${s.liveBudget.remaining.toFixed(2)}`,
            `warn at: ${(s.liveBudget.warnAtRatio * 100).toFixed(0)}%`,
          );
        } else if (s.totalCredits > 0) {
          tipParts.push(`historic credits: ${s.totalCredits.toFixed(2)}`);
        }
        item.tooltip = tipParts.join("\n");
        item.contextValue = "active-session-summary";
        return item;
      }
      case "active-stage": {
        const s = node.summary;
        const agent = STAGE_TO_AGENT[s.stage] ?? s.stage;
        // Expandable when chunked (>1 chunk under this stage) so the
        // user can drill into per-chunk progress without leaving the
        // sidebar.
        const collapsible = s.chunks.length > 1
          ? vscode.TreeItemCollapsibleState.Collapsed
          : vscode.TreeItemCollapsibleState.None;
        const item = new vscode.TreeItem(agent, collapsible);
        item.iconPath = stageIcon(s.status);
        const desc = describeStage(s);
        item.description = desc || s.status.replace("_", " ");
        item.tooltip =
          `stage: ${s.stage}\n` +
          `status: ${s.status}\n` +
          `attempt: ${s.attempt}\n` +
          `lm calls: ${s.rowCount}` +
          (s.totalLmMs > 0 ? `\nlm time: ${(s.totalLmMs / 1000).toFixed(1)}s` : "") +
          (s.totalTokensIn > 0 ? `\ntokens in: ${s.totalTokensIn}` : "") +
          (s.totalTokensOut > 0 ? `\ntokens out: ${s.totalTokensOut}` : "");
        item.contextValue = "active-stage";
        return item;
      }
      case "active-chunk": {
        const item = new vscode.TreeItem(node.chunk.chunk_id, vscode.TreeItemCollapsibleState.None);
        // Reuse stageIcon's "complete" colour when the chunk has at least
        // one LM call recorded; otherwise the same circle-outline as a
        // pending stage. Chunk-level "complete" status isn't in the DB —
        // a chunk row that has lm_ms > 0 is at least in-progress.
        item.iconPath = node.chunk.totalLmMs > 0
          ? new vscode.ThemeIcon("circle-filled", new vscode.ThemeColor("charts.green"))
          : new vscode.ThemeIcon("circle-outline");
        item.description = describeChunk(node.chunk);
        item.tooltip =
          `chunk: ${node.chunk.chunk_id}\n` +
          `attempt: ${node.chunk.attempt}\n` +
          `lm calls: ${node.chunk.rowCount}` +
          (node.chunk.totalLmMs > 0 ? `\nlm time: ${(node.chunk.totalLmMs / 1000).toFixed(1)}s` : "");
        item.contextValue = "active-chunk";
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
      if (node.kind === "active-stage") {
        // Expand into per-chunk rows when the code stage chunked into >1.
        if (node.summary.chunks.length <= 1) { return []; }
        return node.summary.chunks.map(chunk => ({
          kind: "active-chunk" as const, stage: node.summary.stage, chunk,
        }));
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

      // Two reads in parallel: stage statuses (from sessions/stage_outputs)
      // and per-call metrics (timing + chunk breakdown). Metrics is the
      // only source for chunk_id, so we need both to render the live view.
      const [statusRaw, metricsRaw] = await Promise.all([
        this.client.callTool("harness_get_status", { session_id: active.session_id }),
        this.client.callTool("harness_query_stage_metrics", { session_id: active.session_id }),
      ]);
      const status = JSON.parse(statusRaw) as {
        stages?: Record<string, StageStatusInfo>;
        status?: string;
        total_credits?: number;
      };
      const metricsResponse = JSON.parse(metricsRaw) as {
        status?: string;
        rows?: StageMetricsRow[];
      };
      const rows = metricsResponse.rows ?? [];
      const summaries = summarizeStages(status.stages ?? {}, rows);

      // Stage 1 (MVP A.4) — session-level budget header. Live snapshot
      // comes from snapshotActiveBudget (non-null iff a pipeline is
      // currently running against this session). Historic credits come
      // from harness_get_status.total_credits (summed from
      // stage_metrics.credits server-side). summarizeSession picks
      // whichever is authoritative.
      const sessionSummary = summarizeSession(
        active.session_id,
        status.status ?? "active",
        status.total_credits ?? 0,
        snapshotActiveBudget(active.session_id),
      );

      return [
        { kind: "active-session-summary" as const, summary: sessionSummary },
        ...summaries.map(summary => ({
          kind: "active-stage" as const, summary,
        })),
      ];
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
