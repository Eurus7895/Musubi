/**
 * modelsView.ts — CopilotHarness Models sidebar TreeView.
 *
 * Companion surface to the `copilotHarness.modelOverride` setting and the
 * `/model` slash command. Lists every model family Copilot surfaces on the
 * current subscription so the user can see at a glance which families are
 * available and click one to switch the override.
 *
 * Tree shape:
 *   CopilotHarness › Models
 *     ├── Override: gpt-4o-mini          (header; "Clear" inline action)
 *     ├── ● gpt-4o-mini   gpt-4o-mini    (active family marked)
 *     ├── ○ claude-sonnet-4.5
 *     ├── ○ gemini-2.5-flash
 *     └── …
 *
 * Clicks:
 *   - Family row → set as override (writes copilotHarness.modelOverride at
 *     Global scope; the resolver in modelSelector.ts picks it up on the next
 *     LM call).
 *   - Header inline "Clear" → reset the setting to empty.
 *
 * Refresh triggers (auto):
 *   - copilotHarness.modelOverride changed (settings UI, /model, ours)
 *   - vscode.lm.onDidChangeChatModels (Copilot surfaced a new family or
 *     signed out)
 */

import * as vscode from "vscode";

// ── Node types ──────────────────────────────────────────────────────────────

type ModelNode =
  | { kind: "header"; current: string }
  | { kind: "family"; family: string; sampleId: string; isActive: boolean };

// ── Provider ────────────────────────────────────────────────────────────────

export class HarnessModelsProvider implements vscode.TreeDataProvider<ModelNode> {
  private _onDidChangeTreeData = new vscode.EventEmitter<ModelNode | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(private readonly log: (msg: string) => void) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(node: ModelNode): vscode.TreeItem {
    switch (node.kind) {
      case "header": {
        const label = node.current
          ? `Override: ${node.current}`
          : "Override: (none — using agent defaults)";
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
        item.iconPath = new vscode.ThemeIcon(node.current ? "pinned" : "settings-gear");
        // contextValue gates the inline "Clear" button via package.json:
        // when=viewItem==model-header-active.
        item.contextValue = node.current ? "model-header-active" : "model-header-none";
        item.tooltip = node.current
          ? `copilotHarness.modelOverride = "${node.current}". Every harness LM call uses this family. Click "Clear" to remove.`
          : "copilotHarness.modelOverride is unset. Each agent uses the family declared in its frontmatter.";
        return item;
      }
      case "family": {
        const item = new vscode.TreeItem(node.family, vscode.TreeItemCollapsibleState.None);
        item.iconPath = new vscode.ThemeIcon(node.isActive ? "circle-filled" : "circle-outline");
        item.contextValue = node.isActive ? "model-family-active" : "model-family";
        // sampleId is the concrete `LanguageModelChat.id` for one of the
        // models in this family — useful as a description so the user can
        // tell e.g. "gpt-4o-mini" from "gpt-4o" when names are similar.
        if (node.sampleId && node.sampleId !== node.family) {
          item.description = node.sampleId;
        }
        item.tooltip = node.isActive
          ? `${node.family} — current override. Click another family to switch.`
          : `${node.family} — click to set as override (writes copilotHarness.modelOverride).`;
        item.command = {
          command: "copilot-harness.setModelOverrideFromTree",
          title: "Set as harness model",
          arguments: [node.family],
        };
        return item;
      }
    }
  }

  async getChildren(parent?: ModelNode): Promise<ModelNode[]> {
    if (parent) {
      return [];
    }
    const current = vscode.workspace
      .getConfiguration("copilotHarness")
      .get<string>("modelOverride", "")
      .trim();

    let models: readonly vscode.LanguageModelChat[];
    try {
      models = await vscode.lm.selectChatModels({ vendor: "copilot" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.log(`[models-view] selectChatModels failed: ${msg}`);
      models = [];
    }

    const familyToSampleId = new Map<string, string>();
    for (const m of models) {
      if (!familyToSampleId.has(m.family)) {
        familyToSampleId.set(m.family, m.id);
      }
    }
    const families = Array.from(familyToSampleId.keys()).sort();

    const nodes: ModelNode[] = [{ kind: "header", current }];
    for (const family of families) {
      nodes.push({
        kind: "family",
        family,
        sampleId: familyToSampleId.get(family) ?? family,
        isActive: family === current,
      });
    }
    return nodes;
  }
}
