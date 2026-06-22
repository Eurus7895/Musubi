/**
 * harness-tier: ephemeral
 * expires-when: the sub-agent split is dissolved
 * cost-lever: deletes the per-stage budget tracker
 * (what: Per-stage sub-agent spawn budget enforcer.)
 */
/**
 * runners/pipelineSubagentBudget.ts — per-stage-attempt sub-agent spawn
 * budget (Phase G.1). vscode-free so node:test can exercise it directly.
 *
 * The pipeline runner holds one `StageSpawnBudget` instance per
 * (session_id, stage, attempt) tuple while a stage is executing; the
 * stage's `spawnSubAgent` calls consume from it. A fresh attempt resets
 * the counter (per the design — "Retry this stage" gives the model a
 * clean budget, not a residual one).
 *
 * The escalation UX (ask the user when the budget is exhausted) is
 * Phase G.1.5 work — for G.1 this module just throws
 * `SubagentBudgetExhausted` and the caller decides what to do.
 */

/**
 * Per-stage-attempt counter. One instance per (session_id, stage, attempt)
 * tuple; reset at the start of every fresh attempt. Pipeline runners hold
 * it in their stage-loop frame.
 */
export class StageSpawnBudget {
  private spent = 0;
  /** Pretty-print form for logs / errors. */
  readonly stageKey: string;

  constructor(
    readonly sessionId: string,
    readonly stage: string,
    readonly attempt: number,
    /** Hard cap; 0 disables spawning entirely for the stage. */
    readonly limit: number,
  ) {
    if (!Number.isInteger(limit) || limit < 0) {
      throw new Error(`StageSpawnBudget limit must be a non-negative integer, got ${limit}`);
    }
    this.stageKey = `${sessionId}/${stage}#${attempt}`;
  }

  /** Total spawns this stage attempt has issued so far. */
  get used(): number { return this.spent; }

  /** True when the next spawn would breach the limit. */
  get exhausted(): boolean { return this.spent >= this.limit; }

  /** Record a spawn. Caller must check `exhausted` first. */
  consume(): void { this.spent++; }
}

/**
 * Thrown by `spawnSubAgent` when the per-stage budget is already at
 * `limit`. Distinct error class so the caller can route this into the
 * Phase G.1.5 escalation UX (ask the user) without confusing it with a
 * generic spawn failure (LM unavailable, policy denial, etc.).
 */
export class SubagentBudgetExhausted extends Error {
  constructor(readonly budget: StageSpawnBudget) {
    super(
      `sub-agent budget exhausted for stage ${budget.stageKey} ` +
      `(limit=${budget.limit}, used=${budget.used})`,
    );
    this.name = "SubagentBudgetExhausted";
  }
}
