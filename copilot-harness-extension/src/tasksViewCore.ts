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
  rowCount: number;     // number of stage_metrics rows (≈ runAgentLM invocations)
  chunks: ChunkSummary[];
}

/** Aggregated summary for one chunk under a stage. */
export interface ChunkSummary {
  chunk_id: string;
  attempt: number;
  totalLmMs: number;
  totalTokensIn: number;
  totalTokensOut: number;
  rowCount: number;
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
} {
  let totalLmMs = 0, totalTokensIn = 0, totalTokensOut = 0;
  for (const r of rows) {
    totalLmMs += r.lm_ms;
    totalTokensIn += r.tokens_in_estimate;
    totalTokensOut += r.tokens_out_estimate;
  }
  return { totalLmMs, totalTokensIn, totalTokensOut };
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
 * Description string for a stage row in the tree. Composes status,
 * attempt count, timing, and chunk count into one compact suffix.
 */
export function describeStage(s: StageSummary): string {
  const parts: string[] = [];
  if (s.attempt > 1) { parts.push(`attempt ${s.attempt}`); }
  if (s.totalLmMs > 0) { parts.push(formatTiming(s.totalLmMs)); }
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
  return parts.join(" · ");
}
