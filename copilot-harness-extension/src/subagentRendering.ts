/**
 * Sub-agent chat marker rendering (Phase A.3).
 *
 * Two responsibilities:
 *
 *   1. Pure formatters that turn an audit row into a one-line chat marker.
 *      Kept side-effect-free so the agent runner (Phase B) and the
 *      pipeline runner can render markers however they want.
 *
 *   2. SubagentEventTracker — polls `harness_query_subagent_events` and
 *      yields rows newer than the last seen ts. Polling is the contract
 *      until the FastMCP-side push (currently deferred) lands; once it
 *      does, callers can subscribe to McpClient.onNotification instead.
 *
 * No vscode imports — this module is unit-testable as plain TS.
 */

export type SubagentEventKind = "spawned" | "completed";

export interface SubagentSpawnEvent {
  ts: number;
  handle_id: string;
  parent_session_id: string;
  parent_agent_name: string;
  role: string;
  brief: string;
  event: "spawned";
  allowed_tools: string[] | null;
  max_turns: number | null;
  wall_clock_timeout_s: number | null;
}

export interface SubagentCompleteEvent {
  ts: number;
  handle_id: string;
  parent_session_id: string;
  parent_agent_name: string;
  role: string;
  brief: string;
  event: "completed";
  final_status: "done" | "failed" | "escalated" | "abandoned";
  escalated: boolean;
  turns: number;
  tools_used: string[] | null;
  summary_truncated: boolean;
  verification_errors: string[] | null;
}

export type SubagentEvent = SubagentSpawnEvent | SubagentCompleteEvent;

const BRIEF_MAX = 80;

function truncateBrief(brief: string): string {
  if (brief.length <= BRIEF_MAX) { return brief; }
  return brief.slice(0, BRIEF_MAX - 1) + "…";
}

function shortHandle(handle_id: string): string {
  return handle_id.slice(0, 8);
}

function toolHistogram(tools: readonly string[]): string {
  const counts = new Map<string, number>();
  for (const t of tools) {
    counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  const parts: string[] = [];
  for (const [name, n] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
    parts.push(n === 1 ? name : `${name}×${n}`);
  }
  return parts.join(", ");
}

export function formatSpawnMarker(e: SubagentSpawnEvent): string {
  return `▶ **${e.role}** \`${shortHandle(e.handle_id)}\` — ${truncateBrief(e.brief)}`;
}

export function formatCompleteMarker(e: SubagentCompleteEvent): string {
  const icon =
    e.final_status === "done" ? "✓"
    : e.escalated ? "⚠"
    : "✗";
  const bits: string[] = [`${e.turns} turn${e.turns === 1 ? "" : "s"}`];
  if (e.tools_used && e.tools_used.length > 0) {
    bits.push(toolHistogram(e.tools_used));
  }
  if (e.summary_truncated) {
    bits.push("summary truncated");
  }
  if (e.verification_errors && e.verification_errors.length > 0) {
    bits.push(`verify: ${e.verification_errors.join("; ")}`);
  }
  const status = e.escalated ? `escalated (${e.final_status})` : e.final_status;
  return `${icon} **${e.role}** \`${shortHandle(e.handle_id)}\` — ${status} · ${bits.join(" · ")}`;
}

export function formatMarker(e: SubagentEvent): string {
  return e.event === "spawned" ? formatSpawnMarker(e) : formatCompleteMarker(e);
}

/**
 * Minimal client surface the tracker needs — accepts the real McpClient
 * or a test stub.
 */
export interface SubagentEventClient {
  callTool(name: string, args: Record<string, unknown>): Promise<string>;
}

export interface PollResult {
  events: SubagentEvent[];
  markers: string[];
}

export class SubagentEventTracker {
  private sinceTs: number | undefined;

  constructor(
    private readonly client: SubagentEventClient,
    private readonly parentSessionId: string,
    private readonly limit: number = 200,
  ) {}

  /**
   * Fetch events newer than the last seen ts. Advances the cursor to the
   * max ts in the returned batch so the next call only sees new rows.
   * Safe to call repeatedly; returns an empty result when nothing is new.
   */
  async pollOnce(): Promise<PollResult> {
    const args: Record<string, unknown> = {
      parent_session_id: this.parentSessionId,
      limit: this.limit,
    };
    if (this.sinceTs !== undefined) {
      args.since_ts = this.sinceTs;
    }
    const raw = await this.client.callTool("harness_query_subagent_events", args);
    let parsed: { events?: SubagentEvent[] };
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { events: [], markers: [] };
    }
    const events = Array.isArray(parsed.events) ? parsed.events : [];
    if (events.length === 0) {
      return { events: [], markers: [] };
    }
    let maxTs = this.sinceTs ?? 0;
    for (const ev of events) {
      if (ev.ts > maxTs) { maxTs = ev.ts; }
    }
    this.sinceTs = maxTs;
    return { events, markers: events.map(formatMarker) };
  }

  /** Reset cursor — next pollOnce will return all rows for the parent session. */
  reset(): void {
    this.sinceTs = undefined;
  }
}
