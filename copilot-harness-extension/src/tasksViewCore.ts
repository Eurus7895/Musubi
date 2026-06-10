/**
 * tasksViewCore.ts — pure helpers for the Tasks sidebar tree.
 *
 * No vscode imports so node:test can exercise the tree-shape logic
 * directly. The vscode-using shell (TreeDataProvider, icons, refresh
 * plumbing) lives in tasksView.ts.
 *
 * What's here:
 *   • Row + summary shapes shared between tasksView.ts and tests
 *   • summarizeStages — merges a status snapshot with stage_metrics
 *     rows into per-stage summaries, splitting `code` by chunk when
 *     the chunked-execution branch fired
 *   • Small formatters (timing, token counts) so the sidebar and tests
 *     agree on the strings shown
 */
/**
 * Canonical stage order for the feature-dev pipeline. tasksView.ts
 * mirrors this for backwards-compat; the source of truth is here.
 */
export const STAGE_ORDER: readonly string[] = ["plan", "design", "code", "review"];

/**
 * One row from `harness_query_stage_metrics`. Field names match the
 * SQLite column names so JSON.parse on the MCP response Just Works.
 * `credits` + `model_family` shipped in Stage 1 (MVP A.4); both
 * default to 0/null for pre-Stage-1 rows.
 */
export interface StageMetricsRow {
  stage: string;
  chunk_id: string | null;
  attempt: number;
  started_at: number;
  ended_at: number | null;
  lm_ms: number;
  tokens_in_estimate: number;
  tokens_out_estimate: number;
  credits?: number;
  model_family?: string | null;
}

/** Subset of `harness_get_status().stages[stage]` we actually render. */
export interface StageStatusInfo {
  status: string;
  attempt: number;
}

/** Aggregated summary for one stage. `chunks` is empty when no chunks ran. */
export interface StageSummary {
  stage: string;
  status: string;       // "complete" | "in_progress" | "failed" | "pending" | …
  attempt: number;
  totalLmMs: number;
  totalTokensIn: number;
  totalTokensOut: number;
  totalCredits: number;  // Stage 1 (MVP A.4) — summed from row.credits
  rowCount: number;      // number of stage_metrics rows (≈ runAgentLM invocations)
  chunks: ChunkSummary[];
}

/** Aggregated summary for one chunk under a stage. */
export interface ChunkSummary {
  chunk_id: string;
  attempt: number;
  totalLmMs: number;
  totalTokensIn: number;
  totalTokensOut: number;
  totalCredits: number;  // Stage 1 (MVP A.4)
  rowCount: number;
}

/**
 * Stage 1 (MVP A.4) — live budget snapshot from `snapshotActiveBudget`.
 * Optional argument to `summarizeStages`; when present, the session-
 * level header in the sidebar shows "X / Y credits used" instead of
 * just "X credits used" (the historic path).
 */
export interface BudgetSnapshot {
  creditsUsed: number;
  maxCredits: number;
  remaining: number;
  warnAtRatio: number;
}

/**
 * Stage 1 (MVP A.4) — session-level summary returned alongside
 * StageSummary[]. Encapsulates the active/historic distinction.
 *
 *   - `liveBudget` is non-null iff a `BudgetEnforcer` is currently
 *     registered for this session (active pipeline running).
 *   - `historicCreditsUsed` is the sum across all stage_metrics rows
 *     — works for paused / completed sessions.
 *   - When both are present, `liveBudget.creditsUsed` is authoritative
 *     for the "now" display; historic is the persisted baseline.
 */
export interface SessionSummary {
  sessionId: string;
  status: string;
  totalCredits: number;
  liveBudget: BudgetSnapshot | null;
}

/**
 * Merge `harness_get_status` output with `harness_query_stage_metrics`
 * rows. Always returns one summary per stage in STAGE_ORDER even when
 * the stage hasn't started — keeps the sidebar layout stable.
 */
export function summarizeStages(
  statuses: Record<string, StageStatusInfo>,
  metrics: readonly StageMetricsRow[],
  stageOrder: readonly string[] = STAGE_ORDER,
): StageSummary[] {
  return stageOrder.map(stage => {
    const info = statuses[stage];
    const rows = metrics.filter(r => r.stage === stage);
    const chunks = summarizeChunks(rows);
    const totals = aggregate(rows);
    return {
      stage,
      status: info?.status ?? "pending",
      attempt: info?.attempt ?? 0,
      ...totals,
      rowCount: rows.length,
      chunks,
    };
  });
}

