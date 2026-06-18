/**
 * harness-tier: ephemeral
 * expires-when: models reliably drive multi-turn tool-use without compensation
 * cost-lever: deletes the orchestrator-core compensation layer
 * (what: Pure orchestrator loop logic (cycle guards, salvage).)
 */
/**
 * runners/orchestratorCore.ts — Orchestrator helpers (Phase B.2 + C.2).
 *
 * Pure helpers (no vscode imports) so they can be unit-tested with
 * node:test. The vscode-using shell lives in runners/orchestrator.ts.
 *
 * What's here:
 *   • Tool definitions (data-only, LanguageModelChatTool-shaped)
 *   • System-prompt assembly + frontmatter stripping
 *   • MCP dispatch from LLM-facing args → MCP-tool args
 *   • SpawnTracker — bookkeeping for outstanding handles per turn
 *   • cleanupOutstandingSubagents — best-effort sweep at turn end
 *   • loadOrchestratorPrompts — disk loader for agent.md + routing skill
 *   • C.2: chat_id resolver, conversation-row parsing, token estimator,
 *     reactive compaction planner.
 */

import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";

import { parseFrontmatterLmTools } from "../modelSelectorCore";

export const ORCHESTRATOR_AGENT_NAME = "orchestrator";

export const ORCHESTRATOR_TOOL_NAMES = [
  "harness_spawn_subagent",
  "harness_await_subagent",
  "harness_list_subagents",
  "harness_get_skill",
] as const;

export type OrchestratorToolName = typeof ORCHESTRATOR_TOOL_NAMES[number];

/**
 * Subset of ORCHESTRATOR_TOOLS gated on LM_FACING_SUBAGENT_ROLES (see
 * orchestrator.ts). When no extension-side runner exists for a sub-agent
 * role, advertising spawn / await / list to the LM just produces a
 * 30 s-wall-clock-then-retry loop, so we hide them. harness_get_skill
 * is always advertised; it works even when no sub-agent runner is wired.
 */
export const ORCHESTRATOR_SUBAGENT_TOOL_NAMES: ReadonlySet<string> = new Set([
  "harness_spawn_subagent",
  "harness_await_subagent",
  "harness_list_subagents",
]);

export interface OrchestratorTool {
  name: OrchestratorToolName;
  description: string;
  inputSchema: Record<string, unknown>;
}

/**
 * LanguageModelChatTool-shaped definitions. Kept as plain data so they can
 * be serialised, snapshot-tested, and passed to either `request.tools` or
 * `vscode.lm.registerTool` without dragging vscode into pure tests.
 */
export const ORCHESTRATOR_TOOLS: readonly OrchestratorTool[] = [
  {
    name: "harness_spawn_subagent",
    description:
      "Spawn a read-only sub-agent (explorer, investigator, reviewer-aux, planner, " +
      "coder, reviewer) with a one-sentence brief. Returns a handle_id you must " +
      "pass to harness_await_subagent. The sub-agent sees only the brief — no " +
      "conversation history, memory, or other sub-agent results.",
    inputSchema: {
      type: "object",
      properties: {
        role: {
          type: "string",
          description: "Sub-agent role from the spawn allow-list.",
        },
        brief: {
          type: "string",
          description: "One-sentence task description. The sub-agent sees only this.",
        },
        allowed_tools: {
          type: "array",
          items: { type: "string" },
          description:
            "Optional intersection with the role's tool set (e.g. ['Read','Grep']). " +
            "Omit to grant the role's full allow-list.",
        },
        output_schema: {
          type: "object",
          description:
            "Optional JSON schema describing the structured payload you expect back.",
        },
      },
      required: ["role", "brief"],
      additionalProperties: false,
    },
  },
  {
    name: "harness_await_subagent",
    description:
      "Block until a spawned sub-agent reaches a terminal state (done, failed, " +
      "escalated, abandoned) or its wall-clock cap fires. Returns the sub-agent's " +
      "summary + structured payload.",
    inputSchema: {
      type: "object",
      properties: {
        handle_id: {
          type: "string",
          description: "handle_id returned by harness_spawn_subagent.",
        },
        max_wait_s: {
          type: "number",
          description: "Optional wait cap in seconds (defaults to 60).",
        },
      },
      required: ["handle_id"],
      additionalProperties: false,
    },
  },
  {
    name: "harness_list_subagents",
    description:
      "List the sub-agent roles you are permitted to spawn, with each role's " +
      "tool allow-list. Call before spawning when unsure which role fits.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
  },
  {
    name: "harness_get_skill",
    description:
      "Fetch the body of an installed skill (e.g. 'orchestrator-routing') when " +
      "you need detailed routing rules, picker tables, or anti-patterns that " +
      "aren't covered by your inline core rules. The harness used to push the " +
      "full skill body into your system prompt every turn (~841 t); now you " +
      "pull on demand. Call this when you need detail about: tool selection, " +
      "pipeline recommendation, sub-agent runners status, anti-patterns, or " +
      "anything else not covered in the agent's inline 'Core rules' section. " +
      "Do NOT call on every turn — most simple Q&A doesn't need it.",
    inputSchema: {
      type: "object",
      properties: {
        skill_id: {
          type: "string",
          description:
            "Skill identifier — directory name under .github/skills/ (e.g. 'orchestrator-routing').",
        },
      },
      required: ["skill_id"],
      additionalProperties: false,
    },
  },
];

