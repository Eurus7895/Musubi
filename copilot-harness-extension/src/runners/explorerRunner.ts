/**
 * harness-tier: ephemeral
 * expires-when: models do reliable native exploration
 * cost-lever: deletes the explorer runner + spawn machinery
 * (what: Sub-agent runner for codebase-scan role.)
 */
/**
 * runners/explorerRunner.ts — read-only workspace-scan sub-agent
 * (Phase G.1). Thin role wrapper over the generic shell in
 * subagentRunner.ts. The role config (tools, max_turns, wall-clock,
 * agent.md path) lives in subagentRunnerCore.ts::SUBAGENT_ROLE_CONFIGS.
 */

import {
  runSubagentForHandle,
  spawnAndRunSubagent,
  type RunSubagentForHandleOptions,
  type RunSubagentResult,
  type SpawnAndRunSubagentOptions,
} from "./subagentRunner";

export const EXPLORER_ROLE = "explorer" as const;

/** Run the explorer for an already-spawned handle. */
export function runExplorerForHandle(
  opts: Omit<RunSubagentForHandleOptions, "role">,
): Promise<RunSubagentResult> {
  return runSubagentForHandle({ ...opts, role: EXPLORER_ROLE });
}

/** Spawn an explorer, run it, and await its terminal row. */
export function spawnExplorer(
  opts: Omit<SpawnAndRunSubagentOptions, "role">,
): Promise<RunSubagentResult> {
  return spawnAndRunSubagent({ ...opts, role: EXPLORER_ROLE });
}
