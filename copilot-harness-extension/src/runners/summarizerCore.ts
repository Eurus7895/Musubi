/**
 * harness-tier: ephemeral
 * expires-when: models summarise concisely without role injection
 * cost-lever: deletes the summarizer core
 * (what: Pure summarisation loop logic.)
 */
/**
 * runners/summarizerCore.ts — pure helpers for the summarizer sub-agent
 * runner (Phase C.2). No vscode imports so node:test can exercise them
 * without a runtime. The vscode-using shell lives in summarizerRunner.ts.
 */

import { stripFrontmatter, type OrchestratorMessage } from "./orchestratorCore";

/**
 * Serialize older-half turns into the `[role] content` block format the
 * summarizer agent expects. Empty / whitespace-only content rows are
 * dropped so the summarizer does not waste tokens on noise.
 */
export function serializeSummarizerBrief(
  oldHalf: ReadonlyArray<OrchestratorMessage>,
): string {
  const lines: string[] = [];
  for (const m of oldHalf) {
    if (!m.content || !m.content.trim()) { continue; }
    lines.push(`[${m.role}] ${m.content}`);
  }
  return lines.join("\n\n");
}

/**
 * Build the summary system prompt = stripped agent.md body + appended
 * skill body. Mirrors buildOrchestratorSystemPrompt's shape so future
 * Phase-D generalization can collapse them.
 */
export function buildSummarizerSystemPrompt(
  agentMd: string,
  skillBody: string | null,
): string {
  const parts: string[] = [stripFrontmatter(agentMd).trim()];
  const skill = (skillBody ?? "").trim();
  if (skill.length > 0) {
    parts.push(
      "\n\n## Skill: summarizer (pushed by harness)\n\n" + stripFrontmatter(skill).trim()
    );
  }
  return parts.join("");
}
