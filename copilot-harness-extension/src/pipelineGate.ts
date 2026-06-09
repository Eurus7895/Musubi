/**
 * pipelineGate.ts — pure helpers for the Phase G.1.5 review-gate UX.
 *
 * No vscode imports — drives node:test unit coverage. The vscode-using
 * shell (command registration, settings I/O, popup fallback) lives in
 * pipelineGateUi.ts.
 *
 * What's here:
 *   • Resume action vocabulary + validation (mirrored against
 *     `state.VALID_RESUME_ACTIONS` on the Python side).
 *   • Markdown builders for the two gate flavours (stage_review,
 *     budget_exhausted) and for the auto-approve toggle line.
 *   • Command-arg builders + parsers used by chat buttons.
 *   • Pause-state evaluator: should the runner gate this stage given
 *     the session row + the user's per-pipeline auto-approve setting?
 */

// ── Vocabulary ──────────────────────────────────────────────────────────────

export type PauseReason = "stage_review" | "budget_exhausted";

export type StageReviewAction =
  | "approve"
  | "retry"
  | "abort"
  | "auto_approve_rest";

export type BudgetEscalationAction = "grant" | "force" | "abort";

export type ResumeAction = StageReviewAction | BudgetEscalationAction;

export const STAGE_REVIEW_ACTIONS: ReadonlyArray<StageReviewAction> = [
  "approve", "retry", "abort", "auto_approve_rest",
];

export const BUDGET_ESCALATION_ACTIONS: ReadonlyArray<BudgetEscalationAction> = [
  "grant", "force", "abort",
];

const ACTIONS_BY_REASON: Readonly<Record<PauseReason, ReadonlySet<ResumeAction>>> = {
  "stage_review":     new Set<ResumeAction>(STAGE_REVIEW_ACTIONS),
  "budget_exhausted": new Set<ResumeAction>(BUDGET_ESCALATION_ACTIONS),
};

/** True iff `action` is valid for `reason`. */
export function isActionValidFor(reason: PauseReason, action: string): action is ResumeAction {
  return ACTIONS_BY_REASON[reason].has(action as ResumeAction);
}

// ── Pause-state evaluation ──────────────────────────────────────────────────

export interface PauseStateRow {
  paused_at_stage: string | null;
  pause_reason: PauseReason | null;
  /** Per-run override set by the "Run remaining stages without review" button. */
  auto_approve_remaining: boolean;
}

export interface GateDecisionInput {
  /** Live session row from `harness_get_pause_state`. */
  pauseState: PauseStateRow;
  /** Per-pipeline persistent setting (`copilotHarness.autoApprove.<pipeline>`). */
  perPipelineAutoApprove: boolean;
  /** Default `true` — the gate is live. Tests can flip to false to skip. */
  gateEnabled?: boolean;
}

export type GateDecision =
  | { kind: "skip"; reason: "auto_approve_remaining" | "per_pipeline_setting" | "gate_disabled" }
  | { kind: "render"; pauseReason: PauseReason };

/**
 * Decide whether a stage transition should pop the gate UI.
 *
 *   - gateEnabled=false (test seam) → skip.
 *   - per-pipeline setting on → skip (user opted out for this pipeline).
 *   - per-run flag on → skip (user clicked "Run remaining without review").
 *   - paused_at_stage + pause_reason set → render (the harness paused us).
 *
 * Returns `{kind: "skip"}` when the runner should proceed and `{kind:
 * "render"}` when it should render the gate UI and await a decision.
 */
export function decideGate(input: GateDecisionInput): GateDecision {
  if (input.gateEnabled === false) {
    return { kind: "skip", reason: "gate_disabled" };
  }
  if (input.pauseState.auto_approve_remaining) {
    return { kind: "skip", reason: "auto_approve_remaining" };
  }
  if (input.perPipelineAutoApprove) {
    return { kind: "skip", reason: "per_pipeline_setting" };
  }
  if (input.pauseState.paused_at_stage && input.pauseState.pause_reason) {
    return { kind: "render", pauseReason: input.pauseState.pause_reason };
  }
  // No pause flag means the runner shouldn't gate yet — caller is
  // responsible for first calling harness_pause_session before reading
  // pause state.
  return { kind: "skip", reason: "gate_disabled" };
}

