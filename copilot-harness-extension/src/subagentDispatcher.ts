/**
 * subagentDispatcher.ts — Phase G.1.6 heuristic pre-spawn dispatcher.
 *
 * Pipeline stages don't decide themselves whether to spawn sub-agents;
 * the harness pre-spawns based on deterministic rules looking at the
 * design + chunk shape. Decision logic lives here as pure helpers so
 * node:test can exercise it without vscode; the dispatch shell that
 * actually fires spawns lives in `subagentDispatcherRun.ts`.
 *
 * Two pre-spawn types ship in G.1.6:
 *
 * 1. **Pre-coder explorer scan** — before the coder writes a chunk,
 *    spawn explorer with a brief asking for existing imports/callers
 *    of the chunk's files. The summary lands in coder context as
 *    `existing_callers_summary`. Triggered ONLY when the chunk
 *    contains at least one file that already exists on disk; new-only
 *    chunks have nothing to scan for.
 *
 * 2. **Pre-reviewer per-file reviewer-aux** — when a chunk has > 2
 *    modules, fire one reviewer-aux per file with the per-file
 *    checklist. Aggregate verdicts feed the main reviewer's context
 *    as `per_file_review_findings`. Capped at PER_FILE_REVIEW_MAX
 *    files so a 30-module chunk doesn't fan out into 30 spawns.
 *
 * The roadmap's G.1 acceptance criterion is: "feature-dev's `coder`
 * stage spawns an `explorer` sub-agent for a 'find callers of X'
 * lookup; the summary lands in the coder's next prompt; the audit DB
 * shows the spawn + completion rows." That's exactly what (1) does.
 */

import type { SubagentRoleId } from "./runners/subagentRunnerCore";

// ── Caps + thresholds ──────────────────────────────────────────────────

/**
 * Above this module count in a chunk, fire reviewer-aux per file.
 * Tuned to: trivial 1-2 file chunks don't need extra review (the main
 * reviewer is cheap on a tiny surface); 3+ file chunks benefit from a
 * focused per-file pass that the main reviewer can corroborate.
 */
export const PER_FILE_REVIEW_MIN_MODULES = 3;

/**
 * Hard cap on per-file reviewer-aux spawns per chunk attempt. A
 * 30-module chunk shouldn't fan out into 30 spawns — diminishing
 * returns + budget pressure. Files past the cap get covered by the
 * main reviewer alone.
 */
export const PER_FILE_REVIEW_MAX = 5;

/** Max chars of the explorer brief, to stay well under the 2000-token role cap. */
export const EXPLORER_BRIEF_MAX_CHARS = 1500;

// ── Spawn descriptors ──────────────────────────────────────────────────

export interface PreSpawnDescriptor {
  /** Sub-agent role to spawn. */
  role: SubagentRoleId;
  /** One-sentence brief the sub-agent sees verbatim. */
  brief: string;
  /**
   * Optional spawn-time tool narrowing. Subset of the role's policy
   * allow-list — pass-through to harness_spawn_subagent.
   */
  allowedTools?: readonly string[];
  /**
   * Where the sub-agent's summary lands in the parent stage's read
   * context. The dispatcher splices results in under this key (or
   * appends, when multiple sub-agents share a key).
   */
  contextKey: string;
  /**
   * Diagnostic label rendered in chat for observability. e.g.
   * "explorer: scan T1 imports".
   */
  label: string;
}

// ── Heuristic input ────────────────────────────────────────────────────

/**
 * The subset of design + chunk + workspace state the dispatcher reads.
 * Kept tight so tests can construct synthetic inputs without spinning
 * up the full pipeline.
 */
