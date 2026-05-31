/**
 * pipelineBudgetCore.ts — Phase J.3 credit accounting helpers.
 *
 * Pure (vscode-free) helpers for:
 *   - Looking up per-million-token pricing for a Copilot model family
 *   - Estimating credits for a single LM call (pre-flight and post-flight)
 *   - Tracking cumulative credit usage during a pipeline run with soft-
 *     warn and hard-stop thresholds
 *   - Parsing `max_credits:` / `warn_at:` from pipeline.yaml (single
 *     numeric fields, narrow regex same as J.5's contextCapCore)
 *
 * Resolution chain (where the cap value comes from):
 *   1. pipeline.yaml `max_credits:` field  (if declared)
 *   2. Built-in default per-pipeline (none for unknown pipelines, so the
 *      caller falls through to "no enforcement")
 *
 * The budget is denominated in CREDITS, not raw tokens — 1 credit = $0.01
 * (Bosch Copilot billing) so the user-visible numbers match the dashboard.
 *
 * Cost model:
 *   credits = (input_tokens × rate_input
 *              + cached_input_tokens × rate_cached
 *              + output_tokens × rate_output) / 1_000_000 / 0.01
 *
 * Caching: we don't know which portion of input was cached on the proxy
 * side (PR #54's verification is still pending). For now we assume the
 * worst case (no cache hit) for pre-flight estimates — this over-counts
 * and is conservative. When J.4 wires real telemetry, post-flight numbers
 * can be replaced with the actual cached_input from response metadata if
 * VS Code surfaces it.
 *
 * Token estimation: TS doesn't have a tokenizer; we approximate
 * tokens ≈ chars / 4. Good enough for budget accounting (the error is
 * dwarfed by per-call variance), and matches the estimator the
 * orchestrator already uses for replay budgeting.
 */

import * as fs from "fs";
import * as path from "path";

// ── Rate table ──────────────────────────────────────────────────────────────

/** Rates in USD per million tokens. */
export interface ModelRate {
  input: number;
  cached_input: number;
  output: number;
  cache_write: number;
}

/**
 * Per-family rate table. Sourced from the Bosch Copilot billing tier —
 * the user shared these in chat:
 *   claude-sonnet-4.6: input $3.00 / cached $0.30 / output $15.00 / cache_write $3.75
 *
 * Other families are best-effort from public published rates. Update when
 * actual Bosch billing differs.
 *
 * UNKNOWN_FAMILY_RATE is a deliberately pessimistic Sonnet-level fallback
 * — when budgeting, over-estimating cost is safer than under-estimating.
 */
export const RATES: Readonly<Record<string, ModelRate>> = {
  "claude-sonnet-4.6":    { input: 3.00, cached_input: 0.30, output: 15.00, cache_write: 3.75 },
  "claude-sonnet-4.5":    { input: 3.00, cached_input: 0.30, output: 15.00, cache_write: 3.75 },
  "claude-haiku-4.5":     { input: 0.80, cached_input: 0.08, output:  4.00, cache_write: 1.00 },
  "claude-opus-4.8":      { input: 15.00, cached_input: 1.50, output: 75.00, cache_write: 18.75 },
  "claude-opus-4.7":      { input: 15.00, cached_input: 1.50, output: 75.00, cache_write: 18.75 },
  "gpt-4o":               { input: 2.50, cached_input: 1.25, output: 10.00, cache_write: 2.50 },
  "gpt-4o-mini":          { input: 0.15, cached_input: 0.075, output: 0.60, cache_write: 0.15 },
  "gpt-4.1":              { input: 2.00, cached_input: 1.00, output:  8.00, cache_write: 2.00 },
  "gpt-4.1-mini":         { input: 0.40, cached_input: 0.20, output:  1.60, cache_write: 0.40 },
  "gpt-5-mini":           { input: 0.25, cached_input: 0.05, output:  2.00, cache_write: 0.25 },
  "gemini-2.5-flash":     { input: 0.30, cached_input: 0.075, output: 2.50, cache_write: 0.30 },
};

export const UNKNOWN_FAMILY_RATE: ModelRate = {
  input: 3.00, cached_input: 0.30, output: 15.00, cache_write: 3.75,
};

export function rateFor(family: string): ModelRate {
  return RATES[family] ?? UNKNOWN_FAMILY_RATE;
}