// ── Markdown builders ───────────────────────────────────────────────────────

export interface StageGateMarkdownInput {
  pipelineName: string;
  stage: string;
  attempt: number;
  /** Approximate tokens consumed by the stage attempt (for at-a-glance cost). */
  tokenEstimate?: number | null;
  elapsedMs?: number | null;
  /** True when the per-pipeline auto-approve user setting is currently ON. */
  autoApproveOn: boolean;
}

function fmtSecs(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) { return null; }
  if (!Number.isFinite(ms) || ms < 0) { return null; }
  const s = ms / 1000;
  return s < 10 ? s.toFixed(1) + "s" : Math.round(s) + "s";
}

/** Header rendered above the stage_review buttons. */
export function renderStageGateMarkdown(input: StageGateMarkdownInput): string {
  const meta: string[] = [
    `attempt ${input.attempt}`,
  ];
  const secs = fmtSecs(input.elapsedMs ?? null);
  if (secs) { meta.push(secs); }
  if (typeof input.tokenEstimate === "number" && input.tokenEstimate > 0) {
    meta.push(`~${input.tokenEstimate.toLocaleString()}t`);
  }
  return [
    `\n---\n`,
    `**✓ /${input.pipelineName} · stage \`${input.stage}\` complete** · ${meta.join(" · ")}\n`,
    `\nReview the output above, then choose how to continue:\n`,
  ].join("");
}

export interface BudgetGateMarkdownInput {
  pipelineName: string;
  stage: string;
  attempt: number;
  used: number;
  limit: number;
}

/** Header rendered above the budget_exhausted buttons. */
export function renderBudgetGateMarkdown(input: BudgetGateMarkdownInput): string {
  return [
    `\n---\n`,
    `**⚠ /${input.pipelineName} · stage \`${input.stage}\` · attempt ${input.attempt} ` +
    `· spawn budget exhausted (${input.used}/${input.limit})**\n`,
    `\nThe stage tried to spawn another sub-agent past its budget. Choose:\n`,
  ].join("");
}

/** Trailing line that announces the per-pipeline auto-approve toggle. */
export function renderAutoApproveToggleLabel(
  pipelineName: string, autoApproveOn: boolean,
): string {
  return autoApproveOn
    ? `Auto-approve **/${pipelineName}** for future runs: **ON** — click to turn OFF`
    : `Auto-approve **/${pipelineName}** for future runs: **OFF** — click to turn ON`;
}

// ── Command-arg builders + parsers ──────────────────────────────────────────

export const RESUME_COMMAND_ID = "copilot-harness.resumeSession" as const;
export const TOGGLE_AUTO_APPROVE_COMMAND_ID = "copilot-harness.toggleAutoApprove" as const;

/** Args shape passed to the resume command via `stream.button({arguments: [...]})`. */
export interface ResumeCommandArgs {
  sessionId: string;
  pipelineName: string;
  stage: string;
  attempt: number;
  pauseReason: PauseReason;
  action: ResumeAction;
  /**
   * When action='grant', the additional spawn count to grant. G.1.5
   * ships +3 fixed; pipeline.yaml may parameterise later (G.1.6+).
   */
  extraBudget?: number;
  /**
   * When action='retry', the runner pops a hint input box BEFORE
   * dispatching the MCP call — so the args sent on first click do
   * NOT include the hint. Stored separately by the shell.
   */
  promptForHint?: boolean;
  /**
   * Phase G.1.7 — per-task chunk identifier when the gate fires inside
   * a chunked code/review run. Plumbed through harness_resume_session
   * (and harness_increment_attempt on retry) so the right chunk row is
   * targeted. Optional: non-chunked stages (plan / design / single-shot
   * code-review) don't set it.
   */
  chunkId?: string;
}

export interface ToggleAutoApproveArgs {
  pipelineName: string;
}