export interface DispatcherInput {
  /**
   * Stage about to start ("coder" | "reviewer"). Other stages
   * (planner, designer) get no pre-spawns.
   */
  stage: "planner" | "designer" | "coder" | "reviewer";
  /**
   * Chunked execution: the chunk's file paths (relative to workspace).
   * Empty when running non-chunked — the dispatcher falls back to
   * `design.modules[].file` in that case.
   */
  chunkFilePaths: ReadonlyArray<string>;
  /** Chunk identifier, for label rendering. NULL when non-chunked. */
  chunkId: string | null;
  /**
   * Filtered design (modules already trimmed to chunk in the chunked
   * path; full design otherwise). Used to extract symbol names for
   * the explorer's brief.
   */
  design: Record<string, unknown> | null;
  /**
   * For each path in `chunkFilePaths`, true if the file already
   * exists on disk. The pre-coder explorer scan is meaningful only
   * for files with prior callers — new-only chunks skip it.
   */
  fileExistsOnDisk: ReadonlyMap<string, boolean>;
  /**
   * Available spawn budget for this stage attempt. The dispatcher
   * may emit fewer than the heuristic suggests if the budget would
   * be breached.
   */
  remainingBudget: number;
}

// ── Public API ──────────────────────────────────────────────────────────

/**
 * Compute the list of sub-agents the dispatcher should fire before the
 * given stage runs. Returns [] when no pre-spawn is warranted.
 *
 * Determinism: same input ⇒ same output. The heuristics are pure
 * functions of design / chunk shape + workspace existence flags, so
 * tests can pin behaviour without mocking the LM or harness.
 */
export function decidePreSpawns(input: DispatcherInput): PreSpawnDescriptor[] {
  if (input.remainingBudget <= 0) { return []; }
  switch (input.stage) {
    case "coder":    return decideCoderPreSpawns(input);
    case "reviewer": return decideReviewerPreSpawns(input);
    default:         return [];  // planner / designer never pre-spawn
  }
}

// ── Coder heuristic: explorer scan for existing callers ────────────────

function decideCoderPreSpawns(input: DispatcherInput): PreSpawnDescriptor[] {
  const existingPaths = input.chunkFilePaths.filter(
    p => input.fileExistsOnDisk.get(p) === true,
  );
  if (existingPaths.length === 0) { return []; }
  if (input.remainingBudget < 1)  { return []; }

  const symbols = extractPublicSymbols(input.design);
  const brief = renderExplorerBrief(existingPaths, symbols, input.chunkId);
  const label = input.chunkId
    ? `explorer: scan callers for chunk ${input.chunkId}`
    : "explorer: scan callers";

  return [{
    role: "explorer",
    brief,
    contextKey: "existing_callers_summary",
    label,
  }];
}

/**
 * Pull every `public_interface[].name` out of the design's modules so
 * the explorer brief can ask for callers of specific symbols, not
 * just file paths. Capped at 20 symbols — beyond that the brief gets
 * unfocused and the explorer's summary becomes a workspace tour.
 */
function extractPublicSymbols(
  design: Record<string, unknown> | null,
): string[] {
  if (!design || typeof design !== "object") { return []; }
  const modules = (design as { modules?: unknown }).modules;
  if (!Array.isArray(modules)) { return []; }
  const out: string[] = [];
  const seen = new Set<string>();
  for (const m of modules) {
    if (!m || typeof m !== "object") { continue; }
    const iface = (m as { public_interface?: unknown }).public_interface;
    if (!Array.isArray(iface)) { continue; }
    for (const entry of iface) {
      if (!entry || typeof entry !== "object") { continue; }
      const name = (entry as { name?: unknown }).name;
      if (typeof name !== "string" || !name.trim()) { continue; }
      const cleaned = name.trim();
      if (seen.has(cleaned)) { continue; }
      seen.add(cleaned);
      out.push(cleaned);
      if (out.length >= 20) { return out; }
    }
  }
  return out;
}

/**
 * Build the explorer's brief text. Lead with the question, then list
 * paths, then list symbols. Truncated at EXPLORER_BRIEF_MAX_CHARS so
 * the prompt + role-skill combination stays inside the role's token
 * budget.
 */
