import { test } from "node:test";
import assert from "node:assert/strict";

import {
  coerceToEscalation,
  DEFAULT_ESCALATION_RULES,
  parseEscalationRules,
  REVIEWER_CATEGORIES,
  REVIEWER_SEVERITIES,
  shouldEscalate,
  type EscalationRules,
  type ReviewLike,
} from "./correctionRules";

// ── parseEscalationRules ─────────────────────────────────────────────────

test("parseEscalationRules: defaults when input is null / non-object", () => {
  assert.deepEqual(parseEscalationRules(null), DEFAULT_ESCALATION_RULES);
  assert.deepEqual(parseEscalationRules(undefined), DEFAULT_ESCALATION_RULES);
  assert.deepEqual(parseEscalationRules("garbage"), DEFAULT_ESCALATION_RULES);
});

test("parseEscalationRules: explicit escalate_on_critical=false respected", () => {
  const r = parseEscalationRules({ escalate_on_critical: false });
  assert.equal(r.escalateOnCritical, false);
  assert.deepEqual(r.escalateOnCategories, []);
});

test("parseEscalationRules: escalate_on_categories filters strings", () => {
  const r = parseEscalationRules({
    escalate_on_categories: ["security", 42, "", "data-loss", null],
  });
  assert.deepEqual(r.escalateOnCategories, ["security", "data-loss"]);
});

test("parseEscalationRules: non-list categories falls back to empty", () => {
  const r = parseEscalationRules({ escalate_on_categories: "security" });
  assert.deepEqual(r.escalateOnCategories, []);
});

test("parseEscalationRules: non-bool escalate_on_critical falls to default", () => {
  const r = parseEscalationRules({ escalate_on_critical: "true" });
  assert.equal(r.escalateOnCritical, DEFAULT_ESCALATION_RULES.escalateOnCritical);
});

// ── shouldEscalate: critical-rule path ────────────────────────────────────

test("shouldEscalate: critical issue + escalateOnCritical=true ⇒ escalate", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{ severity: "critical", description: "SQLi", fix_instruction: "use params" }],
  };
  const decision = shouldEscalate(review, { escalateOnCritical: true, escalateOnCategories: [] });
  assert.equal(decision.shouldEscalate, true);
  assert.equal(decision.matchingIssues.length, 1);
  assert.match(decision.reason, /critical/);
});

test("shouldEscalate: critical issue + escalateOnCritical=false ⇒ no escalate", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{ severity: "critical", description: "SQLi", fix_instruction: "use params" }],
  };
  const decision = shouldEscalate(review, { escalateOnCritical: false, escalateOnCategories: [] });
  assert.equal(decision.shouldEscalate, false);
});

test("shouldEscalate: high-severity alone doesn't trigger critical rule", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{ severity: "high", description: "no auth", fix_instruction: "add JWT" }],
  };
  const decision = shouldEscalate(review, { escalateOnCritical: true, escalateOnCategories: [] });
  assert.equal(decision.shouldEscalate, false);
});

// ── shouldEscalate: category-rule path ────────────────────────────────────

const securityRules: EscalationRules = {
  escalateOnCritical: false,
  escalateOnCategories: ["security", "data-loss"],
};

test("shouldEscalate: high-severity in matching category ⇒ escalate", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{
      severity: "high", category: "security",
      description: "open redirect", fix_instruction: "validate URL",
    }],
  };
  const decision = shouldEscalate(review, securityRules);
  assert.equal(decision.shouldEscalate, true);
  assert.match(decision.reason, /security/);
});

test("shouldEscalate: critical in matching category ⇒ escalate", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{
      severity: "critical", category: "data-loss",
      description: "drops users table", fix_instruction: "guard migration",
    }],
  };
  const decision = shouldEscalate(review, securityRules);
  assert.equal(decision.shouldEscalate, true);
});

test("shouldEscalate: medium in matching category does NOT trigger", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{
      severity: "medium", category: "security",
      description: "weak hash", fix_instruction: "bcrypt",
    }],
  };
  const decision = shouldEscalate(review, securityRules);
  assert.equal(decision.shouldEscalate, false);
});

test("shouldEscalate: high in non-matching category does NOT trigger", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{
      severity: "high", category: "performance",
      description: "N+1 query", fix_instruction: "join",
    }],
  };
  const decision = shouldEscalate(review, securityRules);
  assert.equal(decision.shouldEscalate, false);
});

// ── shouldEscalate: combined rules + dedup ─────────────────────────────

test("shouldEscalate: critical + category match dedups the issue list", () => {
  const issue = {
    severity: "critical", category: "security",
    description: "RCE", fix_instruction: "...",
  };
  const review: ReviewLike = { status: "fail", issues: [issue] };
  const decision = shouldEscalate(review, {
    escalateOnCritical: true,
    escalateOnCategories: ["security"],
  });
  assert.equal(decision.shouldEscalate, true);
  assert.equal(decision.matchingIssues.length, 1, "one issue matches both rules; should not duplicate");
});

test("shouldEscalate: empty issues list ⇒ no escalation", () => {
  const decision = shouldEscalate(
    { status: "pass", issues: [] },
    { escalateOnCritical: true, escalateOnCategories: ["security"] },
  );
  assert.equal(decision.shouldEscalate, false);
});

test("shouldEscalate: missing issues field treated as empty", () => {
  const decision = shouldEscalate(
    { status: "pass" },
    { escalateOnCritical: true, escalateOnCategories: [] },
  );
  assert.equal(decision.shouldEscalate, false);
});

// ── coerceToEscalation ───────────────────────────────────────────────

test("coerceToEscalation: returns input unchanged when shouldn't escalate", () => {
  const review: ReviewLike = { status: "fail", issues: [] };
  const result = coerceToEscalation(review, {
    shouldEscalate: false, matchingIssues: [], reason: "",
  });
  assert.equal(result, review);
});

test("coerceToEscalation: sets status='escalate' + escalate_reason", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [{ severity: "critical", description: "x", fix_instruction: "y" }],
  };
  const result = coerceToEscalation(review, {
    shouldEscalate: true,
    matchingIssues: review.issues!,
    reason: "Pipeline rule triggered: 1 critical-severity issue(s)",
  });
  assert.equal(result.status, "escalate");
  assert.match(result.escalate_reason ?? "", /critical/);
});

test("coerceToEscalation: preserves the original issues array", () => {
  const review: ReviewLike = {
    status: "fail",
    issues: [
      { severity: "critical", description: "x", fix_instruction: "y" },
      { severity: "low", description: "nit", fix_instruction: "z" },
    ],
  };
  const result = coerceToEscalation(review, {
    shouldEscalate: true,
    matchingIssues: [review.issues![0]],
    reason: "...",
  });
  assert.equal(result.issues!.length, 2);
});

// ── Vocabulary pins (mirror Python REVIEWER_CATEGORY_ENUM) ────────────

test("REVIEWER_CATEGORIES contains the 7 G.2 values", () => {
  assert.deepEqual([...REVIEWER_CATEGORIES].sort(), [
    "breaking-change", "correctness", "data-loss",
    "other", "performance", "security", "style",
  ]);
});

test("REVIEWER_SEVERITIES contains the 4 standard values", () => {
  assert.deepEqual([...REVIEWER_SEVERITIES].sort(), [
    "critical", "high", "low", "medium",
  ]);
});
