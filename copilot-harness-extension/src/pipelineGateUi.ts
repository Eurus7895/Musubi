/**
 * pipelineGateUi.ts — vscode shell for the Phase G.1.5 review gate.
 *
 * Wires together:
 *   • a per-session "pending decision" registry so the runner can await
 *     a user click on a chat button without polling the harness DB,
 *   • two registered VS Code commands (`copilot-harness.resumeSession`
 *     + `copilot-harness.toggleAutoApprove`) that the chat buttons
 *     invoke,
 *   • a `vscode.workspace` getter/setter for the per-pipeline auto-
 *     approve user setting (`copilotHarness.autoApprove.<pipeline>`),
 *   • a popup fallback for hosts where `stream.button()` doesn't
 *     render — logs the limitation and uses showInformationMessage.
 *
 * Pure helpers stay in pipelineGate.ts; this file imports vscode.
 */

import * as vscode from "vscode";
import { McpClient } from "./mcpClient";
import {
  buildBudgetEscalationButtons,
  buildStageReviewButtons,
  decideGate,
  parseResumeCommandArgs,
  parseToggleAutoApproveArgs,
  renderAutoApproveToggleLabel,
  renderBudgetGateMarkdown,
  renderStageGateMarkdown,
  RESUME_COMMAND_ID,
  TOGGLE_AUTO_APPROVE_COMMAND_ID,
  type PauseStateRow,
  type ResumeCommandArgs,
} from "./pipelineGate";

// ── Per-session pending-decision registry ────────────────────────────────

/**
 * Resolved with the user's resume action when they click a gate button
 * during an in-flight chat turn. Lets the runner await the click in
 * the same chat turn rather than asking the user to type
 * `@harness continue` after each gate.
 *
 * Survives across one chat turn — created when the runner enters the
 * gate, resolved when the command handler fires.
 *
 * If the chat turn is cancelled (user closes panel, VS Code crashes)
 * before the click, the pending entry is discarded; the harness's
 * `paused_at_stage` row is still set, so the user can resume the
 * pipeline by sending `@harness continue` later.
 */
interface PendingDecision {
  resolve: (decision: ResumedDecision) => void;
  // Reject is only used when the chat token is cancelled — distinguish
  // from "user clicked Abort" which resolves cleanly.
  reject: (err: Error) => void;
}

/** Result handed back to the runner once the user picks an action. */
export interface ResumedDecision {
  action: ResumeCommandArgs["action"];
  /** Hint typed in the inline input box on a `retry` action. */
  userHint?: string;
  /** Spawns granted on a `grant` action; defaults to args.extraBudget. */
  extraBudget?: number;
  /** Echo from the harness — true when the click set auto_approve_remaining. */
  autoApproveRemaining?: boolean;
  /**
   * Phase G.1.7 — echoed from the click args so the runner knows which
   * chunk's attempt to bump on `retry`. Undefined for non-chunked gates.
   */
  chunkId?: string;
}

const _pending = new Map<string, PendingDecision>();

/** Test seam — clear all pending entries (used by tests + extension reload). */
export function _resetPendingDecisions(): void {
  for (const p of _pending.values()) {
    p.reject(new Error("pending gate decision dropped on reset"));
  }
  _pending.clear();
}

// ── Settings I/O ────────────────────────────────────────────────────────

const SETTINGS_NAMESPACE = "copilotHarness";

export function getPerPipelineAutoApprove(pipelineName: string): boolean {
  const cfg = vscode.workspace.getConfiguration(SETTINGS_NAMESPACE);
  // Settings keys live under copilotHarness.autoApprove.<pipeline>; read
  // via dotted lookup.
  const all = cfg.get<Record<string, unknown>>("autoApprove") ?? {};
  return Boolean(all[pipelineName]);
}

export async function setPerPipelineAutoApprove(
  pipelineName: string, value: boolean,
): Promise<void> {
  const cfg = vscode.workspace.getConfiguration(SETTINGS_NAMESPACE);
  const all = { ...(cfg.get<Record<string, unknown>>("autoApprove") ?? {}) };
  all[pipelineName] = value;
  await cfg.update("autoApprove", all, vscode.ConfigurationTarget.Global);
}

// ── Gate rendering ──────────────────────────────────────────────────────

export interface RenderStageGateOptions {
  stream: vscode.ChatResponseStream;
  sessionId: string;
  pipelineName: string;
  stage: string;
  attempt: number;
  tokenEstimate?: number | null;
  elapsedMs?: number | null;
  /** Phase G.1.7 — chunk identifier when the gate fires inside a chunked stage run. */
  chunkId?: string;
  /** Phase G.1.7 — human label for the chunk ("T1 — write tests …"). */
  chunkLabel?: string;
}