export function buildResumeCommandArgs(
  base: Omit<ResumeCommandArgs, "action" | "extraBudget" | "promptForHint">,
  action: ResumeAction,
  extras?: { extraBudget?: number; promptForHint?: boolean },
): ResumeCommandArgs {
  const out: ResumeCommandArgs = { ...base, action };
  if (typeof extras?.extraBudget === "number") { out.extraBudget = extras.extraBudget; }
  if (extras?.promptForHint) { out.promptForHint = true; }
  // chunkId is part of the Omit'd base if present — copy through.
  return out;
}

export function parseResumeCommandArgs(raw: unknown): ResumeCommandArgs | null {
  if (!raw || typeof raw !== "object") { return null; }
  const o = raw as Record<string, unknown>;
  if (typeof o.sessionId !== "string" || !o.sessionId) { return null; }
  if (typeof o.pipelineName !== "string" || !o.pipelineName) { return null; }
  if (typeof o.stage !== "string" || !o.stage) { return null; }
  if (typeof o.attempt !== "number") { return null; }
  if (o.pauseReason !== "stage_review" && o.pauseReason !== "budget_exhausted") { return null; }
  if (typeof o.action !== "string" || !isActionValidFor(o.pauseReason, o.action)) {
    return null;
  }
  const out: ResumeCommandArgs = {
    sessionId: o.sessionId,
    pipelineName: o.pipelineName,
    stage: o.stage,
    attempt: o.attempt,
    pauseReason: o.pauseReason,
    action: o.action as ResumeAction,
  };
  if (typeof o.extraBudget === "number") { out.extraBudget = o.extraBudget; }
  if (o.promptForHint === true) { out.promptForHint = true; }
  if (typeof o.chunkId === "string" && o.chunkId.length > 0) { out.chunkId = o.chunkId; }
  return out;
}

export function parseToggleAutoApproveArgs(raw: unknown): ToggleAutoApproveArgs | null {
  if (!raw || typeof raw !== "object") { return null; }
  const o = raw as Record<string, unknown>;
  if (typeof o.pipelineName !== "string" || !o.pipelineName) { return null; }
  return { pipelineName: o.pipelineName };
}

/**
 * Spec for one button to render in the gate UI. The shell turns each
 * into a `vscode.Command` and hands it to `stream.button(...)`.
 */
export interface GateButtonSpec {
  title: string;
  args: ResumeCommandArgs;
}

/** Default +3 grant amount on a `budget_exhausted` escalation (Phase G.1.5 MVP). */
export const DEFAULT_GRANT_AMOUNT = 3;

export function buildStageReviewButtons(
  base: Omit<ResumeCommandArgs, "action" | "extraBudget" | "promptForHint">,
): GateButtonSpec[] {
  if (base.pauseReason !== "stage_review") {
    throw new Error(`buildStageReviewButtons called with pauseReason=${base.pauseReason}`);
  }
  return [
    { title: "✓ Approve & continue",
      args: buildResumeCommandArgs(base, "approve") },
    { title: "↻ Retry with hint…",
      args: buildResumeCommandArgs(base, "retry", { promptForHint: true }) },
    { title: "✕ Abort",
      args: buildResumeCommandArgs(base, "abort") },
    { title: "⚡ Run remaining stages without review",
      args: buildResumeCommandArgs(base, "auto_approve_rest") },
  ];
}

export function buildBudgetEscalationButtons(
  base: Omit<ResumeCommandArgs, "action" | "extraBudget" | "promptForHint">,
  grantAmount: number = DEFAULT_GRANT_AMOUNT,
): GateButtonSpec[] {
  if (base.pauseReason !== "budget_exhausted") {
    throw new Error(`buildBudgetEscalationButtons called with pauseReason=${base.pauseReason}`);
  }
  return [
    { title: `+ Grant +${grantAmount} spawns`,
      args: buildResumeCommandArgs(base, "grant", { extraBudget: grantAmount }) },
    { title: "→ Force answer with what it has",
      args: buildResumeCommandArgs(base, "force") },
    { title: "✕ Abort stage",
      args: buildResumeCommandArgs(base, "abort") },
  ];
}

// ── Setting-key helper ──────────────────────────────────────────────────────

/** Settings key for the per-pipeline auto-approve toggle. */
export function autoApproveSettingKey(pipelineName: string): string {
  return `autoApprove.${pipelineName}`;
}
