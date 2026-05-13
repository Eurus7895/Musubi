/**
 * codeReviewInput.ts — pure helpers for /code-review's input layer.
 *
 * Kept free of vscode imports so they can be unit-tested without spinning
 * up the extension host. pipeline.ts re-exports both.
 */

import { spawnSync } from "child_process";

// ── Unified diff slicer ──────────────────────────────────────────────────
// Take a multi-file unified diff and return just the hunks for one path.
// Used by the reviewer-aux fan-out so each sub-agent sees only the file
// it's responsible for. Cheap parser; doesn't try to validate diff syntax.

export function extractFileDiff(fullDiff: string, filePath: string): string {
  if (!fullDiff) { return ""; }
  // Unified diff sections start with `diff --git a/<path> b/<path>` (git format)
  // or `--- a/<path>` (plain format). Walk lines and accumulate the section
  // whose header path matches.
  const lines = fullDiff.split("\n");
  const sections: string[][] = [];
  let cur: string[] | null = null;
  for (const line of lines) {
    if (line.startsWith("diff --git ") || (line.startsWith("--- ") && cur === null)) {
      if (cur) { sections.push(cur); }
      cur = [line];
    } else if (cur) {
      cur.push(line);
    }
  }
  if (cur) { sections.push(cur); }

  for (const section of sections) {
    const header = section[0] ?? "";
    if (header.startsWith("diff --git ")) {
      // git format: `diff --git a/PATH b/PATH`
      const m = header.match(/^diff --git a\/(.+) b\/(.+)$/);
      if (m && (m[1] === filePath || m[2] === filePath)) {
        return section.join("\n");
      }
    } else if (header.startsWith("--- ")) {
      // plain format: --- a/PATH then +++ b/PATH on the next line
      const aPath = header.replace(/^--- (a\/)?/, "");
      if (aPath === filePath) {
        return section.join("\n");
      }
    }
  }
  return "";
}

// ── /code-review input resolution ────────────────────────────────────────

export interface CodeReviewInput {
  /** Resolved unified diff text. */
  diff: string;
  /** Branch (or PR ref) that was diffed. */
  ref: string;
  /** Base ref the diff was taken against. */
  base: string;
  /** True iff diff is empty (user invoked on an up-to-date branch). */
  empty: boolean;
}

export interface CodeReviewResolveError {
  error: string;
  hint?: string;
}

export type CodeReviewResolveResult = CodeReviewInput | CodeReviewResolveError;

export function isResolveError(
  r: CodeReviewResolveResult,
): r is CodeReviewResolveError {
  return typeof (r as CodeReviewResolveError).error === "string";
}

/**
 * Resolve `/code-review` slash command input to a diff.
 *
 * - `feat/branch` → `git diff origin/dev...feat/branch` (or `origin/main`
 *   fallback, then `HEAD~1`).
 * - `#NN`         → not yet supported; returns a typed error so the runner
 *                   can surface it as "use branch form for now." PR-number
 *                   resolution via the GitHub MCP server is a follow-up.
 * - empty input   → typed error.
 */
export function resolveCodeReviewInput(
  rawInput: string, workspaceRoot: string,
): CodeReviewResolveResult {
  const input = (rawInput ?? "").trim();
  if (!input) {
    return {
      error: "Empty input.",
      hint: "Usage: /code-review <branch-name>",
    };
  }
  if (input.startsWith("#")) {
    return {
      error: `PR-number input (${input}) not yet supported in this PR.`,
      hint:
        "Pass a branch name instead: /code-review <branch>. " +
        "PR-number resolution via the GitHub MCP server ships in a follow-up.",
    };
  }
  // Branch form. Try origin/dev as the base, then origin/main, then HEAD~1.
  const candidates = ["origin/dev", "origin/main", "HEAD~1"];
  for (const base of candidates) {
    const result = spawnSync(
      "git",
      ["diff", `${base}...${input}`],
      { cwd: workspaceRoot, encoding: "utf-8", maxBuffer: 50 * 1024 * 1024 },
    );
    if (result.status === 0) {
      const diff = (result.stdout ?? "").trim();
      return { diff, ref: input, base, empty: diff.length === 0 };
    }
    // Common reason a candidate fails: that base ref doesn't exist locally.
    // Keep trying the next candidate.
  }
  return {
    error: `Could not diff '${input}' against origin/dev, origin/main, or HEAD~1.`,
    hint:
      "Check that the branch exists locally (`git branch -a | grep " + input + "`) " +
      "and that you have at least one of: origin/dev, origin/main.",
  };
}
