import { test } from "node:test";
import assert from "node:assert/strict";

import {
  autoApproveSettingKey,
  BUDGET_ESCALATION_ACTIONS,
  buildBudgetEscalationButtons,
  buildResumeCommandArgs,
  buildStageReviewButtons,
  decideGate,
  DEFAULT_GRANT_AMOUNT,
  isActionValidFor,
  parseResumeCommandArgs,
  parseToggleAutoApproveArgs,
  RESUME_COMMAND_ID,
  renderAutoApproveToggleLabel,
  renderBudgetGateMarkdown,
  renderStageGateMarkdown,
  STAGE_REVIEW_ACTIONS,
  TOGGLE_AUTO_APPROVE_COMMAND_ID,
  type ResumeCommandArgs,
} from "./pipelineGate";

// ── isActionValidFor ─────────────────────────────────────────────────────────

test("isActionValidFor: stage_review accepts the four review actions", () => {
  for (const a of STAGE_REVIEW_ACTIONS) {
    assert.equal(isActionValidFor("stage_review", a), true, `expected ${a} to be valid`);
  }
});

test("isActionValidFor: budget_exhausted accepts the three budget actions", () => {
  for (const a of BUDGET_ESCALATION_ACTIONS) {
    assert.equal(isActionValidFor("budget_exhausted", a), true, `expected ${a} to be valid`);
  }
});

test("isActionValidFor: stage_review rejects budget actions and vice versa", () => {
  assert.equal(isActionValidFor("stage_review", "grant"), false);
  assert.equal(isActionValidFor("stage_review", "force"), false);
  assert.equal(isActionValidFor("budget_exhausted", "approve"), false);
  assert.equal(isActionValidFor("budget_exhausted", "retry"), false);
  assert.equal(isActionValidFor("budget_exhausted", "auto_approve_rest"), false);
});

test("isActionValidFor: rejects garbage strings", () => {
  assert.equal(isActionValidFor("stage_review", "yolo"), false);
  assert.equal(isActionValidFor("budget_exhausted", ""), false);
});

// ── decideGate ───────────────────────────────────────────────────────────────

test("decideGate: render when paused with reason and no auto-approve", () => {
  const d = decideGate({
    pauseState: { paused_at_stage: "plan", pause_reason: "stage_review", auto_approve_remaining: false },
    perPipelineAutoApprove: false,
  });
  assert.equal(d.kind, "render");
  if (d.kind === "render") { assert.equal(d.pauseReason, "stage_review"); }
});

test("decideGate: skip when auto_approve_remaining=true (per-run)", () => {
  const d = decideGate({
    pauseState: { paused_at_stage: "plan", pause_reason: "stage_review", auto_approve_remaining: true },
    perPipelineAutoApprove: false,
  });
  assert.deepEqual(d, { kind: "skip", reason: "auto_approve_remaining" });
});

test("decideGate: skip when per-pipeline setting is on", () => {
  const d = decideGate({
    pauseState: { paused_at_stage: "plan", pause_reason: "stage_review", auto_approve_remaining: false },
    perPipelineAutoApprove: true,
  });
  assert.deepEqual(d, { kind: "skip", reason: "per_pipeline_setting" });
});

test("decideGate: skip when gateEnabled=false (test seam)", () => {
  const d = decideGate({
    pauseState: { paused_at_stage: "plan", pause_reason: "stage_review", auto_approve_remaining: false },
    perPipelineAutoApprove: false,
    gateEnabled: false,
  });
  assert.deepEqual(d, { kind: "skip", reason: "gate_disabled" });
});

test("decideGate: skip when no pause flag is set", () => {
  const d = decideGate({
    pauseState: { paused_at_stage: null, pause_reason: null, auto_approve_remaining: false },
    perPipelineAutoApprove: false,
  });
  assert.deepEqual(d, { kind: "skip", reason: "gate_disabled" });
});

test("decideGate: per-run auto_approve takes precedence over per-pipeline setting", () => {
  // If both are true, both reasons would be valid skip reasons; the
  // per-run override wins to make the diagnostic clear in logs.
  const d = decideGate({
    pauseState: { paused_at_stage: "plan", pause_reason: "stage_review", auto_approve_remaining: true },
    perPipelineAutoApprove: true,
  });
  assert.deepEqual(d, { kind: "skip", reason: "auto_approve_remaining" });
});

// ── markdown builders ───────────────────────────────────────────────────────

test("renderStageGateMarkdown: includes pipeline, stage, attempt", () => {
  const md = renderStageGateMarkdown({
    pipelineName: "feature-dev", stage: "plan", attempt: 1,
    autoApproveOn: false,
  });
  assert.match(md, /\/feature-dev/);
  assert.match(md, /stage `plan`/);
  assert.match(md, /attempt 1/);
});

test("renderStageGateMarkdown: token + ms metadata when provided", () => {
  const md = renderStageGateMarkdown({
    pipelineName: "feature-dev", stage: "code", attempt: 2,
    tokenEstimate: 4321, elapsedMs: 12500,
    autoApproveOn: false,
  });
  assert.match(md, /4,321t/);
  assert.match(md, /13s/);
});

test("renderStageGateMarkdown: omits secs/tokens when missing", () => {
  const md = renderStageGateMarkdown({
    pipelineName: "feature-dev", stage: "design", attempt: 1,
    autoApproveOn: false,
  });
  assert.equal(md.includes("t |"), false);
  // attempt only, no extra meta blocks.
  const metaSegment = md.split("·").slice(1).join("·");
  assert.match(metaSegment, /attempt 1/);
});

test("renderBudgetGateMarkdown: includes used/limit ratio", () => {
  const md = renderBudgetGateMarkdown({
    pipelineName: "feature-dev", stage: "coder", attempt: 1, used: 5, limit: 5,
  });
  assert.match(md, /5\/5/);
  assert.match(md, /coder/);
});

