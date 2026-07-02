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
import { readAgentPrompt } from "./agentPromptResolver";

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
 * Pull `lm_tools:` out of a YAML frontmatter block as a list of strings.
 * Accepts both block-list shape:
 *
 *     lm_tools:
 *       - copilot_readFile
 *       - copilot_searchWorkspace
 *
 * and inline shape:
 *
 *     lm_tools: ["copilot_readFile", "copilot_searchWorkspace"]
 *
 * Returns an empty array when the field is missing — callers treat
 * "no field" and "field present but empty" identically (both opt out
 * of advertising any external tools to the LM).
 *
 * Names are returned untrimmed-of-quotes; quotes are stripped here so
 * the caller can put them in a Set without worrying about quoting.
 */
export function parseFrontmatterLmTools(text: string): string[] {
  if (!text.startsWith("---")) { return []; }
  const end = text.indexOf("\n---", 3);
  if (end === -1) { return []; }
  const block = text.slice(3, end);
  const lines = block.split("\n");

  // Find the `lm_tools:` line.
  let startIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s*lm_tools:\s*(.*)$/);
    if (m) {
      // Inline form: lm_tools: ["a", "b"]
      const tail = m[1].trim();
      if (tail.startsWith("[") && tail.endsWith("]")) {
        return tail.slice(1, -1)
          .split(",")
          .map(s => s.trim().replace(/^['"]|['"]$/g, ""))
          .filter(s => s.length > 0);
      }
      startIdx = i + 1;
      break;
    }
  }
  if (startIdx === -1) { return []; }

  // Block-list form: collect '  - <name>' lines until the next non-list line.
  const out: string[] = [];
  for (let i = startIdx; i < lines.length; i++) {
    const m = lines[i].match(/^\s*-\s*['"]?([^'"#\s]+)['"]?\s*(?:#.*)?$/);
    if (!m) { break; }
    out.push(m[1]);
  }
  return out;
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
  const text = readAgentText(roots, agentName);
  return text ? parseFrontmatterModel(text) : null;
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

const FRONTMATTER_MAX_TURNS_RE = /^\s*maxTurns:\s*(\d+)\s*(?:#.*)?$/;

/**
 * Pull `maxTurns:` out of a YAML frontmatter block as a positive integer.
 * Returns null when the field is absent, zero, or non-integer.
 */
export function parseFrontmatterMaxTurns(text: string): number | null {
  if (!text.startsWith("---")) { return null; }
  const end = text.indexOf("\n---", 3);
  if (end === -1) { return null; }
  const block = text.slice(3, end);
  for (const rawLine of block.split("\n")) {
    const m = rawLine.match(FRONTMATTER_MAX_TURNS_RE);
    if (m) {
      const n = parseInt(m[1], 10);
      return Number.isFinite(n) && n > 0 ? n : null;
    }
  }
  return null;
}

/**
 * Read agent file text from `<root>/.github/agents/<agentName>.agent.md`.
 * Returns null if not found in any root.
 */
export function readAgentText(roots: readonly string[], agentName: string): string | null {
  return readAgentPrompt(roots, agentName, { purpose: "root" });
}

/** Read `lm_tools:` from an agent's frontmatter. Returns [] when absent. */
export function readAgentLmToolNames(roots: readonly string[], agentName: string): string[] {
  const text = readAgentText(roots, agentName);
  return text ? parseFrontmatterLmTools(text) : [];
}

/**
 * Read `maxTurns:` from an agent's frontmatter. Returns `fallback` when
 * the field is absent or invalid.
 */
export function readAgentMaxTurns(
  roots: readonly string[],
  agentName: string,
  fallback: number,
): number {
  const text = readAgentText(roots, agentName);
  if (!text) { return fallback; }
  return parseFrontmatterMaxTurns(text) ?? fallback;
}