export function renderExplorerBrief(
  existingPaths: ReadonlyArray<string>,
  symbols: ReadonlyArray<string>,
  chunkId: string | null,
): string {
  const chunkLabel = chunkId ? ` for chunk ${chunkId}` : "";
  const lines: string[] = [
    `Find existing imports and callers of the following modules${chunkLabel}, ` +
    `so the coder can produce changes that match the project's existing patterns.`,
    "",
    "Files (already on disk):",
  ];
  for (const p of existingPaths) {
    lines.push(`  - ${p}`);
  }
  if (symbols.length > 0) {
    lines.push("");
    lines.push("Symbols (public interface from design):");
    for (const s of symbols) {
      lines.push(`  - ${s}`);
    }
  }
  lines.push("");
  lines.push(
    "Return: a tight summary covering import sites and call sites. " +
    "Lead with the answer; quote `path:line` for each reference. " +
    "Skip workspace files unrelated to the listed paths or symbols.",
  );
  const out = lines.join("\n");
  if (out.length <= EXPLORER_BRIEF_MAX_CHARS) { return out; }
  return out.slice(0, EXPLORER_BRIEF_MAX_CHARS - 3).trimEnd() + "...";
}

// ── Reviewer heuristic: per-file reviewer-aux ──────────────────────────

function decideReviewerPreSpawns(input: DispatcherInput): PreSpawnDescriptor[] {
  if (input.chunkFilePaths.length < PER_FILE_REVIEW_MIN_MODULES) { return []; }
  const cap = Math.min(input.chunkFilePaths.length, PER_FILE_REVIEW_MAX, input.remainingBudget);
  const out: PreSpawnDescriptor[] = [];
  for (let i = 0; i < cap; i++) {
    const path = input.chunkFilePaths[i];
    out.push({
      role: "reviewer-aux",
      brief: renderReviewerAuxBrief(path, input.chunkId),
      contextKey: "per_file_review_findings",
      label: `reviewer-aux: ${path}`,
    });
  }
  return out;
}

/**
 * Per-file review brief. The main reviewer's checklist still applies;
 * reviewer-aux returns a focused per-file verdict so the main reviewer
 * can corroborate or override.
 */
export function renderReviewerAuxBrief(
  path: string, chunkId: string | null,
): string {
  const chunkLabel = chunkId ? ` (chunk ${chunkId})` : "";
  return [
    `Apply the code-review checklist to ${path}${chunkLabel}.`,
    "",
    "Read the file, then return:",
    "  - verdict: 'pass' | 'fail' | 'needs-attention'",
    "  - issues: list of {severity: 'critical'|'high'|'medium'|'low', description, fix_instruction}",
    "",
    "Severity policy: critical/high should trigger fail; medium/low can pass with notes.",
  ].join("\n");
}

// ── Result aggregation ────────────────────────────────────────────────

export interface PreSpawnResult {
  /** Echo of the descriptor used at spawn time. */
  descriptor: PreSpawnDescriptor;
  /** Verified summary returned by the harness, or null on failure. */
  summary: string | null;
  /**
   * Final status from `harness_complete_subagent`. Populated even on
   * partial failures so the audit trail is complete.
   */
  finalStatus: string | null;
  /** Human-readable failure reason, when ok=false. */
  reason?: string;
}

/**
 * Splice pre-spawn results into the parent stage's read context. The
 * caller passes the fresh context (already produced by
 * `harness_read_stage`) and gets back a context with summaries
 * attached under each descriptor's `contextKey`.
 *
 * Multiple results sharing a contextKey are joined as a list of
 * `{label, summary}` so the agent can see which file each finding
 * belongs to. A single result keeps the simpler shape `{label, summary}`.
 */
export function spliceResultsIntoContext(
  baseContext: Record<string, unknown>,
  results: ReadonlyArray<PreSpawnResult>,
): Record<string, unknown> {
  if (results.length === 0) { return baseContext; }
  const out = { ...baseContext };
  // Group by contextKey.
  const byKey = new Map<string, PreSpawnResult[]>();
  for (const r of results) {
    if (!r.summary) { continue; }
    const key = r.descriptor.contextKey;
    const list = byKey.get(key);
    if (list) { list.push(r); } else { byKey.set(key, [r]); }
  }
  for (const [key, list] of byKey) {
    if (list.length === 1) {
      out[key] = {
        label: list[0].descriptor.label,
        summary: list[0].summary,
      };
    } else {
      out[key] = list.map(r => ({
        label: r.descriptor.label,
        summary: r.summary,
      }));
    }
  }
  return out;
}
