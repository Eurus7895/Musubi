/**
 * runners/reviewerAuxRunner.ts — per-file checklist review sub-agent
 * (Phase G.1). Thin role wrapper over the generic shell in
 * subagentRunner.ts. The role config lives in
 * subagentRunnerCore.ts::SUBAGENT_ROLE_CONFIGS.
 *
 * Tightest tool surface of the three: `copilot_readFile` only. The
 * parent reviewer (or coder) hands over a single file path + checklist
 * brief; reviewer-aux returns a per-line / per-section verdict without
 * holding any of the parent's context.
 *
 * Hard Invariant #3 reminder: like all sub-agent roles, reviewer-aux
 * sees only `brief` + `role_skill` from `harness_get_subagent_context`
 * — never the parent's plan, design, or memory. The firewall is
 * enforced by `validation/subagent_context.py` and exercised by
 * `firewallLeak.test.ts`.
 */

import {
  runSubagentForHandle,
  spawnAndRunSubagent,
  type RunSubagentForHandleOptions,
  type RunSubagentResult,
  type SpawnAndRunSubagentOptions,
} from "./subagentRunner";

export const REVIEWER_AUX_ROLE = "reviewer-aux" as const;

/** Run the reviewer-aux for an already-spawned handle. */
export function runReviewerAuxForHandle(
  opts: Omit<RunSubagentForHandleOptions, "role">,
): Promise<RunSubagentResult> {
  return runSubagentForHandle({ ...opts, role: REVIEWER_AUX_ROLE });
}

/** Spawn a reviewer-aux, run it, and await its terminal row. */
export function spawnReviewerAux(
  opts: Omit<SpawnAndRunSubagentOptions, "role">,
): Promise<RunSubagentResult> {
  return spawnAndRunSubagent({ ...opts, role: REVIEWER_AUX_ROLE });
}