/** Maximum number of (LLM round-trip → tool dispatch) cycles per user turn. */
/**
 * Hard ceiling on tool-call cycles per turn. The LM gets at most this
 * many sendRequest rounds; anything beyond is forced to a final answer.
 *
 * Lowered from 8 to 5 after a real session showed the LM thrashing
 * through 8 cycles of empty tool results without producing any useful
 * work. With the no-progress backoff (CONSECUTIVE_EMPTY_CYCLE_LIMIT)
 * also in place, the cap rarely fires anyway — but a tighter ceiling
 * means worst-case token spend per turn is bounded.
 */
export const MAX_TOOL_CYCLES = 5;

/**
 * If this many tool-emitting cycles in a row produce zero useful
 * results (every tool call returned empty content OR failed), bail
 * to the force-final-answer path. Catches the "LM hallucinates a path,
 * tool returns empty, LM tries another path, also empty, ..." loop
 * that wastes both wall-clock and tokens.
 *
 * 2 is the minimum that distinguishes "noisy first attempt → recovery"
 * from "stuck loop". One bad cycle is normal; two in a row is a sign
 * the LM has nothing to anchor on and should ask the user.
 */
export const CONSECUTIVE_EMPTY_CYCLE_LIMIT = 2;

/** Cap on the chat history we replay per turn. Phase C replaces this. */
export const MAX_HISTORY_TURNS = 10;

// ── Frontmatter stripping ────────────────────────────────────────────────────

export function stripFrontmatter(text: string): string {
  if (!text.startsWith("---")) { return text; }
  const end = text.indexOf("\n---", 3);
  if (end === -1) { return text; }
  return text.slice(end + 4).replace(/^\n+/, "");
}

// ── System prompt builder ────────────────────────────────────────────────────

export interface OrchestratorPromptInputs {
  agentMd: string;
  routingSkill: string;
  memoryTier1?: string | null;
  tier2Available?: readonly string[];
}

export function buildOrchestratorSystemPrompt(input: OrchestratorPromptInputs): string {
  const parts: string[] = [stripFrontmatter(input.agentMd).trim()];

  if (input.routingSkill && input.routingSkill.trim().length > 0) {
    parts.push(
      "\n\n## Skill: orchestrator-routing (pushed by harness)\n\n" +
      stripFrontmatter(input.routingSkill).trim()
    );
  }

  const mem = (input.memoryTier1 ?? "").trim();
  if (mem.length > 0) {
    parts.push("\n\n## Project memory (Tier 1)\n\n" + mem);
    if (input.tier2Available && input.tier2Available.length > 0) {
      parts.push(
        "\n\nTier 2 entries available on demand via `harness_get_memory_entry`: " +
        input.tier2Available.join(", ")
      );
    }
  }

  return parts.join("");
}

