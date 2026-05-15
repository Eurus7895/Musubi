/**
 * codeReviewInput.ts — pure helpers for /code-review's input layer.
 *
 * Kept free of vscode imports so they can be unit-tested without spinning
 * up the extension host. pipeline.ts re-exports both.
 */

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

// ── Synthetic-diff config (Phase H.1, codebase-as-tree mode) ──────────────
// When /code-review is invoked on a clean working tree, build a synthetic
// diff that treats every tracked file as new content (entirely +lines).
// Caps protect against runaway: a 5000-file repo would otherwise blow the
// LM context.

/** Max files to include in the synthetic tree diff. */
export const TREE_MODE_MAX_FILES = 200;
/** Max total lines (across all files) to include. Soft cap; overshoots by one file. */
export const TREE_MODE_MAX_TOTAL_LINES = 10_000;
/** Files larger than this are summarised by header line only (no content). */
export const TREE_MODE_MAX_LINES_PER_FILE = 500;

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

// ── Tree-mode synthetic diff ─────────────────────────────────────────────

const _BINARY_EXT = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
  ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
  ".exe", ".dll", ".so", ".dylib", ".o", ".obj", ".class", ".jar",
  ".woff", ".woff2", ".ttf", ".otf", ".eot",
  ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".ogg",
  ".db", ".sqlite", ".sqlite3",
]);

function _trackedFiles(workspaceRoot: string): string[] {
  // -c: cached (committed) + -o: untracked, with --exclude-standard so
  // gitignored files are skipped. Catches the working tree exactly.
  const result = spawnSync(
    "git",
    ["ls-files", "-co", "--exclude-standard"],
    { cwd: workspaceRoot, encoding: "utf-8", maxBuffer: 50 * 1024 * 1024 },
  );
  if (result.status !== 0) { return []; }
  return (result.stdout ?? "")
    .split("\n")
    .map(s => s.trim())
    .filter(s => s.length > 0);
}

/**
 * Build a synthetic unified diff for the entire working tree, where every
 * file appears as new content (entirely +lines). Used by /code-review's
 * no-args / clean-tree path so the existing scoper → finder → synthesizer
 * pipeline can run against the codebase as-is.
 *
 * Cuts:
 *   - non-tracked / gitignored files (via `git ls-files -co --exclude-standard`)
 *   - binaries (by extension)
 *   - sections beyond TREE_MODE_MAX_FILES
 *   - file content beyond TREE_MODE_MAX_LINES_PER_FILE (header only emitted)
 *   - total lines beyond TREE_MODE_MAX_TOTAL_LINES (soft cap)
 *
 * Returns the diff text plus a count of how many files were included
 * (for the user-facing message).
 */
