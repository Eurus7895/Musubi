/**
 * harness-tier: ephemeral
 * expires-when: models gain reliable native multi-agent tool-use
 * cost-lever: deletes the sub-agent core
 * (what: Sub-agent runner pure logic.)
 */
/**
 * runners/subagentRunnerCore.ts — pure helpers for the pipeline-side
 * sub-agent runners (Phase G.1). No vscode imports so node:test can
 * exercise them without a runtime; the vscode-using shell lives in
 * subagentRunner.ts.
 *
 * Generalises the summarizerCore pattern to the three roles a pipeline
 * stage may spawn: explorer (read-only workspace scan), investigator
 * (read + diagnostic shell), reviewer-aux (per-file checklist review).
 * Each role's static config (agent.md path, default LM tool surface,
 * turn / wall-clock budgets) is data, not code, so the shell stays
 * role-agnostic.
 */

import { stripFrontmatter } from "./agentCore";

/** Sub-agent roles this PR ships runners for. */
export type SubagentRoleId = "explorer" | "investigator" | "reviewer-aux";

/**
 * Static per-role config consumed by the generic runner. Lives here as
 * plain data so the role wrappers (explorerRunner.ts etc.) become
 * five-line shells and tests can drive the same code paths with a
 * fake config.
 */
export interface SubagentRoleConfig {
  /** Role identifier accepted by harness_spawn_subagent. */
  readonly role: SubagentRoleId;
  /** Agent name passed to selectModelForAgent (matches `<role>.agent.md`). */
  readonly agentName: string;
  /** Workspace-relative path to the agent.md, resolved against `roots`. */
  readonly agentMdRel: string;
  /** Hard ceiling on tool-call cycles in the runner's LM loop. */
  readonly maxTurns: number;
  /** Wall-clock cap (seconds) handed to harness_spawn_subagent. */
  readonly wallClockS: number;
  /**
   * VS Code LM tool names the role is allowed to call. The runner
   * intersects this with `vscode.lm.tools` at runtime so a tool that
   * the workbench never registered is silently dropped instead of
   * crashing. The declarative source of truth is the agent's
   * `lm_tools:` frontmatter; this constant stays in sync with it but
   * is the runtime fallback when the agent file can't be read (tests,
   * a stripped-down packaging, etc.).
   */
  readonly defaultLmTools: readonly string[];
}

/**
 * Static role catalog. Mirrors `.github/agents/<role>.agent.md::lm_tools`
 * — keep both lists in sync. A drift check is written into the unit
 * tests so a missed update fails CI loudly.
 */
export const SUBAGENT_ROLE_CONFIGS: Readonly<Record<SubagentRoleId, SubagentRoleConfig>> = {
  "explorer": {
    role: "explorer",
    agentName: "explorer",
    agentMdRel: ".github/agents/explorer.agent.md",
    maxTurns: 6,
    wallClockS: 30,
    defaultLmTools: [
      "copilot_readFile", "read_file",
      "copilot_listDirectory", "list_dir",
      "copilot_searchWorkspace", "grep_search",
      "copilot_findFiles", "file_search",
    ],
  },
  "investigator": {
    role: "investigator",
    agentName: "investigator",
    agentMdRel: ".github/agents/investigator.agent.md",
    maxTurns: 6,
    wallClockS: 60,
    defaultLmTools: [
      "copilot_readFile", "read_file",
      "copilot_listDirectory", "list_dir",
      "copilot_searchWorkspace", "grep_search",
      "copilot_findFiles", "file_search",
      "copilot_getErrors", "get_errors",
      "copilot_runInTerminal", "run_in_terminal",
    ],
  },
  "reviewer-aux": {
    role: "reviewer-aux",
    agentName: "reviewer-aux",
    agentMdRel: ".github/agents/reviewer-aux.agent.md",
    maxTurns: 4,
    wallClockS: 30,
    defaultLmTools: [
      "copilot_readFile", "read_file",
    ],
  },
};

/**
 * Parsed shape of the harness_get_subagent_context response.
 * Mirrors `validation/subagent_context.py::SubagentContext`.
 */