// ── MCP dispatch ─────────────────────────────────────────────────────────────

export interface ToolDispatchContext {
  parentSessionId: string;
  parentAgentName: string;
}

export interface ToolDispatchClient {
  callTool(name: string, args: Record<string, unknown>, timeoutMs?: number): Promise<string>;
}

/**
 * Dispatch a single orchestrator tool call to the harness. Translates the
 * LLM-facing argument shape into the MCP-tool argument shape and returns
 * the raw JSON string the MCP tool produced. The caller is responsible
 * for feeding it back to the model.
 */
export async function dispatchOrchestratorTool(
  client: ToolDispatchClient,
  ctx: ToolDispatchContext,
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  switch (toolName) {
    case "harness_spawn_subagent": {
      const out: Record<string, unknown> = {
        parent_session_id: ctx.parentSessionId,
        parent_agent_name: ctx.parentAgentName,
        role: args.role,
        brief: args.brief,
      };
      if (Array.isArray(args.allowed_tools)) {
        out.allowed_tools = args.allowed_tools;
      }
      if (args.output_schema && typeof args.output_schema === "object") {
        out.output_schema = JSON.stringify(args.output_schema);
      }
      return client.callTool("harness_spawn_subagent", out);
    }
    case "harness_await_subagent": {
      const out: Record<string, unknown> = {
        handle_id: args.handle_id,
      };
      // 30 s default — long enough for a real sub-agent run, short enough
      // that a hung sub-agent surfaces fast instead of holding up the turn
      // for the server's 300 s wall-clock cap.
      const maxWaitS = typeof args.max_wait_s === "number" ? args.max_wait_s : 30;
      out.max_wait_s = maxWaitS;
      // Client-side timeout must exceed the server-side wait, plus a small
      // grace window for the server to write its terminal response.
      return client.callTool("harness_await_subagent", out, (maxWaitS + 5) * 1000);
    }
    case "harness_list_subagents":
      return client.callTool("harness_list_subagents", {
        main_agent_name: ctx.parentAgentName,
      });
    case "harness_get_skill": {
      // Fill in agent_name from the dispatch context so the LM doesn't
      // have to hand-author it (and can't accidentally request a skill
      // not on the orchestrator's allowlist by passing a different name).
      const skillId = typeof args.skill_id === "string" ? args.skill_id : "";
      return client.callTool("harness_get_skill", {
        skill_id: skillId,
        agent_name: ctx.parentAgentName,
      });
    }
    default:
      throw new Error(`unknown orchestrator tool: ${toolName}`);
  }
}

// ── Spawn tracker (turn-end cleanup bookkeeping) ─────────────────────────────

export class SpawnTracker {
  private readonly outstanding = new Set<string>();
  private readonly roleByHandle = new Map<string, string>();

  recordSpawn(
    toolName: string, mcpResultJson: string, role?: string,
  ): void {
    if (toolName !== "harness_spawn_subagent") { return; }
    try {
      const parsed = JSON.parse(mcpResultJson) as { handle_id?: string };
      if (typeof parsed.handle_id === "string") {
        this.outstanding.add(parsed.handle_id);
        if (role) { this.roleByHandle.set(parsed.handle_id, role); }
      }
    } catch {
      // server returned non-JSON; nothing to track
    }
  }

  recordAwait(mcpResultJson: string): void {
    try {
      const parsed = JSON.parse(mcpResultJson) as {
        status?: string; handle_id?: string;
      };
      if (parsed.status === "recorded" && typeof parsed.handle_id === "string") {
        this.outstanding.delete(parsed.handle_id);
      }
    } catch {
      // ignore
    }
  }

  outstandingHandles(): string[] {
    return [...this.outstanding];
  }

  /** Return the spawn role recorded for `handleId`, or null if unknown. */
  roleFor(handleId: string): string | null {
    return this.roleByHandle.get(handleId) ?? null;
  }
}

/**
 * Best-effort sweep at user-turn-end. Marks any sub-agent the orchestrator
 * spawned-but-never-awaited as 'abandoned'. Errors are swallowed — this
 * is housekeeping, not correctness.
 */
