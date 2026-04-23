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
 */

import * as fs from "fs";
import * as path from "path";

export type SlashAction = "pipeline" | "step" | "continue" | "status" | "help";

export interface SlashCommand {
  name: string;
  description: string;
  action: SlashAction;
  pipeline?: string;
  agent?: string;
}

const VALID_ACTIONS: ReadonlySet<string> = new Set(["pipeline", "step", "continue", "status", "help"]);

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

function commandsDir(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".github", "commands");
}

export function loadSlashCommand(workspaceRoot: string, name: string): SlashCommand | null {
  const safeName = name.replace(/[^a-z0-9_-]/gi, "");
  if (!safeName) { return null; }
  const filePath = path.join(commandsDir(workspaceRoot), `${safeName}.md`);
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
    name: fm.name || safeName,
    description: fm.description || "",
    action: action as SlashAction,
    pipeline: fm.pipeline,
    agent: fm.agent,
  };
}

export function listSlashCommands(workspaceRoot: string): SlashCommand[] {
  const dir = commandsDir(workspaceRoot);
  let files: string[];
  try {
    files = fs.readdirSync(dir).filter(f => f.endsWith(".md"));
  } catch {
    return [];
  }
  const commands: SlashCommand[] = [];
  for (const f of files) {
    const cmd = loadSlashCommand(workspaceRoot, f.replace(/\.md$/, ""));
    if (cmd) { commands.push(cmd); }
  }
  return commands;
}