export interface SubagentContext {
  brief: string;
  role: string;
  roleSkill: string | null;
  /**
   * Tool allow-list for the spawned sub-session. Comes from the policy
   * engine intersected with anything the spawner narrowed via
   * `allowed_tools`. The runner uses this to drive the LM tool surface,
   * not the agent's static tool list, because the spawner may have
   * narrowed it further (e.g. coder spawns explorer with only
   * ["Read","Grep"] for a tighter scan).
   */
  allowedTools: string[];
}

/**
 * Parse `harness_get_subagent_context`'s JSON envelope.
 * Returns null on any malformed input — caller is expected to abandon
 * the handle and report a context-fetch failure rather than press on
 * with a partial brief.
 */
export function parseSubagentContext(raw: string): SubagentContext | null {
  if (!raw) { return null; }
  let obj: unknown;
  try { obj = JSON.parse(raw); } catch { return null; }
  if (!obj || typeof obj !== "object") { return null; }
  const o = obj as Record<string, unknown>;
  if (o.status !== "ok") { return null; }
  if (typeof o.brief !== "string" || typeof o.role !== "string") { return null; }
  const allowed = Array.isArray(o.allowed_tools)
    ? o.allowed_tools.filter((t): t is string => typeof t === "string")
    : [];
  return {
    brief: o.brief,
    role: o.role,
    roleSkill: typeof o.role_skill === "string" ? o.role_skill : null,
    allowedTools: allowed,
  };
}

/** Parsed shape of harness_spawn_subagent's success response. */
export interface SpawnResponse {
  status: "spawned" | "error";
  handleId?: string;
  effectiveTools?: string[];
  error?: string;
}

export function parseSpawnResponse(raw: string): SpawnResponse {
  if (!raw) { return { status: "error", error: "empty response" }; }
  let obj: unknown;
  try { obj = JSON.parse(raw); } catch {
    return { status: "error", error: `non-JSON spawn response: ${raw.slice(0, 200)}` };
  }
  if (!obj || typeof obj !== "object") {
    return { status: "error", error: "spawn response not an object" };
  }
  const o = obj as Record<string, unknown>;
  if (o.status === "spawned" && typeof o.handle_id === "string") {
    const tools = Array.isArray(o.effective_tools)
      ? o.effective_tools.filter((t): t is string => typeof t === "string")
      : undefined;
    return { status: "spawned", handleId: o.handle_id, effectiveTools: tools };
  }
  const err = typeof o.error === "string" ? o.error : `spawn failed: ${raw.slice(0, 200)}`;
  return { status: "error", error: err };
}

/** Parsed shape of harness_complete_subagent's response. */
export interface CompleteResponse {
  ok: boolean;
  finalStatus?: string;
  summary?: string;
  verificationErrors?: string[];
  reason?: string;
}

export function parseCompleteResponse(raw: string): CompleteResponse {
  if (!raw) { return { ok: false, reason: "empty completion response" }; }
  let obj: unknown;
  try { obj = JSON.parse(raw); } catch {
    return { ok: false, reason: `non-JSON completion response: ${raw.slice(0, 200)}` };
  }
  if (!obj || typeof obj !== "object") {
    return { ok: false, reason: "completion response not an object" };
  }
  const o = obj as Record<string, unknown>;
  const errs = Array.isArray(o.verification_errors)
    ? o.verification_errors.filter((e): e is string => typeof e === "string")
    : undefined;
  if (o.status !== "recorded") {
    return {
      ok: false,
      finalStatus: typeof o.final_status === "string" ? o.final_status : undefined,
      summary: typeof o.summary === "string" ? o.summary : undefined,
      verificationErrors: errs,
      reason: typeof o.error === "string"
        ? o.error
        : (errs && errs.length > 0 ? errs.join("; ") : "completion not recorded"),
    };
  }
  return {
    ok: o.final_status === "done",
    finalStatus: typeof o.final_status === "string" ? o.final_status : undefined,
    summary: typeof o.summary === "string" ? o.summary : undefined,
    verificationErrors: errs,
    reason: o.final_status === "done"
      ? undefined
      : (errs && errs.length > 0 ? errs.join("; ") : `final_status=${String(o.final_status)}`),
  };
}