export async function cleanupOutstandingSubagents(
  client: ToolDispatchClient,
  tracker: SpawnTracker,
): Promise<void> {
  for (const handle of tracker.outstandingHandles()) {
    try {
      await client.callTool("harness_complete_subagent", {
        handle_id: handle,
        status: "abandoned",
        summary: "[harness] orchestrator turn ended before await",
        turns: 0,
      });
    } catch {
      // ignore — best-effort cleanup
    }
  }
}

// ── Disk loaders ─────────────────────────────────────────────────────────────

function readFirstExisting(roots: string[], rel: string): string | null {
  for (const root of roots) {
    const p = path.join(root, rel);
    try { return fs.readFileSync(p, "utf-8"); } catch { /* keep looking */ }
  }
  return null;
}

export interface LoadedPrompts {
  agentMd: string;
  routingSkill: string;
  /** Concrete VS Code LM tool names parsed from the agent.md frontmatter
   *  `lm_tools:` field. Empty array when the field is missing — callers
   *  treat that as "advertise no external tools to the LM."
   */
  lmTools: string[];
}

/** Load orchestrator agent.md + routing skill from .github/. */
export function loadOrchestratorPrompts(roots: string[]): LoadedPrompts {
  const agentMd = readFirstExisting(roots, path.join(".github", "agents", "orchestrator.agent.md"))
    ?? "";
  const routingSkill = readFirstExisting(
    roots, path.join(".github", "skills", "orchestrator-routing", "SKILL.md"),
  ) ?? "";
  const lmTools = parseFrontmatterLmTools(agentMd);
  return { agentMd, routingSkill, lmTools };
}

// ── Phase C.2: chat_id, conversation rows, token budget, compaction ─────────

/** Mirror of validation/verifier._CHARS_PER_TOKEN — must stay in sync. */
export const CHARS_PER_TOKEN = 4;

/**
 * Single-call estimate; same heuristic as conversations.estimate_tokens
 * on the harness side so budget math agrees across the boundary.
 */
export function estimateTokens(text: string): number {
  if (!text) { return 0; }
  return Math.max(1, Math.floor(text.length / CHARS_PER_TOKEN));
}

/**
 * Assumed model context window. Sonnet/Opus 4.x land at 200k; the value
 * is intentionally a constant rather than read off the live model so
 * `planCompaction` stays a pure function. Tune here if a smaller model
 * is wired in.
 */
export const MODEL_CONTEXT_TOKENS = 200_000;

/**
 * Phase J.5 — default ceiling for the per-turn context budget when the
 * user hasn't set `copilotHarness.contextCap` and the pipeline.yaml
 * doesn't declare its own `context_cap:`. Tuned for cost rather than
 * quality: at 50k input tokens on Sonnet 4.6 ($3/M input, $0.30/M
 * cached), a single uncached turn costs ~15 credits — fits ~125 turns
 * in the 1900-credit monthly budget. Increase if your typical
 * conversation needs more history; the cap can go up to
 * MODEL_CONTEXT_TOKENS.
 */
export const DEFAULT_CONTEXT_CAP = 50_000;

/** Reactive compaction thresholds (Claude Code pattern). */
export const COMPACT_T1_DROP_TOOLS = 0.80;
export const COMPACT_T2_SUMMARIZE  = 0.90;
export const COMPACT_T3_TRUNCATE   = 0.99;

/**
 * Plain in-memory shape for a conversation row. Mirrors the JSON the
 * harness's `harness_get_conversation` returns; deliberately vscode-free
 * so this module stays unit-testable.
 */
export interface OrchestratorMessage {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  id?: number;
  ts?: string;
}

/**
 * Parse the JSON envelope returned by harness_get_conversation. Returns an
 * empty array on any malformed input — callers may proceed with no
 * replayed history rather than crash a turn over a parser blip.
 */
