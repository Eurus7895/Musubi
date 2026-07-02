/**
 * Agent prompt lookup by runtime purpose.
 *
 * harness-tier: substrate
 * expires-when: never - prompt purpose is part of the agent boundary contract.
 */

import * as fs from "fs";
import * as path from "path";

export type AgentPromptPurpose = "root" | "worker" | "pipeline-stage" | "meta";

export interface AgentPromptLookup {
  readonly purpose: AgentPromptPurpose;
  readonly pipelineName?: string;
}

export function resolveAgentPromptPath(
  roots: readonly string[],
  agentName: string,
  lookup: AgentPromptLookup,
): string | null {
  const safeAgent = safeSegment(agentName);
  if (!safeAgent) { return null; }
  const safePipeline = lookup.pipelineName ? safeSegment(lookup.pipelineName) : null;
  if (lookup.pipelineName && !safePipeline) { return null; }

  for (const root of roots) {
    if (!root) { continue; }
    const base = path.join(root, ".github", "agents");
    for (const candidate of candidates(base, safeAgent, lookup.purpose, safePipeline)) {
      if (fs.existsSync(candidate)) { return candidate; }
    }
  }
  return null;
}

export function readAgentPrompt(
  roots: readonly string[],
  agentName: string,
  lookup: AgentPromptLookup,
): string | null {
  const resolved = resolveAgentPromptPath(roots, agentName, lookup);
  if (!resolved) { return null; }
  try { return fs.readFileSync(resolved, "utf-8"); } catch { return null; }
}

function candidates(
  base: string,
  agentName: string,
  purpose: AgentPromptPurpose,
  pipelineName: string | null,
): string[] {
  const filename = `${agentName}.agent.md`;
  if (purpose === "root") {
    return [path.join(base, "root", filename), path.join(base, filename)];
  }
  if (purpose === "worker") {
    return [path.join(base, "workers", filename), path.join(base, filename)];
  }
  if (purpose === "meta") {
    return [path.join(base, "meta", filename), path.join(base, filename)];
  }
  if (pipelineName) {
    return [
      path.join(base, "pipeline-stages", pipelineName, filename),
      path.join(base, `${pipelineName}-${filename}`),
      path.join(base, filename),
    ];
  }
  return [path.join(base, "pipeline-stages", filename), path.join(base, filename)];
}

function safeSegment(value: string): string | null {
  const raw = value.trim().toLowerCase();
  if (!raw || raw.includes("/") || raw.includes("\\") || raw.includes("..")) {
    return null;
  }
  return raw;
}
