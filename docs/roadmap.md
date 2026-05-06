# Build Roadmap

> Project-level build status, what's shipped, what's next, and Week-by-Week
> plan. Promoted out of `docs/design.md` so the design doc stays focused on
> architecture/schemas (which churn slowly) while this file absorbs the
> high-frequency status churn.
>
> Architecture and schemas → [`docs/design.md`](./design.md).
> Pipeline-specific roadmaps live alongside the pipeline (e.g.
> [`.github/pipelines/feature-dev/ROADMAP.md`](../.github/pipelines/feature-dev/ROADMAP.md)).

---


### Day 1–5 ✅ Complete
All core harness modules built. 260 tests passing.

### Week 2 ✅ Complete
3-tier memory. Edge case hardening. 260 tests.

### Week 3a ✅ Complete — Separate Evaluator Session
**Shipped.** Reviewer now runs as an evaluator with an isolated context.
```
[x] Reviewer context firewalled to {code} only — no request, plan, design,
      or prior review. Enforced in copilot-harness/context_builder.py
      (_STAGE_PERMISSIONS["reviewer"] = {"code"}; _context_reviewer()).
[x] Memory injection skipped for reviewer in server.py harness_read_stage.
[x] Dynamic plan.required_skills injection skipped for reviewer; the
      code-review skill static injection is retained (that IS the checklist).
[x] pipeline.ts AGENT_PIPELINE + runCorrectionLoop: reviewer readStages
      tightened to ["code"].
[x] reviewer.agent.md rewritten for the evaluator contract.
[x] Tests: 11 new assertions covering reviewer isolation
      (test_context_builder.py, test_skill_access.py).
[ ] Deferred — Level 1 vs Level 2 decision. Requires running the LM against
      the eval set and comparing pass rates. Plan: build a one-off
      single-generator probe, run 3–5 representative requests, decide.
      Threshold: ≥ 80% first-attempt pass → Level 1 viable.
```

**Known trade-off:** `wrong_plan` status now rarely fires — the reviewer
cannot see the plan. Accepted for Week 3a. If this produces regressions,
Week 3b+ can add a dedicated planner-feedback channel.

### Week 3b ✅ Complete — Pipeline Directory Migration
**Structural cleanup. No behavior change.**
```
[x] Create .github/pipelines/feature-dev/ directory
[x] Move planner/designer/coder/reviewer into pipeline directory;
      skill-builder stays at .github/agents/ (meta-agent, not pipeline-scoped)
[x] Add pipeline.yaml with level: 2 (Week 3a Level-1 probe deferred)
[x] Add YAML frontmatter to agent .md files (model, maxTurns, tools,
      disallowedTools)
[x] state.AGENTS_DIRS globs both pipeline dir + legacy dir (first wins)
[x] pipeline.ts loadAgentPrompt falls back to legacy path
[x] Mark .github/agents/ deprecated via README (keep for rollback, remove Week 5)
```

### Week 3c ✅ Complete — Direct Mode + Hooks + Commands
```
[x] Direct mode routing in extension.ts
      Slash commands → pipeline. --pipeline flag → pipeline.
      Everything else → direct (vscode.lm.sendRequest, no harness).
      No LLM routing call. Zero cost.
[x] hooks.json at repo root (SessionStart / PreToolUse / PostToolUse)
[x] scripts/pre_tool_use.py — policy engine (PIPELINE_POLICIES fail-closed)
[x] scripts/post_tool_use.py — SQLite audit log (storage/audit.db)
[x] scripts/session_start.py — runs pipeline.yaml baseline_checks
[x] harness_run_hook MCP tool in server.py (shells out, reports results)
[x] .github/commands/*.md — 7 slash command files, frontmatter-driven
[x] copilot-harness-extension/src/slashCommands.ts — loader + lister
[x] --pipeline flag detection in parseCommand()
[x] Tests: +59 new (pipeline YAML shape, policy engine, hooks, slash
      commands, multi-dir agent glob). Total now 334.
```

### Week 4 ✅ Complete — Multi-Agent Coordination + Unblock Week 5

