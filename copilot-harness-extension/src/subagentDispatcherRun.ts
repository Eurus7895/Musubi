/**
 * subagentDispatcherRun.ts — vscode shell that fires the pre-spawns
 * decided by `subagentDispatcher.ts` (Phase G.1.6). Pure helper logic
 * stays vscode-free in subagentDispatcher.ts so it's unit-testable;
 * this file holds only the `vscode` + `McpClient` glue.
 *
 * Lifecycle:
 *   1. `decidePreSpawns` produces a list of PreSpawnDescriptor.
 *   2. `runPreSpawns` iterates, calling `spawnSubAgent` per descriptor
 *      against the current stage's StageSpawnBudget.
 *   3. Each spawn's `RunSubagentResult` is wrapped into a
 *      `PreSpawnResult` (summary + finalStatus + reason).
 *   4. `spliceResultsIntoContext` merges the summaries into the
 *      parent stage's context dict.
 *
 * Failure modes are isolated per descriptor so one stuck explorer
 * doesn't abort the whole pre-spawn batch. Budget exhaustion stops
 * the loop early (the descriptors past the cap simply don't fire).
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import { McpClient } from "./mcpClient";
import {
  StageSpawnBudget,
  SubagentBudgetExhausted,
  spawnSubAgent,
} from "./pipeline";
import {
  decidePreSpawns,
  spliceResultsIntoContext,
  type DispatcherInput,
  type PreSpawnDescriptor,
  type PreSpawnResult,
} from "./subagentDispatcher";

export interface RunPreSpawnsOptions {
  client: McpClient;
  parentSessionId: string;
  parentAgentName: string;
  budget: StageSpawnBudget;
  descriptors: ReadonlyArray<PreSpawnDescriptor>;
  roots: string[];
  log: (msg: string) => void;
  token: vscode.CancellationToken;
  toolInvocationToken?: vscode.ChatParticipantToolToken;
  /** Where to render per-spawn progress. */
  stream: vscode.ChatResponseStream;
}

export async function runPreSpawns(
  opts: RunPreSpawnsOptions,
): Promise<PreSpawnResult[]> {
  const out: PreSpawnResult[] = [];
  for (const desc of opts.descriptors) {
    if (opts.token.isCancellationRequested) { break; }
    if (opts.budget.exhausted) {
      opts.log(`[dispatcher] budget exhausted before ${desc.label}; skipping remaining`);
      break;
    }

    opts.stream.markdown(
      `\n_↳ pre-spawn: **${desc.label}**_\n`,
    );

    let runResult: { summary: string | null; finalStatus: string | null; reason?: string };
    try {
      const r = await spawnSubAgent({
        client:           opts.client,
        parentSessionId:  opts.parentSessionId,
        parentAgentName:  opts.parentAgentName,
        budget:           opts.budget,
        role:             desc.role,
        brief:            desc.brief,
        allowedTools:     desc.allowedTools,
        roots:            opts.roots,
        log:              opts.log,
        token:            opts.token,
        toolInvocationToken: opts.toolInvocationToken,
      });
      runResult = {
        summary:     r.summary,
        finalStatus: r.finalStatus,
        reason:      r.reason,
      };
    } catch (err) {
      if (err instanceof SubagentBudgetExhausted) {
        opts.log(`[dispatcher] budget tripped on ${desc.label}; stopping batch`);
        break;
      }
      const msg = err instanceof Error ? err.message : String(err);
      opts.log(`[dispatcher] ${desc.label} threw: ${msg}`);
      runResult = { summary: null, finalStatus: null, reason: msg };
    }

    out.push({
      descriptor:  desc,
      summary:     runResult.summary,
      finalStatus: runResult.finalStatus,
      reason:      runResult.reason,
    });
  }
  return out;
}

/**
 * One-stop pre-spawn helper for callers in pipeline.ts. Builds the
 * dispatcher input, decides, runs, splices. Returns the augmented
 * context the parent stage should hand to the LM. Returns the input
 * context unchanged when no pre-spawns fire (cheap no-op).
 */
export interface PreSpawnAndSpliceOptions {
  client: McpClient;
  workspaceRoot: string;
  parentSessionId: string;
  parentAgentName: string;
  budget: StageSpawnBudget;
  stage: "planner" | "designer" | "coder" | "reviewer";
  chunkFilePaths: ReadonlyArray<string>;
  chunkId: string | null;
  design: Record<string, unknown> | null;
  baseContext: Record<string, unknown>;
  roots: string[];
  log: (msg: string) => void;
  token: vscode.CancellationToken;
  toolInvocationToken?: vscode.ChatParticipantToolToken;
  stream: vscode.ChatResponseStream;
  /** Test seam — when true, decision runs but spawns are skipped. */
  dryRun?: boolean;
}

export async function preSpawnAndSplice(
  opts: PreSpawnAndSpliceOptions,
): Promise<Record<string, unknown>> {
  const fileExistsOnDisk = new Map<string, boolean>();
  for (const rel of opts.chunkFilePaths) {
    fileExistsOnDisk.set(rel, fileExistsAt(opts.workspaceRoot, rel));
  }
  const dispatcherInput: DispatcherInput = {
    stage:            opts.stage,
    chunkFilePaths:   opts.chunkFilePaths,
    chunkId:          opts.chunkId,
    design:           opts.design,
    fileExistsOnDisk,
    remainingBudget:  Math.max(0, opts.budget.limit - opts.budget.used),
  };
  const descriptors = decidePreSpawns(dispatcherInput);
  if (descriptors.length === 0 || opts.dryRun) {
    if (descriptors.length > 0 && opts.dryRun) {
      opts.log(`[dispatcher] dry-run: would spawn ${descriptors.length} sub-agent(s)`);
    }
    return opts.baseContext;
  }
  const results = await runPreSpawns({
    client:               opts.client,
    parentSessionId:      opts.parentSessionId,
    parentAgentName:      opts.parentAgentName,
    budget:               opts.budget,
    descriptors,
    roots:                opts.roots,
    log:                  opts.log,
    token:                opts.token,
    toolInvocationToken:  opts.toolInvocationToken,
    stream:               opts.stream,
  });
  return spliceResultsIntoContext(opts.baseContext, results);
}

function fileExistsAt(workspaceRoot: string, rel: string): boolean {
  try {
    return fs.statSync(path.join(workspaceRoot, rel)).isFile();
  } catch {
    return false;
  }
}
