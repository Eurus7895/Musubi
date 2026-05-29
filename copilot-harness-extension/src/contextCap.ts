/**
 * contextCap.ts — vscode shell that resolves the effective context-token
 * budget for a harness LM call.
 *
 * Mirrors the modelOverride pattern: a three-layer fall-through
 * resolution chain, with a slash command (/context-cap) that writes the
 * VS Code setting.
 *
 * Resolution chain, in order:
 *   1. `context_cap:` field in the relevant pipeline.yaml (if running a
 *      named pipeline). Lets a complex pipeline ask for more headroom.
 *   2. `copilotHarness.contextCap` VS Code setting (0 = unset).
 *   3. DEFAULT_CONTEXT_CAP constant — the new lower default that
 *      replaces the previous "always use the full 200k model window"
 *      behaviour. Phase J.5 tradeoff: less history, much lower per-turn
 *      cost.
 *
 * Always clamped to [1, MODEL_CONTEXT_TOKENS]. A user-provided value
 * above the model window would silently overflow `sendRequest`; we
 * truncate-with-warning instead.
 *
 * The resolver is synchronous — the file read and config read are both
 * synchronous APIs, and the per-turn hot path can't afford to await on
 * an MCP round-trip.
 */

import * as vscode from "vscode";
import { resolvePipelineContextCap } from "./contextCapCore";
import { DEFAULT_CONTEXT_CAP, MODEL_CONTEXT_TOKENS } from "./runners/orchestratorCore";

export interface ResolveContextCapOptions {
  /**
   * Pipeline name when running pipeline mode. Omit for orchestrator
   * turns — the orchestrator isn't a pipeline and only consults the
   * setting + default.
   */
  pipelineName?: string;
  roots: readonly string[];
  /** Receives one informational line on each resolution / clamp event. */
  log?: (msg: string) => void;
}

export interface ResolveContextCapResult {
  cap: number;
  source: "pipeline" | "settings" | "default";
}

export function resolveContextCap(
  opts: ResolveContextCapOptions,
): ResolveContextCapResult {
  const log = opts.log ?? (() => { /* no-op */ });

  // 1. Pipeline override.
  if (opts.pipelineName) {
    const pipelineCap = resolvePipelineContextCap(opts.roots, opts.pipelineName);
    if (pipelineCap !== null) {
      const clamped = clampToModel(pipelineCap, log, `pipeline=${opts.pipelineName}`);
      return { cap: clamped, source: "pipeline" };
    }
  }

  // 2. VS Code setting (0 = unset, treated as "use default").
  const setting = vscode.workspace
    .getConfiguration("copilotHarness")
    .get<number>("contextCap", 0);
  if (Number.isFinite(setting) && setting > 0) {
    const clamped = clampToModel(setting, log, "settings");
    return { cap: clamped, source: "settings" };
  }

  // 3. Built-in default.
  return { cap: DEFAULT_CONTEXT_CAP, source: "default" };
}

function clampToModel(
  requested: number,
  log: (msg: string) => void,
  label: string,
): number {
  if (requested > MODEL_CONTEXT_TOKENS) {
    log(`[context-cap] ${label} requested=${requested} clamped to MODEL_CONTEXT_TOKENS=${MODEL_CONTEXT_TOKENS}`);
    return MODEL_CONTEXT_TOKENS;
  }
  if (requested < 1) {
    log(`[context-cap] ${label} requested=${requested} below 1, using 1`);
    return 1;
  }
  return requested;
}