```
Day 1 ✅ /help slash command (dynamic, data-driven)
  [x] Added "help" to SlashAction in slashCommands.ts + VALID_ACTIONS
  [x] .github/commands/help.md (action: help)
  [x] extension.ts buildHelpMarkdown() renders a table from
      listSlashCommands(workspaceRoot) — stays in sync with new commands
  [x] USAGE_HEADER + USAGE_FOOTER reused by both cmd.type=="help" and
      the slash "/help" route
  [x] test_slash_commands.py: +2 assertions (help.md has action=help;
      help action carries no pipeline/agent)

Day 2 ✅ Plugin manifest + skill locality decision
  [x] .github/pipelines/feature-dev/.claude-plugin/plugin.json with
      { name, version, description, commands, agents, skills, hooks,
        mcpServers, pipeline, skillLocality } — purely declarative
  [x] Decision recorded in plugin.json.skillLocality: mode="global".
      Rationale: multiple pipelines reuse the same skills (python,
      testing, code-review); per-pipeline duplication would fragment
      the knowledge base. Revisit when a pipeline-specific skill
      appears or a second repo needs copy-paste install without skills.
  [x] NOT wired: skill_loader multi-dir fallback — deferred until the
      locality decision flips to pipeline-local.
  [x] test_plugin_manifest.py — 10 assertions (JSON parses + every
      referenced path resolves + skillLocality decision is recorded)

Day 3 ✅ Direct-mode pull-on-demand skills
  [x] context_builder.AGENT_SKILL_ALLOWLIST["direct"] = designer ∪ coder.
      Deliberately excludes reviewer's code-review skill (that's an
      evaluator checklist, not generator knowledge)
  [x] New MCP tool harness_list_skills(agent_name) in server.py —
      returns catalog filtered through caller's allowlist
  [x] extension.ts runDirect(): one-shot MCP call to harness_list_skills,
      injects catalog into system prompt, pull-on-demand loop with
      {"action":"pull_skill","skill_id":...} marker (max 3 rounds).
      Simpler than registering vscode.lm tools; the marker form keeps
      direct mode harness-free at the tool layer.
  [x] Pipeline mode untouched — still push-only, firewall intact
  [x] test_skill_access.py: +8 assertions (direct allowlist rejects
      code-review; list_skills filters per caller; planner catalog empty;
      reviewer catalog ⊆ {code-review, testing}; regression on
      harness_read_stage for pipeline agents)

Day 4 ✅ Memory: Tier 2 compaction + cross-session query
  [x] session_distiller.compact_failure_patterns() — fires when
      .github/memory/failure-patterns.md > 5 KB. Keeps union of
      top-10 most-frequent + top-10 most-recent. Auto-called from
      distill_session after every append.
  [x] memory_loader.query_sessions(query, limit) — case-insensitive
      substring match against request + stored review output.
      Returns structured excerpts (never full transcripts).
  [x] harness_query_sessions + harness_compact_memory MCP tools
  [x] test_session_distiller.py: +6 assertions (noop below threshold;
      fires above; preserves most-frequent; idempotent; distill triggers
      compaction; survives churn)
  [x] test_memory_loader.py: +7 assertions (request match; review match;
      empty query; limit; case-insensitive; no match; truncation)

Day 5 ✅ Level-1 probe for feature-dev (infrastructure built)
  [x] Built .github/pipelines/feature-dev-level1-probe/ with
      pipeline.yaml (level: 1, singular generator.agent), composite
      agent file producing plan+design+code in one shot, README
      documenting the 80% threshold and measurement protocol
  [x] Re-uses production reviewer via ../feature-dev/agents/reviewer.agent.md
      so generator-side changes are the only variable
  [x] probe.target_pass_rate: 0.80, sample_size: 5, baseline: feature-dev
  [x] test_level1_probe.py: 9 assertions (level=1, singular generator,
      reviewer reuse, probe metadata, README decision rule, production
      pipeline stays Level 2)
  [ ] STILL DEFERRED: actually running the probe. The infrastructure is
      built; 5 representative /feature-dev requests still need to be
      selected and run through both pipelines. Decision log in the
      probe README is empty until that happens. feature-dev stays
      Level 2 until we have that evidence.

Tests: +45 new across Days 1–5. Total now 379 (was 334).

CONDITIONAL (triggered by Day 5 outcome, once probe is run):
  [ ] Handoff schemas (plan→design, design→code, code→review)
      Only if Level-2 stays. Bumps to Week 4.5 or top of Week 5.
  [ ] Pipeline-as-install-unit (copy-paste install)
      Day 2 decision was "global skills" — portability still partial.
      Revisit if second-repo install becomes a real requirement.
```

### Week 5+ — Orchestrator Pivot (active plan, supersedes prior Week 5 / Week 6)

**Why.** Direct mode + the planned agent mode were one mode too many on top of
pipelines. The proven pattern across modern coding agents (Claude Code, Cursor,
Aider) is one orchestrator + sub-agents on demand. We collapsed to **two
modes**: pipeline (for high-stakes, repeatable workflows) + orchestrator (for
everything else). The intent of the never-shipped agent mode (LLM picks verbs,
harness enforces grammar) is fulfilled more cleanly by the orchestrator
spawning sub-agents.