function summarizeChunks(rows: readonly StageMetricsRow[]): ChunkSummary[] {
  const byChunk = new Map<string, StageMetricsRow[]>();
  for (const r of rows) {
    if (!r.chunk_id) { continue; }
    const list = byChunk.get(r.chunk_id) ?? [];
    list.push(r);
    byChunk.set(r.chunk_id, list);
  }
  const out: ChunkSummary[] = [];
  for (const [chunk_id, list] of byChunk) {
    const maxAttempt = Math.max(...list.map(r => r.attempt));
    const totals = aggregate(list);
    out.push({ chunk_id, attempt: maxAttempt, rowCount: list.length, ...totals });
  }
  // Stable sort by chunk_id so a refresh doesn't reorder visible rows.
  out.sort((a, b) => a.chunk_id.localeCompare(b.chunk_id));
  return out;
}

function aggregate(rows: readonly StageMetricsRow[]): {
  totalLmMs: number; totalTokensIn: number; totalTokensOut: number;
  totalCredits: number;
} {
  let totalLmMs = 0, totalTokensIn = 0, totalTokensOut = 0, totalCredits = 0;
  for (const r of rows) {
    totalLmMs += r.lm_ms;
    totalTokensIn += r.tokens_in_estimate;
    totalTokensOut += r.tokens_out_estimate;
    totalCredits += r.credits ?? 0;
  }
  return { totalLmMs, totalTokensIn, totalTokensOut, totalCredits };
}

/**
 * Stage 1 (MVP A.4) — produce the session-level summary used by the
 * sidebar's session header. Combines harness_get_status output (gives
 * us `status` and the persisted `total_credits` sum) with an optional
 * live BudgetEnforcer snapshot.
 *
 * When `liveBudget` is non-null, the sidebar should show the live
 * `creditsUsed` (more up-to-date than persisted rows which only update
 * at end-of-stage). When liveBudget is null, fall back to the
 * persisted `historicCreditsUsed`.
 */
export function summarizeSession(
  sessionId: string,
  status: string,
  historicCreditsUsed: number,
  liveBudget: BudgetSnapshot | null,
): SessionSummary {
  return {
    sessionId,
    status,
    totalCredits: liveBudget?.creditsUsed ?? historicCreditsUsed,
    liveBudget,
  };
}

/** "320ms" / "4.2s" / "1m 12s" — keeps the description column compact. */
export function formatTiming(ms: number): string {
  if (ms <= 0) { return ""; }
  if (ms < 1000) { return `${ms}ms`; }
  if (ms < 60_000) { return `${(ms / 1000).toFixed(1)}s`; }
  const seconds = Math.round(ms / 1000);
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

/** "12.3k" / "412" — token counts shown compactly. */
export function formatTokens(n: number): string {
  if (n <= 0) { return ""; }
  if (n < 1000) { return String(n); }
  return `${(n / 1000).toFixed(1)}k`;
}

/**
 * Stage 1 (MVP A.4) — credits shown to one decimal up to 999.9, then
 * rounded. "12.3c" / "412c" / "1.2kc" — the "c" suffix mirrors the
 * "$" affordance without conflating with cents.
 */
export function formatCredits(n: number): string {
  if (n <= 0) { return ""; }
  if (n < 100) { return `${n.toFixed(1)}c`; }
  if (n < 1000) { return `${Math.round(n)}c`; }
  return `${(n / 1000).toFixed(1)}kc`;
}

/**
 * Description string for a stage row in the tree. Composes status,
 * attempt count, timing, credits, and chunk count into one compact suffix.
 */
export function describeStage(s: StageSummary): string {
  const parts: string[] = [];
  if (s.attempt > 1) { parts.push(`attempt ${s.attempt}`); }
  if (s.totalLmMs > 0) { parts.push(formatTiming(s.totalLmMs)); }
  if (s.totalCredits > 0) { parts.push(formatCredits(s.totalCredits)); }
  if (s.chunks.length > 1) {
    const done = s.chunks.filter(c => c.totalLmMs > 0).length;
    parts.push(`${done}/${s.chunks.length} chunks`);
  }
  return parts.join(" · ");
}

/** Description string for a chunk row. */
export function describeChunk(c: ChunkSummary): string {
  const parts: string[] = [];
  if (c.attempt > 1) { parts.push(`attempt ${c.attempt}`); }
  if (c.totalLmMs > 0) { parts.push(formatTiming(c.totalLmMs)); }
  if (c.totalCredits > 0) { parts.push(formatCredits(c.totalCredits)); }
  return parts.join(" · ");
}

/**
 * Stage 1 (MVP A.4) — session-header description string. Either
 * "12.4 / 50 credits (24%)" when an enforcer is registered, or
 * "12.4 credits used" for paused/historic sessions.
 */
export function describeSession(s: SessionSummary): string {
  if (s.liveBudget) {
    const pct = Math.round(100 * s.liveBudget.creditsUsed / s.liveBudget.maxCredits);
    return `${s.liveBudget.creditsUsed.toFixed(1)} / ${s.liveBudget.maxCredits.toFixed(0)} credits (${pct}%)`;
  }
  if (s.totalCredits > 0) {
    return `${s.totalCredits.toFixed(1)} credits used`;
  }
  return "";
}
