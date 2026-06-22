/**
 * contextCapCore.ts — vscode-free helper for resolving the per-pipeline
 * `context_cap:` field out of a pipeline.yaml file.
 *
 * Companion to contextCap.ts (the vscode shell). Lives split so the
 * file-system + regex bits can be unit-tested without a vscode runtime.
 *
 * We don't import a YAML parser on the TS side (the Python harness owns
 * yaml parsing; TS receives structured data via MCP). For a single
 * numeric field at the top level we use a narrow regex — same approach
 * used for slash-command frontmatter in slashCommands.ts. If the value
 * doesn't match the canonical shape, we fall through and the next
 * resolution layer (VS Code setting → default) takes over.
 */

import * as fs from "fs";
import * as path from "path";

/**
 * Top-level `context_cap: <positive integer>` line in pipeline.yaml.
 * Anchored to start-of-line so a nested `context_cap:` inside an
 * agents[].something block doesn't match. Allows trailing whitespace
 * + comment.
 */
const CONTEXT_CAP_LINE = /^context_cap:\s*(\d+)\s*(?:#.*)?$/m;

/**
 * Read `context_cap:` from `<root>/.github/pipelines/<pipelineName>/pipeline.yaml`.
 * Returns the integer value, or null when the file is missing, the field
 * isn't declared, or the value doesn't parse. Multiple roots are tried in
 * order — first hit wins (workspace > extension bundle).
 */
export function resolvePipelineContextCap(
  roots: readonly string[],
  pipelineName: string,
): number | null {
  const trimmed = (pipelineName || "").trim();
  // Reject path-traversal or path-separator patterns outright rather than
  // silently sanitising — the caller is in-extension code that should pass
  // a clean pipeline directory name, so a dirty input is a bug to surface
  // (as null), not something to fix up.
  if (!trimmed || !/^[a-z0-9_-]+$/i.test(trimmed)) { return null; }
  for (const root of roots) {
    if (!root) { continue; }
    const file = path.join(root, ".github", "pipelines", trimmed, "pipeline.yaml");
    let text: string;
    try {
      text = fs.readFileSync(file, "utf-8");
    } catch {
      continue;
    }
    const match = text.match(CONTEXT_CAP_LINE);
    if (!match) { continue; }
    const n = parseInt(match[1], 10);
    if (!Number.isFinite(n) || n <= 0) { continue; }
    return n;
  }
  return null;
}