**Architecture invariants preserved.** Zero LLM calls in harness, evaluator
firewall, skills pushed not pulled, fail-closed policy, append-only state —
all hold under the pivot. The pivot improves alignment with harness
best-practices on 6 of 30 rows (BP 2, 13, 15, 19, 28, 30); risks 2 (BP 7, 8)
addressable via the orchestrator's system prompt + redefining
"session = one user turn."

**Two modes after the pivot:**

| Entry | Mode | Behavior |
|---|---|---|
| `/<pipeline-name> <task>` | Pipeline | Fixed sequence, full guardrails, evaluator firewall (unchanged) |
| Anything else | Orchestrator | One main agent, persistent conversation, spawns sub-agents on demand |

**What gets deleted (delivered in Phase D):**
- Direct mode — `runDirect`, `AGENT_SKILL_ALLOWLIST["direct"]`, the bare-prompt path, and its 4 tests
- The `--pipeline` flag — `stripPipelineFlag` + `pipelineForced` variant in `parseCommand`
- Never-shipped agent-mode plumbing (`runAgentChain`, `research.agent.md`, `commands/agent.md`) — never built, dropped from the plan

**Locked decisions** (settled before code; recorded for future reference):

1. **Conversation continuity = replay-on-each-turn.** Extension persists
   transcript per chat_id; replays full history on each user message.
2. **Token budget = Claude Code's reactive pattern.** No fixed number —
   compact at 80% of model context (drop oldest sub-agent transcripts),
   summarize at 90% (oldest user/assistant turns into one rolling summary),
   hard truncate at 99%.
3. **One orchestrator** (`orchestrator.agent.md`). Domain knowledge comes
   from skills, not domain-variant orchestrator files.
4. **Orchestrator does NOT auto-invoke pipelines.** Pipelines remain
   user-invoked. Orchestrator may spawn individual agents (planner, coder,
   reviewer, etc.) but never a whole pipeline.
5. **Memory:** orchestrator gets Tier-1 always + Tier-2 on demand (existing
   tools). Sub-agents get nothing (sub-agent firewall — Phase A invariant).
6. **Spawn budget:** main agent (orchestrator or pipeline stage) capped at
   3 spawns of any one role per user turn. Hard cap, harness-enforced,
   fail-closed.
7. **Tool-call protocol:** real `vscode.lm` tool calls (LM tool
   registration), not JSON markers. The LLM is trained for tool calls;
   VS Code chat renders them natively.
8. **Distillation triggers (4 total, de-duped at append):**
   (a) per-turn gated on noteworthy events; (b) on `/clear` or chat closed;
   (c) on reviewer sub-agent fail; (d) on detected user frustration via
   deterministic regex on negative-sentiment patterns.

Detailed memory contract: see `docs/memory.md`.

---

#### Phase A — Sub-agent primitives (3 days, foundation for orchestrator)

