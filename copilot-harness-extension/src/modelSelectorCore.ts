/**
 * modelSelectorCore.ts — pure helpers for resolving an agent's chat-model
 * family from its `.agent.md` frontmatter. The vscode-using shell lives
 * in modelSelector.ts.
 *
 * Contract: agent files declare `model: <family>` (e.g. `gpt-4o`,
 * `gpt-4o-mini`, `claude-sonnet-4.5`) in their YAML frontmatter. The
 * runtime reads that field and passes it to vscode.lm.selectChatModels
 * so each agent runs against its declared family — not whatever the
 * caller hardcoded.
 */

import * as fs from "fs";
import * as path from "path";

const FRONTMATTER_MODEL_RE = /^\s*model:\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$/;

/**
 * Pull `model: <family>` out of a YAML frontmatter block. Returns null if
 * the file has no frontmatter or no `model:` line. Quoted values and
 * trailing comments are accepted; multi-line values are not (frontmatter
 * shape in this repo is single-line).
 */
export function parseAgentModelFamily(agentMd: string): string | null {
  if (!agentMd.startsWith("---")) { return null; }
  const end = agentMd.indexOf("\n---", 3);
  if (end === -1) { return null; }
  const block = agentMd.slice(3, end);
  for (const rawLine of block.split("\n")) {
    const m = rawLine.match(FRONTMATTER_MODEL_RE);
    if (m) { return m[1]; }
  }
  return null;
}

/**
 * Resolve `<root>/.github/agents/<agentName>.agent.md` across the
 * provided roots (workspace first, extension bundle second), parse its
 * `model:` frontmatter, and return the family. Returns null if the file
 * is missing or the frontmatter doesn't declare one.
 */
export function readAgentModelFamily(
  roots: readonly string[],
  agentName: string,
): string | null {
  for (const root of roots) {
    if (!root) { continue; }
    const p = path.join(root, ".github", "agents", `${agentName}.agent.md`);
    let text: string;
    try { text = fs.readFileSync(p, "utf-8"); } catch { continue; }
    const fam = parseAgentModelFamily(text);
    if (fam) { return fam; }
  }
  return null;
}
