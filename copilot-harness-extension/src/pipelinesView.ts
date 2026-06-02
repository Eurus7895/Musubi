/**
 * pipelinesView.ts — CopilotHarness Pipelines sidebar TreeView.
 *
 * Companion to the per-pipeline auto-approve setting
 * (`copilotHarness.autoApprove.<pipeline>`). Previously the toggle
 * lived as a button in the in-chat review-gate UI, but VS Code's
 * single-resolution semantics for chat-response buttons caused a click
 * on the toggle to disable the four review-gate buttons in the same
 * turn — leaving the user stuck. Moving the toggle to a sidebar view
 * (same pattern as the Models view) resolves the collision:
 * sidebar clicks don't share lifecycle with chat-response buttons.
 *
 * Tree shape:
 *   CopilotHarness › Pipelines
 *     ├── ● feature-dev      auto-approve ON
 *     ├── ○ code-review      auto-approve OFF
 *     └── …
 *
 * Each row is a pipeline discovered under `.github/pipelines/`. Click
 * a row to flip the auto-approve setting for that pipeline.
 *
 * Refresh triggers (auto):
 *   - copilotHarness.autoApprove changed (settings UI, sidebar click)
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

interface PipelineNode {
  name: string;
  autoApprove: boolean;
  /** sourceRoot is purely diagnostic — which root the directory was discovered under */
  sourceRoot: string;
}

export class HarnessPipelinesProvider implements vscode.TreeDataProvider<PipelineNode> {
  private _onDidChangeTreeData = new vscode.EventEmitter<PipelineNode | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(
    private readonly roots: readonly string[],
    private readonly log: (msg: string) => void,
  ) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(node: PipelineNode): vscode.TreeItem {
    const item = new vscode.TreeItem(node.name, vscode.TreeItemCollapsibleState.None);
    item.iconPath = new vscode.ThemeIcon(node.autoApprove ? "circle-filled" : "circle-outline");
    item.description = node.autoApprove ? "auto-approve ON" : "auto-approve OFF";
    item.contextValue = node.autoApprove ? "pipeline-auto-on" : "pipeline-auto-off";
    item.tooltip =
      `Pipeline: /${node.name}\n` +
      `Auto-approve: ${node.autoApprove ? "ON — review gate skipped" : "OFF — review gate fires between stages"}\n` +
      `Click to ${node.autoApprove ? "turn OFF" : "turn ON"}.`;
    item.command = {
      command: "copilot-harness.togglePipelineAutoApprove",
      title: "Toggle auto-approve",
      arguments: [node.name],
    };
    return item;
  }

  async getChildren(parent?: PipelineNode): Promise<PipelineNode[]> {
    if (parent) {
      return [];
    }
    const names = listPipelines(this.roots);
    const cfg = vscode.workspace.getConfiguration("copilotHarness");
    const autoApproveMap = cfg.get<Record<string, unknown>>("autoApprove") ?? {};
    return names.map((entry) => ({
      name: entry.name,
      autoApprove: Boolean(autoApproveMap[entry.name]),
      sourceRoot: entry.sourceRoot,
    }));
  }
}

interface DiscoveredPipeline {
  name: string;
  sourceRoot: string;
}

/**
 * Enumerate `.github/pipelines/<name>/pipeline.yaml` under each root.
 * First root wins on name collisions (workspace > extension bundle).
 */
function listPipelines(roots: readonly string[]): DiscoveredPipeline[] {
  const seen = new Map<string, DiscoveredPipeline>();
  for (const root of roots) {
    if (!root) { continue; }
    const dir = path.join(root, ".github", "pipelines");
    let entries: string[];
    try {
      entries = fs.readdirSync(dir);
    } catch {
      continue;
    }
    for (const e of entries) {
      if (!/^[a-z0-9_-]+$/i.test(e)) { continue; }
      const yamlPath = path.join(dir, e, "pipeline.yaml");
      try {
        if (!fs.statSync(yamlPath).isFile()) { continue; }
      } catch {
        continue;
      }
      if (!seen.has(e)) {
        seen.set(e, { name: e, sourceRoot: root });
      }
    }
  }
  return Array.from(seen.values()).sort((a, b) => a.name.localeCompare(b.name));
}