```
Day A.1 ✅ MCP plumbing + policy + storage + timeouts (commits 0606ed0, fcd9c9c)
  [x] storage/db.py: sub_sessions table + CRUD helpers (0606ed0)
  [x] copilot-harness/session/sub_sessions.py: lifecycle, handle_id
      (uuid hex[:12]), status transitions
      (running → done/failed/escalated/abandoned), orphan cleanup
      (parent end + harness startup sweep)
  [x] scripts/policy_engine.py: SUBAGENT_POLICIES (per-role allow-list)
      + MAIN_SUBAGENT_ALLOWLIST (per-main allow-list of roles) + helpers
      (check_subagent_allowed, list_subagent_roles, get_subagent_tools,
      effective_subagent_tools, subagent_deny_reason)
  [x] server.py: register harness_spawn_subagent / harness_complete_subagent
      / harness_await_subagent / harness_list_subagents MCP tools.
      harness_complete_subagent records the runner's terminal result;
      harness_await_subagent polls until terminal or wall-clock kill.
      Startup orphan sweep wired at module-import time.
  [x] Four-layer timeout parameters wired through spawn:
      max_turns (caller arg), per_turn_timeout_s (default 60),
      wall_clock_timeout_s (default 300), await max_wait_s (default 300).
      Auto-escalation in complete() when turns >= max_turns or elapsed
      > wall_clock_timeout_s, with reason appended to the summary.
  [x] Tests: tests/test_sub_sessions.py + tests/test_subagent_policy.py
      (+71 tests; covers handle uniqueness, status transitions,
      cascade-on-parent-end, policy intersection, list_subagents
      filters by caller, unknown role rejected, max_turns / wall_clock
      kills produce escalated=true with structured timeout summary,
      MCP-tool integration of spawn → complete → await flow)

Day A.2 ✅ Firewall + result verification
  [x] copilot-harness/validation/subagent_context.py:
      build_subagent_context(brief, role) — returns the frozen
      SubagentContext(brief, role, role_skill, allowed_tools). Function
      signature deliberately excludes session_id / db_path so the
      firewall is enforceable at the type level. SUBAGENT_ROLE_SKILLS
      table maps each role → SKILL.md id (Phase A.3 lands the files).
      assert_no_session_leakage helper rejects forbidden keys defensively.
  [x] verifier.py: verify_subagent_summary(summary, structured,
      max_tokens=2000, schema=None). Truncates over-cap text with
      `[truncated by harness — exceeded max_tokens cap]`. Reuses the
      secrets + injection scanners as hard-fails. Optional schema check
      (required / types / enum) accepts string type names so JSON-encoded
      schemas from the extension work without a jsonschema dep.
  [x] server.py: harness_complete_subagent now passes the runner's
      summary + structured through verify_subagent_summary; failures
      coerce the row to status='failed' with a structured error. New
      harness_get_subagent_context MCP tool returns the firewalled
      payload to the runner (consumed in Phase A.3).
  [x] Tests: tests/test_subagent_context.py (signature firewall,
      closed key set, leakage detection) + tests/test_subagent_summary_verify.py
      (token cap, marker text, secrets / injection rejection, schema
      type-name coercion, MCP integration through harness_complete_subagent
      and harness_get_subagent_context). +46 tests; total 487.

Day A.3 ◐ Role files + spawn-event surface (Python side ✅; TS helpers ✅; server push deferred)
  [x] .github/agents/explorer.agent.md       (Read + View + Grep + Glob)
  [x] .github/agents/investigator.agent.md   (+ Bash for read-only diagnostics)
  [x] .github/agents/reviewer-aux.agent.md   (Read + View, per-file checklist)
  [x] .github/skills/{explorer,investigator,reviewer-aux}/SKILL.md —
      role procedures pushed by the harness through
      validation/subagent_context.SUBAGENT_ROLE_SKILLS.
  [x] copilot-harness/storage/subagent_audit.py — durable audit table
      `subagent_audit` in audit.db with record_spawn, record_complete,
      query_events. Indexed on ts / parent_session_id / handle_id.
  [x] server.py: harness_spawn_subagent + harness_complete_subagent now
      write a row to subagent_audit on every spawn / completion. New
      harness_query_subagent_events MCP tool exposes the log so the
      extension's chat-marker layer can poll it.
  [x] copilot-harness-extension/src/mcpClient.ts: notification
      EventEmitter with onNotification subscription + emitNotification
      fan-out so the polling layer and the future server push share
      one subscription point. Test seam: McpClient._forTest +
      _handleLine for stream-free unit tests.
  [ ] server.py: emit subagent_spawned / subagent_done MCP notifications
      (FastMCP-side push; deferred — the extension polls
      harness_query_subagent_events via SubagentEventTracker until
      the push lands).
  [x] copilot-harness-extension/src/subagentRendering.ts: pure
      formatters (formatSpawnMarker / formatCompleteMarker / formatMarker)
      with brief truncation + tool histogram + escalation/truncation/
      verification-error annotations, plus SubagentEventTracker that
      polls harness_query_subagent_events with cursor-advancing
      since_ts. No vscode imports — fully unit-testable.
  [x] TS test setup: tsx + node --test runner; npm test green
      (24 assertions covering notification fan-out, unsubscribe,
      malformed input, and tracker cursor / formatting / limit
      forwarding). tsconfig excludes *.test.ts from dist.
  [x] Tests: every spawn writes an audit row, every completion writes
      its mirror row, escalation / verification-failure / truncation are
      all captured, and the no-silent-sub-agents invariant is checked
      end-to-end across explorer + investigator + reviewer-aux roles.
      +20 tests; total 507.

End-of-A checkpoint:
  [x] 507 tests green after A.1 + A.2 + A.3 Python side
      (was 370 — A.1 +71, A.2 +46, A.3 +20).
  [x] Spawn → simulated complete → fetch summary path works in unit tests
      (test_sub_sessions.test_mcp_spawn_then_complete_then_await_returns_summary)
  [x] Sub-agent firewall enforced at type level + tested against every
      forbidden main-session key
  [x] Over-cap summary truncated with marker;
      malformed structured payload rejected against output_schema
  [x] No silent sub agents — durable audit row per spawn + completion
      with ts / handle / parent / role / brief / event / final_status /
      escalated / verification_errors. Extension polls
      harness_query_subagent_events to render chat markers.
  [x] No regressions in existing pipeline mode + memory + skill paths
  [x] Extension-side helpers (mcpClient EventEmitter +
      subagentRendering.ts chat markers + SubagentEventTracker
      poller) shipped. Wiring into a runner lands with the
      orchestrator (Phase B). Server-side push notifications stay
      deferred; the tracker polls harness_query_subagent_events
      until they land.
```