export function parseConversationResponse(raw: string): OrchestratorMessage[] {
  if (!raw) { return []; }
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return []; }
  if (!parsed || typeof parsed !== "object") { return []; }
  const obj = parsed as { status?: string; messages?: unknown };
  if (obj.status !== "ok" || !Array.isArray(obj.messages)) { return []; }
  const out: OrchestratorMessage[] = [];
  for (const m of obj.messages) {
    if (!m || typeof m !== "object") { continue; }
    const row = m as { role?: unknown; content?: unknown; id?: unknown; ts?: unknown };
    if (typeof row.role !== "string" || typeof row.content !== "string") { continue; }
    if (row.role !== "user" && row.role !== "assistant"
        && row.role !== "tool" && row.role !== "system") { continue; }
    out.push({
      role: row.role,
      content: row.content,
      id: typeof row.id === "number" ? row.id : undefined,
      ts: typeof row.ts === "string" ? row.ts : undefined,
    });
  }
  return out;
}

/**
 * Stable per-chat identifier across turns within a single chat panel.
 *
 * VS Code 1.93's ChatRequest exposes no thread id; the only stable
 * proxy we have is the first user prompt in `chatContext.history`,
 * combined with the participant id and workspace path. On the very
 * first turn (history empty) we fall back to the current prompt —
 * which becomes `chatContext.history[0]` on turn 2, so the hash stays
 * stable. Truncated SHA-256 (16 hex chars) keeps the chat_id short
 * enough for SQLite indexes while leaving collision space generous.
 *
 * `sessionSalt` is a per-extension-activation random string that the
 * runner mints once when activate() runs. Including it means: identical
 * first prompts in two separate chat panels (or two separate VS Code
 * sessions) hash to DIFFERENT chat_ids, so a brand-new chat doesn't
 * silently inherit the prior chat's history. Multi-turn within the
 * same activation still hashes the same because the salt is unchanged.
 *
 * Trade-off: closing and reopening VS Code resets the salt and hence
 * abandons the prior chat's replay. Conversation rows stay in the DB
 * (orphaned) but won't be loaded. That matches the user expectation of
 * "new VS Code session = new chat state."
 *
 * Replace with whatever VS Code adds when it ships a stable session
 * id (likely 1.95+).
 */
export function resolveChatId(input: {
  participantId: string;
  firstUserPrompt: string;
  workspacePath?: string;
  sessionSalt?: string;
}): string {
  const h = crypto.createHash("sha256");
  // Bump version when the input list changes to invalidate stale ids.
  h.update("v2\0");
  h.update(input.participantId);
  h.update("\0");
  h.update(input.firstUserPrompt);
  h.update("\0");
  h.update(input.workspacePath ?? "");
  h.update("\0");
  h.update(input.sessionSalt ?? "");
  return h.digest("hex").slice(0, 16);
}

// ── Reactive compaction (Phase C.2) ───────────────────────────────────────

export type CompactionStrategy =
  | { kind: "none" }
  | { kind: "drop-tools" }
  | { kind: "summarize-old"; oldestHalf: OrchestratorMessage[] }
  | { kind: "hard-truncate"; budgetTokens: number };

/**
 * Decide which compaction strategy to apply for a turn, given the
 * pre-prompt token total. Pure function; the runner separately drives
 * the side-effecting parts (re-fetch with role_filter, spawn summarizer,
 * re-fetch with smaller budget).
 *
 *   <80%  → none
 *   80-90 → drop role:"tool" rows for this turn's render
 *   90-99 → summarize the oldest half via the summarizer sub-agent
 *   ≥99   → hard-truncate to 50% of the model window
 */
export function planCompaction(
  history: OrchestratorMessage[],
  totalTokens: number,
  modelContextTokens: number = MODEL_CONTEXT_TOKENS,
): CompactionStrategy {
  if (modelContextTokens <= 0) { return { kind: "none" }; }
  const ratio = totalTokens / modelContextTokens;

  if (ratio < COMPACT_T1_DROP_TOOLS) { return { kind: "none" }; }
  if (ratio < COMPACT_T2_SUMMARIZE)  { return { kind: "drop-tools" }; }
  if (ratio < COMPACT_T3_TRUNCATE) {
    if (history.length < 2) {
      // Nothing to summarize — fall through to hard truncate.
      return { kind: "hard-truncate", budgetTokens: Math.floor(modelContextTokens * 0.5) };
    }
    const half = Math.floor(history.length / 2);
    return { kind: "summarize-old", oldestHalf: history.slice(0, half) };
  }
  return { kind: "hard-truncate", budgetTokens: Math.floor(modelContextTokens * 0.5) };
}

