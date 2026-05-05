/**
 * modelSelectorCore.ts — pure helpers for resolving an agent's chat-model
 * family from agent + skill frontmatter. The vscode-using shell lives in
 * modelSelector.ts.
 *
 * Resolution order (implemented by selectModelForAgent in modelSelector.ts):
 *   1. First active skill whose SKILL.md declares `model:` (in load order).
 *      Skills override the agent default so a "complicated skill" can lift
 *      a small agent onto a heavier family for that one invocation.
 *   2. Agent file's `model:` field.
 *   3. Configured fallback family.
 *   4. Any vendor=copilot model.
 */

import * as fs from "fs";
import * as path from "path";

const FRONTMATTER_MODEL_RE = /^\s*model:\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$/;

/**
 * Pull `model: <family>` out of a YAML frontmatter block. Returns null if
 * the file has no frontmatter or no `model:` line. Quoted values and
 * trailing comments are accepted; multi-line values are not (frontmatter
 * shape in this repo is single-line). Works for both agent.md and SKILL.md
 * since both use the same `---` block convention.
 */
export function parseFrontmatterModel(text: string): string | null {
  if (!text.startsWith("---")) { return null; }
  const end = text.indexOf("\n---", 3);
  if (end === -1) { return null; }
  const block = text.slice(3, end);
  for (const rawLine of block.split("\n")) {
    const m = rawLine.match(FRONTMATTER_MODEL_RE);
    if (m) { return m[1]; }
  }
  return null;
}

/** @deprecated alias preserved for callers — use parseFrontmatterModel. */
export const parseAgentModelFamily = parseFrontmatterModel;

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
    const fam = parseFrontmatterModel(text);
    if (fam) { return fam; }
  }
  return null;
}

/**
 * Resolve `<root>/.github/skills/<skillId>/SKILL.md` across the provided
 * roots and return the family declared by `model:` in its frontmatter.
 * Returns null if the skill file doesn't exist or doesn't declare one.
 */
export function readSkillModelFamily(
  roots: readonly string[],
  skillId: string,
): string | null {
  for (const root of roots) {
    if (!root) { continue; }
    const p = path.join(root, ".github", "skills", skillId, "SKILL.md");
    let text: string;
    try { text = fs.readFileSync(p, "utf-8"); } catch { continue; }
    const fam = parseFrontmatterModel(text);
    if (fam) { return fam; }
  }
  return null;
}

/**
 * Walk a list of skills in load order; return the first family any of
 * them declares. The convention is "first wins", so when an agent loads
 * multiple skills, put the skill whose model demand is highest at the
 * front of `inject_skills` (or the dynamic stage→skill map).
 */
export function pickSkillModelFamily(
  roots: readonly string[],
  skills: readonly string[],
): { skillId: string; family: string } | null {
  for (const skillId of skills) {
    if (!skillId) { continue; }
    const fam = readSkillModelFamily(roots, skillId);
    if (fam) { return { skillId, family: fam }; }
  }
  return null;
}