#### Phase B — Orchestrator core (2 days)

```
Day B.1 — Agent file + harness wiring
  [ ] .github/agents/orchestrator.agent.md (frontmatter: name, version,
      role, sees [user_message, conversation_history, memory_tier1],
      inject_skills [orchestrator-routing], output_schema, spawn_allowlist,
      max_spawns_per_role_per_turn)
  [ ] .github/skills/orchestrator-routing/SKILL.md (system-prompt content)
  [ ] copilot-harness/validation/context_builder.py: _context_orchestrator
  [ ] scripts/policy_engine.py: MAIN_SUBAGENT_ALLOWLIST["orchestrator"]
  [ ] tests/test_orchestrator_context.py

Day B.2 ✅ Extension-side runner
  [x] copilot-harness-extension/src/runners/orchestratorCore.ts +
      runners/orchestrator.ts. Core file holds pure helpers (system-
      prompt builder, MCP dispatch, SpawnTracker, cleanup,
      loadOrchestratorPrompts) so node:test can exercise them without
      the vscode runtime. The thin shell composes them into runOrchestrator
      with vscode.lm.sendRequest + replayed chat history + tool-call loop
      (max 8 cycles) + finally-cleanup of any handles spawned-but-never-
      awaited (best-effort harness_complete_subagent with status='abandoned').
  [x] Registered harness_spawn_subagent + harness_await_subagent +
      harness_list_subagents via vscode.lm.registerTool — real LM tool
      calls, not JSON markers. Manifest entries added to package.json
      `contributes.languageModelTools` (canBeReferencedInPrompt=false so
      they don't pollute the user's # autocomplete).
  [x] extension.ts wires registerOrchestratorTools into activate() and
      delegates the new `/orchestrate` slash command (action: orchestrator
      in slashCommands.ts) to runOrchestrator. Pipeline + direct paths
      unchanged — Phase D will pivot routing so non-pipeline turns auto-
      route here.
  [x] Tests: tests/test_slash_commands.py VALID_ACTIONS picks up the new
      "orchestrator" action; +28 TS assertions in
      runners/orchestrator.test.ts (frontmatter strip, prompt assembly,
      tool catalog shape, dispatch arg translation, SpawnTracker bookkeeping,
      cleanup best-effort, disk loader root precedence). 53 TS tests +
      544 Python tests green.

B.2 follow-up ✅ Frontmatter-driven model selection
  [x] All five vscode.lm.selectChatModels call sites previously hardcoded
      `family: "gpt-4o"` regardless of what each agent's frontmatter
      declared. Replaced with copilot-harness-extension/src/modelSelector.ts
      + modelSelectorCore.ts. Resolution chain: skill model: → agent
      model: → fallback (claude-sonnet-4.5) → any vendor=copilot model.
  [x] Skill-level override: any active SKILL.md whose frontmatter
      declares model: lifts the agent onto that family for the
      invocation (first skill wins by load order). Pipeline.ts reads
      active skills from context.injected_skills; orchestrator runner
      passes its inject_skills list. No skill currently declares one —
      the hook is plumbing, ready for procedures that genuinely demand
      heavier capacity.
  [x] Default switched from gpt-4o to claude-sonnet-4.5 across all 10
      agent files (planner, designer, coder, reviewer, orchestrator,
      pipeline-builder, skill-builder, explorer, investigator,
      reviewer-aux). Convention recorded in CLAUDE.md § Conventions
      ("agent = wage; skill = bonus").
  [x] Tests: +23 TS assertions in modelSelectorCore.test.ts covering
      frontmatter parsing edge cases, agent + skill disk readers, root
      precedence, multi-skill first-wins, and a regression check that
      every shipped .agent.md declares a model. 76 TS + 544 Python
      tests green.
```

#### Phase C — Conversation continuity (1.5 days)

