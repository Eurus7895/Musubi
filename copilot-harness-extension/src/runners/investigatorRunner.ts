/**
 * harness-tier: ephemeral
 * expires-when: models do reliable native investigation
 * cost-lever: deletes the investigator runner + spawn machinery
 * (what: Sub-agent runner for diagnostic role.)
 */
/**
 * runners/investigatorRunner.ts — read + diagnostic-shell sub-agent
 * (Phase G.1). Thin role wrapper over the generic shell in
 * subagentRunner.ts. The role config lives in
 * subagentRunnerCore.ts::SUBAGENT_ROLE_CONFIGS.
 *
 * Differs from explorer by exposing `copilot_runInTerminal` +
 * `copilot_getErrors` so a parent can ask "does this typecheck?" or
 * "run pytest -k foo and tell me which assertions failed."
 */

import {
  runSubagentForHandle,
  spawnAndRunSubagent,
  type RunSubagentForHandleOptions,
  type RunSubagentResult,
  type SpawnAndRunSubagentOptions,
} from "./subagentRunner";

export const INVESTIGATOR_ROLE = "investigator" as const;

/** Run the investigator for an already-spawned handle. */
export function runInvestigatorForHandle(
  opts: Omit<RunSubagentForHandleOptions, "role">,
): Promise<RunSubagentResult> {
  return runSubagentForHandle({ ...opts, role: INVESTIGATOR_ROLE });
}

/** Spawn an investigator, run it, and await its terminal row. */
export function spawnInvestigator(
  opts: Omit<SpawnAndRunSubagentOptions, "role">,
): Promise<RunSubagentResult> {
  return spawnAndRunSubagent({ ...opts, role: INVESTIGATOR_ROLE });
}
