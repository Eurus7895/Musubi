/**
 * modelSelector.ts — vscode shell that resolves an agent's chat model from
 * its frontmatter (and any active skills' frontmatter), then asks Copilot
 * for it.
 *
 * Selection chain, in order:
 *   0. VS Code setting `copilotHarness.modelOverride` (if non-empty).
 *      Bypasses skill / agent / fallback resolution entirely. Lets a user
 *      switch every harness LM call to a cheap family when they've run
 *      out of quota on the agent defaults. Falls through to (1-4) only
 *      when the override family is unavailable on Copilot's side.
 *   1. First active skill that declares `model:` in its SKILL.md (load
 *      order). Lets a "complicated skill" lift a small agent onto a
 *      heavier family for that one invocation.
 *   2. Family declared in `<agent>.agent.md` frontmatter.
 *   3. Provided fallback family.
 *   4. Any vendor=copilot model.
 * Throws only when (4) is empty — i.e. Copilot Chat isn't installed /
 * signed in. Family-not-available is logged and falls through.
 */

import * as vscode from "vscode";
import { pickSkillModelFamily, readAgentModelFamily } from "./modelSelectorCore";

export interface SelectModelOptions {
  roots: readonly string[];
  agentName: string;
  /** Used when no skill or agent file declares `model:` (e.g. direct mode). */
  fallbackFamily?: string;
  /**
   * Skill IDs active for this invocation, in the load order the harness
   * uses. The first skill that declares `model:` overrides the agent
   * default — earlier entries win.
   */
  skills?: readonly string[];
  /** Receives one informational line per resolution / fallback step. */
  log?: (msg: string) => void;
}

const DEFAULT_FALLBACK_FAMILY = "claude-sonnet-4.5";

export async function selectModelForAgent(
  opts: SelectModelOptions,
): Promise<vscode.LanguageModelChat> {
  const log = opts.log ?? (() => { /* no-op */ });
  const fallback = opts.fallbackFamily ?? DEFAULT_FALLBACK_FAMILY;

  // 0. Settings override — wins everything when set.
  const overrideFamily = vscode.workspace
    .getConfiguration("copilotHarness")
    .get<string>("modelOverride", "")
    .trim();
  if (overrideFamily) {
    const overrideModels = await vscode.lm.selectChatModels({
      vendor: "copilot", family: overrideFamily,
    });
    if (overrideModels.length > 0) {
      log(`[model] ${opts.agentName}: family=${overrideFamily} (settings override)`);
      return overrideModels[0];
    }
    log(`[model] ${opts.agentName}: override family=${overrideFamily} unavailable on this Copilot subscription, falling through to frontmatter resolution`);
  }

  // 1. Skill override — first skill with `model:` wins.
  const skillPick = opts.skills && opts.skills.length > 0
    ? pickSkillModelFamily(opts.roots, opts.skills)
    : null;

  // 2. Agent default.
  const agentFamily = readAgentModelFamily(opts.roots, opts.agentName);

  // 3. Resolve to one requested family.
  let source: "skill" | "agent" | "fallback";
  let requested: string;
  if (skillPick) {
    source = "skill";
    requested = skillPick.family;
  } else if (agentFamily) {
    source = "agent";
    requested = agentFamily;
  } else {
    source = "fallback";
    requested = fallback;
  }

  const sourceLabel = source === "skill" ? `skill=${skillPick!.skillId}`
    : source === "agent" ? "frontmatter"
    : "fallback (no frontmatter)";

  let models = await vscode.lm.selectChatModels({
    vendor: "copilot", family: requested,
  });
  if (models.length > 0) {
    log(`[model] ${opts.agentName}: family=${requested} (${sourceLabel})`);
    return models[0];
  }

  if (requested !== fallback) {
    log(`[model] ${opts.agentName}: family=${requested} unavailable, trying fallback ${fallback}`);
    models = await vscode.lm.selectChatModels({ vendor: "copilot", family: fallback });
    if (models.length > 0) { return models[0]; }
  }

  log(`[model] ${opts.agentName}: no family match — picking any copilot model`);
  models = await vscode.lm.selectChatModels({ vendor: "copilot" });
  if (models.length > 0) { return models[0]; }

  throw new Error(
    "No Copilot language model found. Ensure GitHub Copilot Chat is installed and signed in.",
  );
}