test("renderAutoApproveToggleLabel: reflects ON/OFF state", () => {
  assert.match(renderAutoApproveToggleLabel("feature-dev", true), /\*\*ON\*\* — click to turn OFF/);
  assert.match(renderAutoApproveToggleLabel("feature-dev", false), /\*\*OFF\*\* — click to turn ON/);
});

// ── Command-arg builders + parsers ──────────────────────────────────────────

const baseStageArgs = {
  sessionId: "sess1",
  pipelineName: "feature-dev",
  stage: "plan",
  attempt: 1,
  pauseReason: "stage_review" as const,
};

test("buildResumeCommandArgs: includes action; no extras by default", () => {
  const args = buildResumeCommandArgs(baseStageArgs, "approve");
  assert.equal(args.action, "approve");
  assert.equal(args.extraBudget, undefined);
  assert.equal(args.promptForHint, undefined);
});

test("buildResumeCommandArgs: extras flow through", () => {
  const args = buildResumeCommandArgs(
    { ...baseStageArgs, pauseReason: "budget_exhausted" },
    "grant",
    { extraBudget: 3 },
  );
  assert.equal(args.extraBudget, 3);
});

test("parseResumeCommandArgs: round-trips a canonical args object", () => {
  const built: ResumeCommandArgs = buildResumeCommandArgs(baseStageArgs, "retry", { promptForHint: true });
  const parsed = parseResumeCommandArgs(built);
  assert.deepEqual(parsed, built);
});

test("parseResumeCommandArgs: rejects non-object / missing fields", () => {
  assert.equal(parseResumeCommandArgs(null), null);
  assert.equal(parseResumeCommandArgs("garbage"), null);
  assert.equal(parseResumeCommandArgs({}), null);
  // missing action
  const noAction = { ...baseStageArgs };
  assert.equal(parseResumeCommandArgs(noAction), null);
  // attempt as string (not number)
  const badAttempt = { ...baseStageArgs, attempt: "1" as unknown as number, action: "approve" };
  assert.equal(parseResumeCommandArgs(badAttempt), null);
  // empty sessionId
  const emptySession = { ...baseStageArgs, sessionId: "", action: "approve" };
  assert.equal(parseResumeCommandArgs(emptySession), null);
});

test("parseResumeCommandArgs: rejects action that does not match pauseReason", () => {
  const bad = { ...baseStageArgs, action: "grant" };  // grant is budget-only
  assert.equal(parseResumeCommandArgs(bad), null);
});

test("parseResumeCommandArgs: rejects unknown pauseReason", () => {
  const bad = { ...baseStageArgs, pauseReason: "yolo", action: "approve" };
  assert.equal(parseResumeCommandArgs(bad), null);
});

test("parseToggleAutoApproveArgs: round-trips", () => {
  assert.deepEqual(parseToggleAutoApproveArgs({ pipelineName: "feature-dev" }), { pipelineName: "feature-dev" });
});

test("parseToggleAutoApproveArgs: rejects missing pipelineName", () => {
  assert.equal(parseToggleAutoApproveArgs({}), null);
  assert.equal(parseToggleAutoApproveArgs(null), null);
});

// ── Button builders ─────────────────────────────────────────────────────────

test("buildStageReviewButtons: emits four buttons in canonical order", () => {
  const btns = buildStageReviewButtons(baseStageArgs);
  assert.deepEqual(btns.map(b => b.args.action), ["approve", "retry", "abort", "auto_approve_rest"]);
  // Retry button signals the shell to prompt for a hint before dispatching.
  const retry = btns.find(b => b.args.action === "retry");
  assert.equal(retry?.args.promptForHint, true);
});

test("buildStageReviewButtons: throws on wrong pause_reason", () => {
  assert.throws(() => buildStageReviewButtons({ ...baseStageArgs, pauseReason: "budget_exhausted" }));
});

test("buildBudgetEscalationButtons: emits three buttons; grant carries extraBudget", () => {
  const base = { ...baseStageArgs, pauseReason: "budget_exhausted" as const };
  const btns = buildBudgetEscalationButtons(base);
  assert.deepEqual(btns.map(b => b.args.action), ["grant", "force", "abort"]);
  const grant = btns.find(b => b.args.action === "grant");
  assert.equal(grant?.args.extraBudget, DEFAULT_GRANT_AMOUNT);
});

test("buildBudgetEscalationButtons: respects custom grant amount", () => {
  const base = { ...baseStageArgs, pauseReason: "budget_exhausted" as const };
  const btns = buildBudgetEscalationButtons(base, 7);
  const grant = btns.find(b => b.args.action === "grant");
  assert.equal(grant?.args.extraBudget, 7);
  assert.match(grant?.title ?? "", /\+7 spawns/);
});

test("buildBudgetEscalationButtons: throws on wrong pause_reason", () => {
  assert.throws(() => buildBudgetEscalationButtons(baseStageArgs));
});

// ── Setting-key helper ──────────────────────────────────────────────────────

test("autoApproveSettingKey: encodes per-pipeline scope", () => {
  assert.equal(autoApproveSettingKey("feature-dev"), "autoApprove.feature-dev");
  assert.equal(autoApproveSettingKey("code-review"), "autoApprove.code-review");
});

test("Command IDs are stable strings", () => {
  // Pinning these prevents an unannounced rename from breaking the
  // package.json command declarations.
  assert.equal(RESUME_COMMAND_ID, "copilot-harness.resumeSession");
  assert.equal(TOGGLE_AUTO_APPROVE_COMMAND_ID, "copilot-harness.toggleAutoApprove");
});