```
Day C.1 ✅ Storage + replay
  [x] copilot-harness/session/conversations.py: append_message, get_history
      (token-budgeted, newest-first truncation; estimator mirrors
      verifier._CHARS_PER_TOKEN so budgets agree across the codebase).
      Roles validated fail-closed against VALID_ROLES =
      {user, assistant, tool, system}; chat_id opaque to the harness.
  [x] storage/db.py: conversation_messages table (id PK, chat_id, role,
      content, ts) + idx_conv_chat_ts index. Schema mirrored in both
      schema.sql and the embedded _SCHEMA_SQL constant. CRUD helpers
      insert_conversation_message + get_conversation_messages stay in
      db.py to match the sub_sessions split.
  [x] server.py: harness_append_message + harness_get_conversation MCP
      tools. Both return JSON {status, ...}; bad input fails closed
      with status='error'.
  [x] tests/test_conversations.py: 19 assertions covering id/ts/tokens
      shape, role + content + chat_id validation, round-trip
      chronological order, multi-chat isolation, newest-first
      truncation, single-oversized-message survival, role_filter,
      same-ts deterministic ordering by id, unicode round-trip
      (UTF-8 invariant), schema migration on a fresh DB, and MCP
      integration through server.harness_append_message /
      harness_get_conversation. Total: 563 (was 544).

Day C.2 ✅ Wire into orchestrator + reactive compaction
  [x] orchestrator.ts replays via harness_get_conversation: append user
      message before send, fetch token-budgeted history, append the
      assistant text in finally (so partial replies survive cancel),
      append each tool result as role:"tool". Best-effort everywhere
      — a harness write failure logs and the turn proceeds.
  [x] Sub-agent spawn results appended as role:"tool" entries by the
      orchestrator runner. JSON envelope `{tool, result}` so future
      compaction passes can identify and drop them.
  [x] Reactive compaction at 80% / 90% / 99%:
       80% — drop role:"tool" rows from the per-turn render (storage
             stays canonical; nothing is deleted).
       90% — spawn the new summarizer sub-agent over the oldest half;
             persist the verified summary as role:"system" so
             subsequent turns reuse it; on failure fall through to
             hard-truncate.
       99% — hard-truncate to 50% of model context window. Pure-fn
             planCompaction + applyCompaction in orchestratorCore so
             threshold logic is unit-tested in isolation.
  [x] Summarizer sub-agent (.github/agents/summarizer.agent.md +
      .github/skills/summarizer/SKILL.md) — text-only, single-turn,
      tools=[]. SUBAGENT_POLICIES["summarizer"]=[];
      MAIN_SUBAGENT_ALLOWLIST["orchestrator"] += summarizer;
      SUBAGENT_ROLE_SKILLS["summarizer"]="summarizer".
  [x] Extension-side sub-agent runner — runners/summarizerRunner.ts is
      the FIRST end-to-end LM session that turns a Phase-A `running`
      sub_sessions row into a terminal one. Spawn → fetch firewalled
      context → selectModelForAgent → one-shot vscode.lm.sendRequest
      → harness_complete_subagent. Phase D promotes this shape into a
      generic runSubagent(role, brief).
  [x] Sub-session cleanup at user-turn-end already correct in B.2
      (cleanupOutstandingSubagents calls harness_complete_subagent
      with status='abandoned'). Roadmap line "mark done, delete rows"
      was misleading — `done` is wrong for never-awaited handles, and
      deleting destroys audit rows. Resolution: keep the abandon
      transition; new harness_delete_subsessions_for_parent MCP tool
      (status-gated, age-gated, audit-safe) is exposed for a future
      low-priority background pruner timer; not wired in C.2.
  [x] Distillation triggers — two of four wired:
       (c) reviewer-fail: when a reviewer / reviewer-aux sub-agent
           returns final_status='failed', the runner records a
           failure pattern via harness_append_failure_pattern.
           SpawnTracker.roleFor lets the await callback recover the
           spawned role for the dispatch.
       (d) user frustration regex: 8 patterns in
           .github/memory/sentiment-patterns.json (mirror in
           orchestratorCore.detectFrustration regex bank). On match,
           the runner records a `frustration:<label>` pattern.
       Per-turn dedup via TriggerDedup; persistent dedup via the
       existing _load_existing_patterns index in session_distiller.
       (a) per-turn noteworthy events and (b) /clear / chat-closed
       deferred — overlaps with reviewer-fail in this iteration; (b)
       requires a VS Code chat lifecycle API that does not exist in
       1.93.
  [x] chat_id stability heuristic: sha256(participant + first user
      prompt + workspace path) truncated to 16 hex. Stable across
      turns within a chat panel because chatContext.history[0] never
      reorders. Documented as best-effort; swap when VS Code ships
      a real chat-thread id (likely 1.95+).

Tests: +27 Python (5 in test_orchestrator_context.py for summarizer
spawn allow-list + agent + skill files; 22 in test_distillation_triggers.py
for detect_frustration each pattern + neutral text + missing file +
hot-reload, append_pattern dedup + format + reject empty, MCP
append_failure_pattern round-trip + dedup + reject empty, MCP
delete_subsessions prunes only terminal + age-gates) + 36 TS
(29 in orchestratorCompaction.test.ts: estimateTokens, parse +
resolveChatId stability + sensitivity, totalHistoryTokens,
planCompaction at each threshold + <2-msg fallthrough,
applyCompaction strategies, detectFrustration each pattern +
neutral + empty, TriggerDedup, SpawnTracker.roleFor; 7 in
summarizerRunner.test.ts: serialize emits [role] blocks + drops
empty + empty input; build prompt strips frontmatter + appends
skill + omits empty skill + null skill).
Total: 590 Py + 112 TS (was 568 Py + 76 TS at C.1 close).
```

