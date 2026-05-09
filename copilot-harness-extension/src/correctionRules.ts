/**
 * correctionRules.ts — Phase G.2 escalate-on-* helpers.
 *
 * The correction loop today retries up to `max_retries` regardless of
 * how bad the reviewer's findings are. G.2 lets pipelines declare:
 *
 *   correction:
 *     max_retries: 3
 *     escalate_on_critical: true     # default true
 *     escalate_on_categories:        # optional list
 *       - security
 *       - data-loss
 *
 * When the reviewer reports an issue matching the rules, the runner
 * coerces the review status to 'escalate' immediately — no retry.
 *
 * Pure helpers; no vscode imports so node:test can exercise them
 * directly. Wired into pipeline.ts::runCorrectionLoop.
 */

// ── Vocabulary ──────────────────────────────────────────────────────────

/** Categories the reviewer may emit (Phase G.2 vocabulary). */
export const REVIEWER_CATEGORIES = [
  "security",
  "data-loss",
  "performance",
  "style",
  "correctness",
  "breaking-change",
  "other",
] as const;
export type ReviewerCategory = typeof REVIEWER_CATEGORIES[number];

/** Severity values the reviewer may emit. */
export const REVIEWER_SEVERITIES = ["critical", "high", "medium", "low"] as const;
export type ReviewerSeverity = typeof REVIEWER_SEVERITIES[number];

/** Shape of one review issue (G.2). */
export interface ReviewIssue {
  severity?: string;
  category?: string;
  description?: string;
  fix_instruction?: string;
}

/** Shape of the reviewer's output relevant to correction-rule evaluation. */
export interface ReviewLike {
  status?: string;
  issues?: ReviewIssue[];
  escalate_reason?: string | null;
}

// ── Rule definition ────────────────────────────────────────────────────

/**
 * Escalation rules for one pipeline's correction loop. Sourced from
 * `pipeline.yaml::correction`. Defaults applied at parse time so the
 * runner only ever sees a fully-resolved object.
 */
export interface EscalationRules {
  /**
   * When true, ANY issue with severity='critical' triggers immediate
   * escalation — no retry. Default true.
   */
  escalateOnCritical: boolean;
  /**
   * Optional list of categories that trigger escalation when paired
   * with severity ∈ {critical, high}. e.g. ["security", "data-loss"]
   * escalates a high-severity security finding even if
   * escalateOnCritical=false. Default empty list.
   */
  escalateOnCategories: ReadonlyArray<string>;
}

/** Default rules when pipeline.yaml omits `correction.escalate_on_*`. */
export const DEFAULT_ESCALATION_RULES: EscalationRules = {
  escalateOnCritical: true,
  escalateOnCategories: [],
};

/**
 * Parse `correction:` block from a pipeline.yaml-shaped object. Tolerates
 * missing or malformed input by falling back to the field's default.
 */
export function parseEscalationRules(correctionBlock: unknown): EscalationRules {
  if (!correctionBlock || typeof correctionBlock !== "object") {
    return { ...DEFAULT_ESCALATION_RULES };
  }
  const obj = correctionBlock as Record<string, unknown>;
  const onCritical = obj.escalate_on_critical;
  const onCategories = obj.escalate_on_categories;
  return {
    escalateOnCritical:
      typeof onCritical === "boolean" ? onCritical : DEFAULT_ESCALATION_RULES.escalateOnCritical,
    escalateOnCategories:
      Array.isArray(onCategories)
        ? onCategories.filter((c): c is string => typeof c === "string" && c.length > 0)
        : [],
  };
}

// ── Decision ──────────────────────────────────────────────────────────

export interface EscalationDecision {
  /** True iff at least one issue triggers escalation. */
  shouldEscalate: boolean;
  /**
   * Issues that triggered the decision. Useful for the runner's chat
   * message and the coerced review's `escalate_reason`.
   */
  matchingIssues: ReviewIssue[];
  /** Human-readable reason phrase for the escalate_reason field. */
  reason: string;
}

/**
 * Inspect a reviewer's output against the configured rules. Returns
 * a structured decision the runner can dispatch on without re-deriving
 * the reasoning.
 *
 * Rule semantics:
 *   - escalateOnCritical=true  AND  any issue.severity='critical'
 *     ⇒ escalate (regardless of category)
 *   - escalateOnCategories has any entry AND there's an issue with
 *     matching category AND severity ∈ {critical, high}
 *     ⇒ escalate
 *   - otherwise ⇒ don't escalate (correction loop continues normally)
 *
 * The "category at high+severity" rule prevents medium/low style nits
 * from escalating just because they happen to be in a sensitive
 * category. Critical escalation isn't gated on category — by
 * definition critical needs eyes regardless.
 */
export function shouldEscalate(
  review: ReviewLike,
  rules: EscalationRules,
): EscalationDecision {
  const issues = Array.isArray(review.issues) ? review.issues : [];
  const matched: ReviewIssue[] = [];
  const reasons: string[] = [];

  if (rules.escalateOnCritical) {
    const criticalIssues = issues.filter(
      i => typeof i?.severity === "string" && i.severity === "critical",
    );
    if (criticalIssues.length > 0) {
      matched.push(...criticalIssues);
      reasons.push(
        `${criticalIssues.length} critical-severity issue(s) (escalate_on_critical=true)`,
      );
    }
  }

  if (rules.escalateOnCategories.length > 0) {
    const categorySet = new Set(rules.escalateOnCategories);
    const categoryMatches = issues.filter(i => {
      if (!i || typeof i.category !== "string") { return false; }
      if (!categorySet.has(i.category)) { return false; }
      const sev = typeof i.severity === "string" ? i.severity : "";
      return sev === "critical" || sev === "high";
    });
    if (categoryMatches.length > 0) {
      // Avoid double-listing issues already matched by the critical rule.
      for (const m of categoryMatches) {
        if (!matched.includes(m)) { matched.push(m); }
      }
      const cats = [...new Set(categoryMatches.map(i => i.category))].sort().join(", ");
      reasons.push(
        `${categoryMatches.length} high+severity issue(s) in escalate_on_categories [${cats}]`,
      );
    }
  }

  if (matched.length === 0) {
    return { shouldEscalate: false, matchingIssues: [], reason: "" };
  }
  return {
    shouldEscalate: true,
    matchingIssues: matched,
    reason: `Pipeline rule triggered: ${reasons.join("; ")}.`,
  };
}

/**
 * Build the coerced review shape: status='escalate' with a synthetic
 * `escalate_reason` derived from the matching issues. Preserves the
 * original `issues` array so the user sees what the reviewer actually
 * found.
 */
export function coerceToEscalation(
  review: ReviewLike,
  decision: EscalationDecision,
): ReviewLike {
  if (!decision.shouldEscalate) { return review; }
  return {
    ...review,
    status: "escalate",
    escalate_reason: decision.reason,
  };
}