export function buildTreeModeDiff(
  workspaceRoot: string,
): { diff: string; filesIncluded: number; filesSkipped: number } {
  const allFiles = _trackedFiles(workspaceRoot);
  const sections: string[] = [];
  let totalLines = 0;
  let included = 0;
  let skipped = 0;

  for (const rel of allFiles) {
    if (included >= TREE_MODE_MAX_FILES) { skipped++; continue; }
    if (totalLines >= TREE_MODE_MAX_TOTAL_LINES) { skipped++; continue; }

    const ext = path.extname(rel).toLowerCase();
    if (_BINARY_EXT.has(ext)) { skipped++; continue; }

    const abs = path.join(workspaceRoot, rel);
    let content: string;
    try {
      const stat = fs.statSync(abs);
      if (!stat.isFile()) { skipped++; continue; }
      content = fs.readFileSync(abs, "utf-8");
    } catch {
      skipped++;
      continue;
    }
    // Detect binary by a NUL byte in the first 8KB (most binaries have one).
    if (content.indexOf("\x00", 0) >= 0 && content.indexOf("\x00", 0) < 8192) {
      skipped++;
      continue;
    }

    const lines = content.split("\n");
    const trimmed = lines.length > TREE_MODE_MAX_LINES_PER_FILE;
    const lineCount = trimmed ? 0 : lines.length;

    const header = [
      `diff --git a/${rel} b/${rel}`,
      `new file mode 100644`,
      `--- /dev/null`,
      `+++ b/${rel}`,
    ];
    if (trimmed) {
      // Don't include content for huge files — emit a header-only stub
      // so the scoper still sees the file path + can reason about it.
      header.push(`@@ -0,0 +0,0 @@`);
      header.push(
        `+(file body omitted: ${lines.length} lines > ` +
        `${TREE_MODE_MAX_LINES_PER_FILE}-line cap. ` +
        `Reviewer-aux can read the file directly via its 'view' tool.)`,
      );
      sections.push(header.join("\n"));
      included++;
      totalLines++;
      continue;
    }

    const hunkHeader = `@@ -0,0 +1,${lines.length} @@`;
    const body = lines.map(l => `+${l}`);
    sections.push([...header, hunkHeader, ...body].join("\n"));
    included++;
    totalLines += lineCount;
  }

  return {
    diff: sections.join("\n"),
    filesIncluded: included,
    filesSkipped: skipped,
  };
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

  // No-args form: review the working tree against HEAD. Captures both
  // staged AND unstaged changes (git diff HEAD includes the index +
  // working-tree deltas). When the tree is clean (no diff vs HEAD),
  // fall through to tree mode — synthesize a diff treating every tracked
  // file as new content so the same pipeline can review the codebase
  // as-is.
  if (!input) {
    const result = spawnSync(
      "git",
      ["diff", "HEAD"],
      { cwd: workspaceRoot, encoding: "utf-8", maxBuffer: 50 * 1024 * 1024 },
    );
    if (result.status === 0) {
      const diff = (result.stdout ?? "").trim();
      if (diff.length > 0) {
        return { diff, ref: "working tree", base: "HEAD", empty: false };
      }
      // Clean tree — synthesize a tree-mode diff.
      const tree = buildTreeModeDiff(workspaceRoot);
      if (tree.filesIncluded === 0) {
        return {
          error: "Working tree is clean and no tracked files found.",
          hint: "Make sure this is a git repo with at least one committed file.",
        };
      }
      return {
        diff: tree.diff,
        ref: `codebase scan (${tree.filesIncluded} files` +
          (tree.filesSkipped > 0 ? `, ${tree.filesSkipped} skipped` : "") + `)`,
        base: "(empty tree)",
        empty: false,
      };
    }
    return {
      error: "Could not run `git diff HEAD` to review the working tree.",
      hint:
        "Make sure this is a git repo with at least one commit. " +
        "Or pass an explicit branch: /code-review <branch-name>.",
    };
  }

  // Natural-language input — anything with whitespace — is the user
  // asking for a codebase scan in prose ("/code-review review this
  // codebase"). The strict rejection was friction; treat it the same as
  // the no-args path (tree mode). The runner surfaces a one-line note
  // about the interpretation so the user can correct if they meant a
  // branch with a typo'd name.
  if (/\s/.test(input)) {
    const result = spawnSync(
      "git",
      ["diff", "HEAD"],
      { cwd: workspaceRoot, encoding: "utf-8", maxBuffer: 50 * 1024 * 1024 },
    );
    if (result.status === 0) {
      const diff = (result.stdout ?? "").trim();
      if (diff.length > 0) {
        return {
          diff,
          ref: `working tree (input "${input}" treated as codebase scan)`,
          base: "HEAD",
          empty: false,
        };
      }
    }
    // Fall through to tree mode on clean tree.
    const tree = buildTreeModeDiff(workspaceRoot);
    if (tree.filesIncluded === 0) {
      return {
        error:
          `/code-review takes a branch name, not a description. ` +
          `Got: ${JSON.stringify(input)}.`,
        hint:
          "Usage: /code-review <branch-name>  (e.g. /code-review feat/login). " +
          "Tried to fall through to a codebase scan but the workspace isn't " +
          "a git repo with tracked files.",
      };
    }
    return {
      diff: tree.diff,
      ref:
        `codebase scan (${tree.filesIncluded} files` +
        (tree.filesSkipped > 0 ? `, ${tree.filesSkipped} skipped` : "") +
        `) — input "${input}" interpreted as scan request`,
      base: "(empty tree)",
      empty: false,
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
    error: `Branch '${input}' not found, or no common base with origin/dev / origin/main.`,
    hint:
      `Check the branch exists locally: git branch -a | grep ${input}. ` +
      `Also confirm at least one of origin/dev or origin/main is fetched.`,
  };
}
