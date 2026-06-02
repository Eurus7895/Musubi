/**
 * agentToolCycleCore.ts — Pure helpers for the bounded tool-call cycle loop
 * that A1 wires into pipeline.ts::runAgentLM.
 *
 * Lives here (not in pipeline.ts) so it can be unit-tested without dragging
 * in VS Code. The vscode-using loop lives inline in runAgentLM.
 */

/**
 * Default maxTurns per pipeline-mode agent. Pipeline agents that don't use
 * tools still exit at cycle 0 (no tool calls emitted) — the cap is a safety
 * net, not a billing commitment.
 *
 * Named by the value in each agent's `.agent.md` frontmatter; the runtime
 * reads the frontmatter value first and falls back to this table only when
 * the file is missing or the field is absent.
 */
export const MAX_TURNS_BY_AGENT: Readonly<Record<string, number>> = {
  planner:                  3,
  designer:                 5,
  coder:                   10,
  reviewer:                 5,
  "code-review-scoper":     2,
  "code-review-finder":     5,
  "code-review-synthesizer": 3,
  "pipeline-builder":       5,
  "skill-builder":          5,
};

/** Fallback when the agent is not in MAX_TURNS_BY_AGENT. */
export const DEFAULT_PIPELINE_MAX_TURNS = 3;

/**
 * Resolve the effective maxTurns for a pipeline agent. Priority:
 *   1. frontmatter value (already parsed by the caller via readAgentMaxTurns)
 *   2. MAX_TURNS_BY_AGENT entry for this agent name
 *   3. DEFAULT_PIPELINE_MAX_TURNS
 */
export function resolveMaxTurns(
  agentName: string,
  fromFrontmatter: number | null,
): number {
  if (fromFrontmatter !== null && fromFrontmatter > 0) { return fromFrontmatter; }
  return MAX_TURNS_BY_AGENT[agentName] ?? DEFAULT_PIPELINE_MAX_TURNS;
}