#### Phase D ✅ Routing pivot + deletions

```
[x] copilot-harness-extension/src/extension.ts::parseCommand: new rule —
    `/` → slash command; legacy bare keyword → its existing handler;
    everything else → orchestrator. Dropped --pipeline flag,
    pipelineForced variant, stripPipelineFlag, direct variant.
[x] Deleted runDirect path entirely — runDirect, parseSkillPullRequest,
    fetchDirectCatalog, fetchMemoryContext, extractChatHistory (the
    orchestrator runner has its own copy), DIRECT_AGENT_NAME,
    MAX_PULL_ROUNDS, MAX_HISTORY_TURNS, SkillCatalog/MemoryContext
    interfaces, the now-unused selectModelForAgent import. ~290
    deletions in extension.ts.
[x] Dropped AGENT_SKILL_ALLOWLIST["direct"] from
    validation/context_builder.py. Generator skills reach the
    orchestrator only through spawned sub-agents whose allowlists
    already cover them — see MAIN_SUBAGENT_ALLOWLIST.
[x] Deleted 4 direct-mode tests in test_skill_access.py
    (allowlist_includes_generator_skills, excludes_evaluator_skill,
    rejects_disallowed_skill_via_server, authorized_skill_passes_allowlist).
    Migrated test_harness_list_skills_filters_to_caller_allowlist
    to exercise "coder" instead of "direct".
[x] Stripped planned Agent Mode references from roadmap (this section
    + supersedes block) and README files.
[x] Rewrote .github/commands/help.md for 2-mode reality (pipeline +
    orchestrator).
[x] Updated CLAUDE.md Hard Invariants #2 + #4 and the Decision Rules
    table (Direct row → Orchestrator row); test counts bumped to
    586 Py + 112 TS.
```

**Note on roadmap projection:** the original "−34 tests" estimate was
speculative. Direct-mode test coverage was concentrated in 4 tests
in test_skill_access.py; the rest of the deletion was TS code with
no test surface. Actual: 590 → 586 Py.

#### Phase E ✅ Documentation

```
[x] CLAUDE.md Hard Invariant #2: pull-on-demand clause removed; skills
    are pushed via inject_skills frontmatter for both pipeline agents
    and the orchestrator.
[x] CLAUDE.md Hard Invariant #4: zero-cost-routing rule rewritten to
    /<pipeline-name> → pipeline; everything else → orchestrator.
[x] CLAUDE.md Decision Rules table: "Direct" row replaced with
    "Orchestrator".
[x] docs/design.md § Current State: rewritten for v0.5 (Phases A–E,
    orchestrator + pipeline; sub-agent primitives, conversation
    replay, reactive compaction, summarizer, distillation triggers).
[x] docs/design.md § Best Practices Compliance: BP 2, 13, 15, 19,
    28, 30 lifted to ✅ with pivot-aware notes; gap list reduced to
    BP 3, 25, 27, 29.
[x] docs/memory.md: trigger table now flags reviewer-fail + frustration
    as shipped (Phase C.2) and per-turn / chat-closed as deferred;
    conversation transcript section retargeted from JSONL files to
    `conversation_messages` (Phase C.1) with the chat_id heuristic;
    file map adds harness_append_failure_pattern + append_message +
    get_conversation.
[x] AGENTS.md: orchestrator is named the default-entry agent; mode
    table collapsed from three (Direct/Pipeline/Agent) to two
    (Orchestrator/Pipeline); session protocol covers replay + reactive
    compaction; duplicated invariants pruned (CLAUDE.md is the source).
```

#### Post-E ✅ Bringup hardening + token-cost guards (PR #37)