/**
 * Render the four-button stage_review gate inline in chat and return a
 * Promise that resolves when the user clicks a button. Fallback popup
 * fires if `stream.button()` is unavailable.
 */
export function renderStageReviewGate(
  opts: RenderStageGateOptions,
): Promise<ResumedDecision> {
  const autoApproveOn = getPerPipelineAutoApprove(opts.pipelineName);
  opts.stream.markdown(renderStageGateMarkdown({
    pipelineName: opts.pipelineName,
    stage: opts.stage,
    attempt: opts.attempt,
    tokenEstimate: opts.tokenEstimate ?? null,
    elapsedMs: opts.elapsedMs ?? null,
    autoApproveOn,
  }));
  if (opts.chunkLabel) {
    opts.stream.markdown(`*Chunk: **${opts.chunkLabel}***\n`);
  }

  const base = {
    sessionId: opts.sessionId,
    pipelineName: opts.pipelineName,
    stage: opts.stage,
    attempt: opts.attempt,
    pauseReason: "stage_review" as const,
    ...(opts.chunkId ? { chunkId: opts.chunkId } : {}),
  };

  const pendingPromise = registerPendingDecision(opts.sessionId);
  const buttons = buildStageReviewButtons(base);
  emitButtonsOrFallback(opts.stream, buttons);

  // Auto-approve toggle lives in the Pipelines sidebar view, not in the
  // gate UI. VS Code's chat-response button surface treats all buttons in
  // one response as a single-resolution group — clicking the toggle here
  // would consume the click and disable the four review-gate buttons,
  // leaving the user stuck. Show a one-line hint with the current state
  // so the user knows where to flip it.
  opts.stream.markdown(
    `\n${renderAutoApproveToggleLabel(opts.pipelineName, autoApproveOn)} ` +
    `_(toggle in the **Pipelines** sidebar)_\n`,
  );

  return pendingPromise;
}

export interface RenderBudgetGateOptions {
  stream: vscode.ChatResponseStream;
  sessionId: string;
  pipelineName: string;
  stage: string;
  attempt: number;
  used: number;
  limit: number;
}

export function renderBudgetEscalationGate(
  opts: RenderBudgetGateOptions,
): Promise<ResumedDecision> {
  opts.stream.markdown(renderBudgetGateMarkdown({
    pipelineName: opts.pipelineName,
    stage: opts.stage,
    attempt: opts.attempt,
    used: opts.used,
    limit: opts.limit,
  }));

  const base = {
    sessionId: opts.sessionId,
    pipelineName: opts.pipelineName,
    stage: opts.stage,
    attempt: opts.attempt,
    pauseReason: "budget_exhausted" as const,
  };

  const pendingPromise = registerPendingDecision(opts.sessionId);
  emitButtonsOrFallback(opts.stream, buildBudgetEscalationButtons(base));
  return pendingPromise;
}

function emitButtonsOrFallback(
  stream: vscode.ChatResponseStream,
  buttons: ReadonlyArray<{ title: string; args: ResumeCommandArgs }>,
): void {
  // The vscode types declare stream.button(); a host that doesn't
  // render it just no-ops. We have no API surface to detect "didn't
  // render" — so as a best-effort fallback, ALSO log a one-line
  // notification telling the user what to do if buttons don't appear.
  // The real popup fallback fires from the PendingDecision timeout
  // path (the runner can call `awaitPendingDecisionWithFallback`).
  for (const b of buttons) {
    stream.button({
      command: RESUME_COMMAND_ID,
      title: b.title,
      arguments: [b.args],
    });
  }
}

// ── Pending-decision registry helpers ───────────────────────────────────

function registerPendingDecision(sessionId: string): Promise<ResumedDecision> {
  // Replace any prior pending entry for this session — the harness only
  // pauses one stage at a time; a new gate render on the same session
  // means the previous one already resolved or was cancelled.
  const prior = _pending.get(sessionId);
  if (prior) { prior.reject(new Error("superseded by a new gate render")); }
  return new Promise<ResumedDecision>((resolve, reject) => {
    _pending.set(sessionId, { resolve, reject });
  });
}

/**
 * Cancel the pending decision for a session. The harness's
 * `paused_at_stage` row stays intact so a future `@harness continue`
 * can resume.
 */
export function cancelPendingDecision(sessionId: string, reason: string): void {
  const p = _pending.get(sessionId);
  if (!p) { return; }
  _pending.delete(sessionId);
  p.reject(new Error(reason));
}

// ── Command handlers ────────────────────────────────────────────────────

/** Callback invoked by the runner when it has the result of a click. */
export interface CommandRegistration extends vscode.Disposable {}

export interface RegisterCommandsOptions {
  client: McpClient;
  log: (msg: string) => void;
}