/**
 * Build the sub-agent's system prompt: stripped agent.md + role_skill +
 * brief. Mirrors buildSummarizerSystemPrompt's shape so the eventual
 * Phase-D collapse can fold all three into one helper.
 *
 * The brief is appended as a prominent section because the runner's
 * LM-side conversation does not get a separate user message containing
 * it — the brief IS the task.
 */
export function buildSubagentSystemPrompt(
  agentMd: string,
  roleSkill: string | null,
  brief: string,
): string {
  const parts: string[] = [stripFrontmatter(agentMd).trim()];
  const skill = (roleSkill ?? "").trim();
  if (skill.length > 0) {
    parts.push(
      "\n\n## Skill (pushed by harness)\n\n" + stripFrontmatter(skill).trim(),
    );
  }
  parts.push(
    "\n\n## Brief\n\n" + brief.trim() +
    "\n\nFollow the Output Contract in your role section. " +
    "Produce your answer as plain text; the harness captures and verifies it on completion.",
  );
  return parts.join("");
}

/**
 * Compute the LM tool surface for a sub-agent run: intersect the
 * spawn-time allow-list (from the harness) with the role's declared
 * `defaultLmTools` and what the workbench has actually registered.
 *
 * Why three layers?
 *   - `harness allowed_tools` reflects policy + caller narrowing — the
 *     final source of truth for what the role MAY use.
 *   - `defaultLmTools` keeps the runner working offline / in tests when
 *     the agent.md hasn't been bundled.
 *   - `availableLmTools` reflects what the live VS Code workbench has
 *     registered — calling an unregistered tool throws.
 *
 * The harness `allowed_tools` uses Copilot's symbolic names (Read,
 * Grep, etc.); the LM API uses concrete tool names (copilot_readFile,
 * read_file, ...). The mapping is many-to-many — `Read` corresponds to
 * both `copilot_readFile` and the workspace's `read_file`. We resolve
 * via SYMBOLIC_TO_LM_NAMES below.
 */
export const SYMBOLIC_TO_LM_NAMES: Readonly<Record<string, readonly string[]>> = {
  "Read":  ["copilot_readFile", "read_file"],
  "View":  ["copilot_readFile", "read_file"],
  "Grep":  ["copilot_searchWorkspace", "grep_search"],
  "Glob":  ["copilot_findFiles", "file_search"],
  "List":  ["copilot_listDirectory", "list_dir"],
  "Bash":  ["copilot_runInTerminal"],
  "Errors": ["copilot_getErrors"],
};

export function resolveLmToolSurface(args: {
  harnessAllowedTools: readonly string[];
  roleDefaultLmTools: readonly string[];
  availableLmTools: readonly string[];
}): string[] {
  const { harnessAllowedTools, roleDefaultLmTools, availableLmTools } = args;
  const available = new Set(availableLmTools);

  // Translate symbolic role-allow-list entries into concrete LM tool names.
  const roleConcrete = new Set<string>();
  for (const sym of harnessAllowedTools) {
    const concretes = SYMBOLIC_TO_LM_NAMES[sym];
    if (concretes) {
      for (const c of concretes) { roleConcrete.add(c); }
    } else {
      // Already a concrete name — pass through.
      roleConcrete.add(sym);
    }
  }
  // If the harness side returned no names at all (e.g. role with no
  // tools — summarizer-shaped), fall back to the role's declared
  // defaults so the runner still has *something* to advertise. The
  // policy engine remains authoritative on actual tool dispatch via
  // PreToolUse, so a misconfigured fallback can't escalate privileges.
  const filtered = roleConcrete.size === 0 ? new Set(roleDefaultLmTools) : roleConcrete;

  const out: string[] = [];
  for (const t of filtered) {
    if (available.has(t)) { out.push(t); }
  }
  // Stable order so logs / tests don't depend on Set iteration.
  out.sort();
  return out;
}