// ── Token + credit estimation ───────────────────────────────────────────────

/** Char-to-token approximation. Matches estimateTokens in orchestratorCore. */
export function estimateTokensFromChars(chars: number): number {
  return Math.ceil(chars / 4);
}

/**
 * Cost in credits (1 credit = $0.01) for an LM call.
 * `cachedInputTokens` defaults to 0 — pre-flight callers don't know it.
 */
export function estimateCallCredits(
  family: string,
  inputTokens: number,
  outputTokens: number,
  cachedInputTokens: number = 0,
): number {
  const r = rateFor(family);
  const freshInput = Math.max(0, inputTokens - cachedInputTokens);
  const usd =
    (freshInput * r.input
      + cachedInputTokens * r.cached_input
      + outputTokens * r.output) / 1_000_000;
  return usd / 0.01;
}

// ── Pipeline.yaml parser ────────────────────────────────────────────────────

const MAX_CREDITS_LINE = /^max_credits:\s*(\d+(?:\.\d+)?)\s*(?:#.*)?$/m;
const WARN_AT_LINE = /^warn_at:\s*(0?\.\d+|1(?:\.0+)?)\s*(?:#.*)?$/m;

export interface PipelineBudgetConfig {
  maxCredits: number | null;
  warnAtRatio: number;
}

/**
 * Read `max_credits:` / `warn_at:` from
 * `<root>/.github/pipelines/<pipelineName>/pipeline.yaml`.
 *
 * Returns:
 *   - `maxCredits = null` when the field is absent (no enforcement)
 *   - `maxCredits = <positive number>` when declared
 *   - `warnAtRatio = 0.8` default when not declared, or parsed value in (0,1]
 */
export function resolvePipelineBudget(
  roots: readonly string[],
  pipelineName: string,
): PipelineBudgetConfig {
  const trimmed = (pipelineName || "").trim();
  if (!trimmed || !/^[a-z0-9_-]+$/i.test(trimmed)) {
    return { maxCredits: null, warnAtRatio: 0.8 };
  }
  for (const root of roots) {
    if (!root) { continue; }
    const file = path.join(root, ".github", "pipelines", trimmed, "pipeline.yaml");
    let text: string;
    try {
      text = fs.readFileSync(file, "utf-8");
    } catch {
      continue;
    }
    const maxMatch = text.match(MAX_CREDITS_LINE);
    const warnMatch = text.match(WARN_AT_LINE);
    const maxCredits = maxMatch ? parseFloat(maxMatch[1]) : null;
    const parsedWarn = warnMatch ? parseFloat(warnMatch[1]) : 0.8;
    const warnAtRatio = parsedWarn > 0 && parsedWarn <= 1 ? parsedWarn : 0.8;
    return {
      maxCredits: maxCredits !== null && Number.isFinite(maxCredits) && maxCredits > 0
        ? maxCredits : null,
      warnAtRatio,
    };
  }
  return { maxCredits: null, warnAtRatio: 0.8 };
}

// ── BudgetEnforcer ──────────────────────────────────────────────────────────

export type BudgetStatus = "allow" | "warn" | "halt";

/**
 * Running credit accountant for a single pipeline run. Pre-flight: estimate
 * the call's cost and check whether it would breach the cap. Post-flight:
 * update the actual cost (more accurate than pre-flight when output size
 * was known up-front) and re-check.
 *
 * The class is *additive* — credit consumed is never refunded. A halted
 * pipeline that resumes via /continue would instantiate a fresh enforcer
 * with the budget seeded from the prior run's leftover.
 */
export class BudgetEnforcer {
  private _creditsUsed = 0;
  private _warned = false;

  constructor(
    readonly maxCredits: number,
    readonly warnAtRatio: number = 0.8,
  ) {
    if (!Number.isFinite(maxCredits) || maxCredits <= 0) {
      throw new Error(`BudgetEnforcer: maxCredits must be positive, got ${maxCredits}`);
    }
    if (warnAtRatio <= 0 || warnAtRatio > 1) {
      throw new Error(`BudgetEnforcer: warnAtRatio must be in (0,1], got ${warnAtRatio}`);
    }
  }

  get creditsUsed(): number { return this._creditsUsed; }
  get remaining(): number { return Math.max(0, this.maxCredits - this._creditsUsed); }
  get warned(): boolean { return this._warned; }

  /**
   * Check whether spending `estimatedCredits` would breach the cap.
   * Returns "halt" if projected total > maxCredits, "warn" if first time
   * crossing the warn threshold (caller should emit a one-time message,
   * then mark this enforcer as warned via subsequent calls — the flag
   * is sticky), "allow" otherwise.
   */
  preflight(estimatedCredits: number): BudgetStatus {
    const projected = this._creditsUsed + estimatedCredits;
    if (projected > this.maxCredits) { return "halt"; }
    const warnThreshold = this.maxCredits * this.warnAtRatio;
    if (projected >= warnThreshold && !this._warned) { return "warn"; }
    return "allow";
  }

  /**
   * Apply actual credit spend after a call completes. Updates the running
   * total and returns the post-update status. Idempotent in the sense that
   * each call is one add; never refunds.
   *
   * If we crossed the warn threshold on this call, the returned status is
   * "warn" exactly once — the internal `_warned` flag flips so subsequent
   * post-flights return "allow" until the cap is reached.
   */
  charge(actualCredits: number): BudgetStatus {
    if (!Number.isFinite(actualCredits) || actualCredits < 0) {
      throw new Error(`BudgetEnforcer.charge: actualCredits must be non-negative, got ${actualCredits}`);
    }
    this._creditsUsed += actualCredits;
    if (this._creditsUsed > this.maxCredits) { return "halt"; }
    const warnThreshold = this.maxCredits * this.warnAtRatio;
    if (this._creditsUsed >= warnThreshold && !this._warned) {
      this._warned = true;
      return "warn";
    }
    return "allow";
  }
}

// ── Per-session active-enforcer registry ────────────────────────────────────

/**
 * runAgentLM is called from many sites across pipeline.ts. Threading a
 * BudgetEnforcer parameter through every call site would touch ~15
 * functions. Instead, runPipeline registers the enforcer in this module-
 * level map keyed by session_id at start and deregisters in a finally
 * block. runAgentLM looks up by the sessionId it already has in its
 * AgentObs and acts on the enforcer (and the on-event callback registered
 * alongside it) if present.
 *
 * Single-threaded JS + per-session keying means concurrent pipelines
 * won't trample each other. The registry is private; mutate only via
 * registerActiveBudget / unregisterActiveBudget.
 */

export interface BudgetEvent {
  status: "warn" | "halt";
  phase: "preflight" | "postflight";
  creditsUsed: number;
  maxCredits: number;
  remaining: number;
  family: string;
  thisCallCredits: number;
}

export interface ActiveBudget {
  enforcer: BudgetEnforcer;
  onEvent: (event: BudgetEvent) => void;
}

const _activeEnforcers = new Map<string, ActiveBudget>();

export function registerActiveBudget(
  sessionId: string,
  enforcer: BudgetEnforcer,
  onEvent: (event: BudgetEvent) => void,
): void {
  _activeEnforcers.set(sessionId, { enforcer, onEvent });
}

export function unregisterActiveBudget(sessionId: string): void {
  _activeEnforcers.delete(sessionId);
}

export function getActiveBudget(sessionId: string): ActiveBudget | null {
  return _activeEnforcers.get(sessionId) ?? null;
}

/** Reset the registry — test helper, not for production use. */
export function _resetActiveBudgets_FOR_TESTS(): void {
  _activeEnforcers.clear();
}

/**
 * Thrown by runAgentLM when the pre-flight check or the post-flight
 * charge crosses the cap. Caught by runPipeline to terminate the run
 * cleanly with a halt message instead of bubbling up as an unhandled
 * error.
 */
export class BudgetExhaustedError extends Error {
  constructor(
    readonly phase: "preflight" | "postflight",
    readonly creditsUsed: number,
    readonly maxCredits: number,
    readonly family: string,
    readonly thisCallCredits: number,
  ) {
    super(
      `Pipeline budget exhausted at ${phase}: ${creditsUsed.toFixed(2)} ` +
      `of ${maxCredits.toFixed(2)} credits used after a ${thisCallCredits.toFixed(2)}-credit ` +
      `${family} call.`,
    );
    this.name = "BudgetExhaustedError";
  }
}