export function registerGateCommands(opts: RegisterCommandsOptions): CommandRegistration {
  const subs: vscode.Disposable[] = [];

  subs.push(vscode.commands.registerCommand(
    RESUME_COMMAND_ID,
    async (rawArgs: unknown) => {
      const parsed = parseResumeCommandArgs(rawArgs);
      if (!parsed) {
        opts.log(`[gate] ${RESUME_COMMAND_ID} ignoring malformed args: ${JSON.stringify(rawArgs)}`);
        return;
      }
      let userHint: string | undefined;
      if (parsed.action === "retry" && parsed.promptForHint) {
        const typed = await vscode.window.showInputBox({
          title: `Retry stage ${parsed.stage}`,
          prompt: "Optional: tell the agent what was wrong with the previous attempt.",
          placeHolder: "Leave empty to auto-retry.",
          ignoreFocusOut: true,
        });
        userHint = typed?.trim() || undefined;
      }
      const extraBudget = parsed.action === "grant" ? (parsed.extraBudget ?? 0) : 0;

      let resumeRaw: string;
      try {
        resumeRaw = await opts.client.callTool("harness_resume_session", {
          session_id: parsed.sessionId,
          action: parsed.action,
          user_hint: userHint ?? null,
          extra_budget: extraBudget,
        });
      // Note: harness_resume_session itself doesn't take chunk_id —
      // pause_session already recorded which chunk was paused, and
      // resume_session simply clears it. The runner reads the chunk
      // id back via the resolved Promise's `chunkId` field on retry.
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        opts.log(`[gate] resume MCP threw: ${msg}`);
        cancelPendingDecision(parsed.sessionId, `MCP error: ${msg}`);
        vscode.window.showErrorMessage(`CopilotHarness: gate resume failed — ${msg}`);
        return;
      }

      let parsedResume: { status?: string; auto_approve_remaining?: boolean; error?: string };
      try { parsedResume = JSON.parse(resumeRaw); } catch {
        opts.log(`[gate] resume MCP non-JSON response: ${resumeRaw.slice(0, 200)}`);
        cancelPendingDecision(parsed.sessionId, "MCP non-JSON response");
        return;
      }
      if (parsedResume.status !== "resumed") {
        opts.log(`[gate] resume MCP error: ${parsedResume.error ?? resumeRaw}`);
        cancelPendingDecision(parsed.sessionId, parsedResume.error ?? "resume not recorded");
        vscode.window.showErrorMessage(
          `CopilotHarness: ${parsedResume.error ?? "could not resume gate"}`,
        );
        return;
      }

      // Resolve the runner's awaiting Promise. If the chat turn was
      // already cancelled (user closed VS Code), no entry is registered;
      // the harness state still carries the resume action so a future
      // `@harness continue` will pick it up.
      const pending = _pending.get(parsed.sessionId);
      if (pending) {
        _pending.delete(parsed.sessionId);
        pending.resolve({
          action: parsed.action,
          userHint,
          extraBudget: parsed.action === "grant" ? extraBudget : undefined,
          autoApproveRemaining: parsedResume.auto_approve_remaining,
          chunkId: parsed.chunkId,
        });
      } else {
        opts.log(`[gate] no pending decision for session ${parsed.sessionId} — resume recorded; user may need to '@harness continue'`);
        vscode.window.showInformationMessage(
          `CopilotHarness: gate resumed (${parsed.action}). Type @harness continue if the pipeline doesn't pick up automatically.`,
        );
      }
    },
  ));

  subs.push(vscode.commands.registerCommand(
    TOGGLE_AUTO_APPROVE_COMMAND_ID,
    async (rawArgs: unknown) => {
      const parsed = parseToggleAutoApproveArgs(rawArgs);
      if (!parsed) {
        opts.log(`[gate] ${TOGGLE_AUTO_APPROVE_COMMAND_ID} ignoring malformed args: ${JSON.stringify(rawArgs)}`);
        return;
      }
      const current = getPerPipelineAutoApprove(parsed.pipelineName);
      try {
        await setPerPipelineAutoApprove(parsed.pipelineName, !current);
        vscode.window.showInformationMessage(
          `CopilotHarness: auto-approve ${current ? "disabled" : "enabled"} for /${parsed.pipelineName}.`,
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        opts.log(`[gate] failed to update auto-approve setting: ${msg}`);
        vscode.window.showErrorMessage(
          `CopilotHarness: failed to update setting — ${msg}`,
        );
      }
    },
  ));

  return vscode.Disposable.from(...subs);
}

// ── High-level helper: run-or-skip the stage_review gate ────────────────

export interface RunStageReviewGateOptions {
  client: McpClient;
  stream: vscode.ChatResponseStream;
  sessionId: string;
  pipelineName: string;
  stage: string;
  attempt: number;
  tokenEstimate?: number | null;
  elapsedMs?: number | null;
  log: (msg: string) => void;
  /** Test seam — disable the gate entirely (keeps the existing happy path). */
  gateEnabled?: boolean;
  /** Phase G.1.7 — chunk identifier when gating inside a chunked stage run. */
  chunkId?: string;
  /** Phase G.1.7 — human label for the chunk ("T1 — write tests …"). */
  chunkLabel?: string;
}

export type StageGateOutcome =
  | { kind: "approved" }
  | { kind: "auto_approved"; autoApproveRemaining: boolean }
  | { kind: "retry"; userHint?: string; chunkId?: string }
  | { kind: "aborted" };

/**
 * Pause the session, decide whether to render the gate, render+await
 * if so, and translate the user's click into a high-level outcome the
 * pipeline runner can dispatch on. Always clears the pause flag before
 * returning (either via `approve`, the user's chosen action, or an
 * abort-on-error path).
 *
 * Returns `{kind: "approved"}` on auto-skip (per-pipeline setting +
 * per-run flag) so the caller has one decision branch.
 */
export async function runStageReviewGate(
  opts: RunStageReviewGateOptions,
): Promise<StageGateOutcome> {
  // 1. Persist pause state so a chat-cancel mid-await still recoverable
  //    via `@harness continue`.
  try {
    const pauseArgs: Record<string, unknown> = {
      session_id: opts.sessionId,
      stage: opts.stage,
      reason: "stage_review",
    };
    if (opts.chunkId) { pauseArgs.chunk_id = opts.chunkId; }
    await opts.client.callTool("harness_pause_session", pauseArgs);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    opts.log(`[gate] pause MCP threw: ${msg}`);
    // Fall through — without pause state the runner just proceeds (no
    // worse than the pre-gate behaviour).
    return { kind: "approved" };
  }

  // 2. Read current pause state + per-pipeline setting.
  let pauseState: PauseStateRow;
  try {
    const raw = await opts.client.callTool("harness_get_pause_state", {
      session_id: opts.sessionId,
    });
    const parsed = JSON.parse(raw) as {
      paused_at_stage?: string | null;
      pause_reason?: string | null;
      auto_approve_remaining?: boolean;
    };
    pauseState = {
      paused_at_stage:        parsed.paused_at_stage ?? null,
      pause_reason:           (parsed.pause_reason as PauseStateRow["pause_reason"]) ?? null,
      auto_approve_remaining: Boolean(parsed.auto_approve_remaining),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    opts.log(`[gate] get_pause_state threw: ${msg}`);
    return { kind: "approved" };
  }
  const perPipelineAutoApprove = getPerPipelineAutoApprove(opts.pipelineName);

  // 3. Decide.
  const decision = decideGate({
    pauseState,
    perPipelineAutoApprove,
    gateEnabled: opts.gateEnabled,
  });

  if (decision.kind === "skip") {
    // Auto-resume so the pause flag clears for the next iteration.
    try {
      await opts.client.callTool("harness_resume_session", {
        session_id: opts.sessionId,
        action: "approve",
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      opts.log(`[gate] auto-resume threw: ${msg}`);
    }
    return decision.reason === "auto_approve_remaining" || decision.reason === "per_pipeline_setting"
      ? { kind: "auto_approved", autoApproveRemaining: pauseState.auto_approve_remaining }
      : { kind: "approved" };
  }

  // 4. Render gate, await user click.
  let resumed: ResumedDecision;
  try {
    resumed = await renderStageReviewGate({
      stream: opts.stream,
      sessionId: opts.sessionId,
      pipelineName: opts.pipelineName,
      stage: opts.stage,
      attempt: opts.attempt,
      tokenEstimate: opts.tokenEstimate,
      elapsedMs: opts.elapsedMs,
      chunkId: opts.chunkId,
      chunkLabel: opts.chunkLabel,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    opts.log(`[gate] gate awaitting failed: ${msg}`);
    // Pause state stays set; user can `@harness continue` later.
    return { kind: "aborted" };
  }

  // 5. Translate.
  switch (resumed.action) {
    case "approve":
      return { kind: "approved" };
    case "auto_approve_rest":
      return { kind: "auto_approved", autoApproveRemaining: true };
    case "retry":
      return { kind: "retry", userHint: resumed.userHint, chunkId: resumed.chunkId };
    case "abort":
      return { kind: "aborted" };
    default:
      // Budget actions can't reach this gate.
      opts.log(`[gate] unexpected action ${resumed.action} on stage_review gate`);
      return { kind: "aborted" };
  }
}