/**
 * Apply the local part of a compaction directive. The `summarize-old`
 * branch returns the *recent half* unchanged; the runner is expected to
 * spawn the summarizer separately and prepend its result as a
 * `role:"system"` synthetic message.
 */
export function applyCompaction(
  directive: CompactionStrategy,
  history: OrchestratorMessage[],
): OrchestratorMessage[] {
  switch (directive.kind) {
    case "none":
      return history;
    case "drop-tools":
      return history.filter(m => m.role !== "tool");
    case "summarize-old": {
      const half = Math.floor(history.length / 2);
      return history.slice(half);
    }
    case "hard-truncate": {
      // Keep newest messages whose cumulative tokens fit the budget.
      const kept: OrchestratorMessage[] = [];
      let total = 0;
      for (let i = history.length - 1; i >= 0; i--) {
        const cost = estimateTokens(history[i].content);
        if (kept.length === 0) { kept.push(history[i]); total = cost; continue; }
        if (total + cost > directive.budgetTokens) { continue; }
        kept.push(history[i]);
        total += cost;
      }
      return kept.reverse();
    }
  }
}

/** Sum tokens across messages plus the explicit prompt + system prompt. */
export function totalHistoryTokens(
  messages: ReadonlyArray<OrchestratorMessage>,
  systemPrompt: string,
  currentPrompt: string,
): number {
  let total = estimateTokens(systemPrompt) + estimateTokens(currentPrompt);
  for (const m of messages) { total += estimateTokens(m.content); }
  return total;
}

// ── Distillation triggers (Phase C.2) ─────────────────────────────────────

/**
 * Bundled frustration regexes — TS mirror of pattern_detector's bank, used
 * extension-side because the trigger fires before any MCP round-trip. The
 * Python side keeps its own copy in `.github/memory/sentiment-patterns.json`
 * for tests + future hot-reload wiring; both lists must stay in sync. If
 * you change one, change the other.
 */
const FRUSTRATION_PATTERNS: ReadonlyArray<{ label: string; regex: RegExp }> = [
  { label: "wrong/broken assertion",   regex: /\bthat'?s?\s+(wrong|broken|stupid|garbage)\b/i },
  { label: "still not working",        regex: /\bthis\s+(isn'?t|is\s+not)\s+(working|right|what)\b/i },
  { label: "stop doing X",             regex: /\bstop\s+(doing|telling|saying)\b/i },
  { label: "repeated correction",      regex: /\b(no|nope),?\s+(again|i\s+(said|told))\b/i },
  { label: "give up",                  regex: /\bgive\s+up\b/i },
  { label: "never mind",               regex: /\bnever\s+mind\b/i },
  { label: "forget it",                regex: /\bforget\s+it\b/i },
  { label: "ugh",                      regex: /^\s*ugh[!.]?\s*$/i },
];

/** Return the first frustration label that matches `text`, or null. */
export function detectFrustration(text: string): string | null {
  if (!text || !text.trim()) { return null; }
  for (const { label, regex } of FRUSTRATION_PATTERNS) {
    if (regex.test(text)) { return label; }
  }
  return null;
}

/**
 * Per-turn dedup tracker for distillation triggers. The orchestrator
 * keeps one of these alive for the duration of a single user turn; once
 * the turn ends the tracker is discarded so the next turn starts fresh.
 * Persistent dedup (across turns) is enforced server-side by
 * `session_distiller.append_pattern`'s `_load_existing_patterns` index.
 */
export class TriggerDedup {
  private fired = new Set<string>();

  /** Returns true the first time `key` is checked, false on subsequent calls. */
  shouldFire(key: string): boolean {
    if (this.fired.has(key)) { return false; }
    this.fired.add(key);
    return true;
  }
}
