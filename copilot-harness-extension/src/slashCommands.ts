/**
 * slashCommands.ts — Load and parse slash command definitions from
 * .github/commands/*.md.
 *
 * Each command file has a YAML frontmatter block with keys:
 *   name:        string   (same as filename stem)
 *   description: string
 *   action:      "pipeline" | "step" | "continue" | "status" | "help"
 *   pipeline:    string   (only when action=pipeline)
 *   agent:       string   (only when action=step)
 *
 * A tiny hand-written parser keeps this dependency-free. The schema
 * is narrow and controlled in-repo, so a full YAML parser would be
 * overkill.
 *
 * Resolution order: each loader/lister accepts a list of roots and
 * searches them in order — workspace first, extension bundle as the
 * fallback — so the slash commands shipped with the .vsix work even
 * when the open workspace has no `.github/commands/` of its own.
 * A workspace command of the same name wins over the bundled one.
 */

import * as fs from "fs";
import * as path from "path";

// `agent` is a one-shot LLM call against an agent prompt — no harness session,
// no stage validation. Used for low-frequency dev tools like /pipeline-builder
// where the 4-agent ceremony is overkill.
export type SlashAction = "pipeline" | "step" | "continue" | "status" | "help" | "agent";

export interface SlashCommand {
  name: string;
  description: string;
  action: SlashAction;
  pipeline?: string;
  agent?: string;
}

const VALID_ACTIONS: ReadonlySet<string> = new Set(["pipeline", "step", "continue", "status", "help", "agent"]);

function asRootList(roots: string | string[]): string[] {
  return Array.isArray(roots) ? roots.filter(r => r && r.length > 0) : [roots];
}

function parseFrontmatter(text: string): Record<string, string> | null {
  if (!text.startsWith("---")) { return null; }
  const end = text.indexOf("\n---", 3);
  if (end === -1) { return null; }
  const block = text.slice(3, end).trim();
  const result: Record<string, string> = {};
  for (const rawLine of block.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) { continue; }
    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) { continue; }
    const key = line.slice(0, colonIdx).trim();
    let value = line.slice(colonIdx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    result[key] = value;
  }
  return result;
}

function commandsDir(root: string): string {
  return path.join(root, ".github", "commands");
}

function parseCommandFile(filePath: string, fallbackName: string): SlashCommand | null {
  let text: string;
  try {
    text = fs.readFileSync(filePath, "utf-8");
  } catch {
    return null;
  }
  const fm = parseFrontmatter(text);
  if (!fm) { return null; }
  const action = fm.action;
  if (!action || !VALID_ACTIONS.has(action)) { return null; }
  return {
    name: fm.name || fallbackName,
    description: fm.description || "",
    action: action as SlashAction,
    pipeline: fm.pipeline,
    agent: fm.agent,
  };
}

export function loadSlashCommand(roots: string | string[], name: string): SlashCommand | null {
  const safeName = name.replace(/[^a-z0-9_-]/gi, "");
  if (!safeName) { return null; }
  for (const root of asRootList(roots)) {
    const filePath = path.join(commandsDir(root), `${safeName}.md`);
    const cmd = parseCommandFile(filePath, safeName);
    if (cmd) { return cmd; }
  }
  return null;
}

export function listSlashCommands(roots: string | string[]): SlashCommand[] {
  const seen = new Map<string, SlashCommand>();
  for (const root of asRootList(roots)) {
    const dir = commandsDir(root);
    let files: string[];
    try {
      files = fs.readdirSync(dir).filter(f => f.endsWith(".md"));
    } catch {
      continue;
    }
    for (const f of files) {
      const stem = f.replace(/\.md$/, "");
      if (seen.has(stem)) { continue; }   // earlier root wins
      const cmd = parseCommandFile(path.join(dir, f), stem);
      if (cmd) { seen.set(stem, cmd); }
    }
  }
  return Array.from(seen.values());
}