```
Real-world bringup uncovered a stack of issues that all surfaced
together once the user tried to install the extension and run a turn.
Shipped on fix/mcp-init-timeout, merged at bce6d2e.

Build + install pipeline:
  [x] McpClient.initialize is time-bound (15 s). A hung server fails
      activation with a clean error toast instead of freezing the
      chat input. stderr piped into the CopilotHarness output channel
      as [server] <line>; was inheriting to invisible main stderr.
  [x] Git Bash on Windows is the supported shell; WSL refused with a
      clear redirect. scripts/run-bash.js Node wrapper hardcodes Git
      Bash candidate paths so npm scripts work regardless of PATH order.
  [x] npm run all is one-shot: setup → package → install:vsix.
      install-vsix.sh detects EPERM (VS Code is running) and surfaces
      the actionable banner. setup + build:server idempotent — no-op
      re-run is ~5 s instead of ~60 s.
  [x] PyInstaller spec rewritten with collect_submodules so
      session.sub_sessions / session.conversations /
      validation.subagent_context / storage.subagent_audit /
      policy_engine all bundle automatically. Fixes the runtime
      ModuleNotFoundError: No module named 'policy_engine' crash.

Orchestrator runtime:
  [x] Catalog includes Copilot read tools — orchestrator can answer
      "describe this project" without spawning a sub-agent.
  [x] harness_await_subagent capped at 30 s (was 5 min wait for
      runners that don't exist yet).
  [x] harness_append_message accepts Any and json.dumps non-string
      content; tool-result rows use plaintext '[tool name]\nresult'
      format instead of JSON envelope (Pydantic was deserializing
      JSON-shaped strings back into dicts).
  [x] Per-cycle + per-tool timing logs:
        [orchestrator] cycle 0: lm=2800ms text=42ch tool_calls=1
        [orchestrator]   tool copilot_searchWorkspace: ok 412ms
        [orchestrator] turn done — total=7800ms cycles=3
      MCP SDK heartbeat ('Processing request of type CallToolRequest')
      filtered out of the server stderr stream.

Token-cost reductions:
  [x] DEFAULT_MAX_TOKENS lowered 100_000 → 50_000 (replay budget).
  [x] Tool-result rows capped at 4 000 chars at storage time.
  [x] Sub-agent tools hidden from the catalog while
      LM_FACING_SUBAGENT_ROLES is empty (all roles today). Prevents
      spawn → 30 s wall-clock → retry token-burning loop.
  [x] orchestrator.agent.md + orchestrator-routing/SKILL.md trimmed
      ~51 % on the static system prompt (~2,525 t → ~1,225 t).
  [x] Declarative tool catalog: every .agent.md declares lm_tools:
      in frontmatter. parseFrontmatterLmTools reads it; runner uses
      it as the per-turn allowlist. EXTERNAL_TOOL_ALLOWLIST constant
      deleted. Editing tool surface is now a YAML edit + rebuild.

UX:
  [x] harness_clear_active_session MCP tool + sidebar inline action
      (right-click "Active session" → Clear). Idempotent.
  [x] Destructive-intent protocol in the routing skill: orchestrator
      must (1) name the risk, (2) suggest a path that can do it,
      (3) confirm on ambiguous intent. No more silent "I don't have
      that tool" replies.
  [x] Killed the preemptive /feature-dev pitch. Pipelines are
      strictly process/workflow only — the LM is forbidden from
      listing them as a "thing you could do" in capability summaries.

Tests: 591 Py + 125 TS green; tsc clean.
```

**Test count trajectory:**
```
Pre-A:        379 Py
After A:      507 Py
After B:      544 Py + 76 TS
After C:      590 Py + 112 TS = 702
After D:      586 Py + 112 TS = 698  (−4; original −34 estimate was speculative)
After E:    ~ 698  (docs only, no test impact)
After PR #37: 591 Py + 125 TS = 716  (+18; clear-active-session,
              dict-coercion, parseFrontmatterLmTools, mcp timeout,
              and per-agent lm_tools assertion)
```

**Risk areas:**
- `vscode.lm.registerTool` API surface — verify in Phase B.1; fall back to
  JSON markers if API is unstable. Recommended target: real LM tools.
- Long-running orchestrator + sub-session cleanup race conditions —
  mitigation: serialize per-chat_id; reject new user messages until prior
  turn settles.
- Reactive compaction hitting 99% mid-stream — defensive hard truncate
  keeps the request valid.

**What this section supersedes:**
- Prior Week 5 (sub agents standalone) — folded into Phase A as the
  foundation.
- Prior Week 6 Agent Mode (`/agent` slash + chain runtime) — replaced by
  the orchestrator. The files that the prior plan would have built
  (`runners/agentChain.ts`, `commands/agent.md`, `agents/research.agent.md`)
  were never built and have been removed from the plan.

---
