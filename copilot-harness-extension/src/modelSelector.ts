/**
 * modelSelector.ts — vscode shell that resolves an agent's chat model from
 * its frontmatter (via modelSelectorCore) and asks Copilot for it.
 *
 * Selection chain, in order:
 *   1. Family declared in `.agent.md` frontmatter (`model:` field).
 *   2. Provided fallback family (caller's preferred default).
 *   3. Any vendor=copilot model.
 * Throws only when (3) is empty — i.e. Copilot Chat isn't installed /
 * signed in. Family-not-available is logged and falls through.
 */

import * as vscode from "vscode";
import { readAgentModelFamily } from "./modelSelectorCore";

export interface SelectModelOptions {
  roots: readonly string[];
  agentName: string;
  /** Used when the agent file has no `model:` (e.g. direct mode). */
  fallbackFamily?: string;
  /** Receives one informational line per fallback step. */
  log?: (msg: string) => void;
}

const DEFAULT_FALLBACK_FAMILY = "gpt-4o";

export async function selectModelForAgent(
  opts: SelectModelOptions,
): Promise<vscode.LanguageModelChat> {
  const log = opts.log ?? (() => { /* no-op */ });
  const declared = readAgentModelFamily(opts.roots, opts.agentName);
  const fallback = opts.fallbackFamily ?? DEFAULT_FALLBACK_FAMILY;
  const requested = declared ?? fallback;

  let models = await vscode.lm.selectChatModels({
    vendor: "copilot", family: requested,
  });
  if (models.length > 0) {
    if (declared) {
      log(`[model] ${opts.agentName}: family=${requested} (from frontmatter)`);
    } else {
      log(`[model] ${opts.agentName}: family=${requested} (fallback — no frontmatter)`);
    }
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
