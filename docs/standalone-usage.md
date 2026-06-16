# Standalone Usage — driving the harness without Copilot

> Companion to [`docs/design.md`](./design.md) (architecture + MCP tool
> reference) and [`docs/harness-direction.md`](./harness-direction.md)
> (substrate vs ephemeral discipline).

CopilotHarness is split into two parts. This doc covers using the
**core harness only** — no VS Code, no Copilot, no `vscode.lm.sendRequest` —
against any LLM that supports tool use.

| Component | What it is | LLM-coupled? |
|---|---|---|
| `copilot-harness/` (Python) | MCP stdio server exposing the governance substrate: audit DB, skill catalog, three-tier memory, policy engine, project-profile detection, deterministic verifiers, SessionStart/PreToolUse/PostToolUse hooks. **Zero LLM calls** (Hard Invariant #1). | No — works with any MCP-capable client. |
| `copilot-harness-extension/` (TypeScript) | VS Code extension. The only place `vscode.lm.sendRequest` lives. Contains `runOrchestrator`, `runPipeline`, `runAgentLM`, the cycle loop, slash commands, sidebar. | Yes — Copilot Chat is the LLM. |

Drop the extension, keep the harness, and you have an LLM-agnostic
governance backend. This doc shows two ways to drive it.

---

## 1. Architecture recap

Hard Invariant #1 says: *zero LLM calls inside the harness; only
`vscode.lm.sendRequest` from the TS extension reaches a model.* The
flip side is that the Python harness is **already LLM-agnostic by
construction**. It speaks MCP stdio. Any MCP-capable client — Claude
Code, Cursor, Continue, your own script — can drive it.

```
┌────────────────────────────┐           ┌─────────────────────────┐
│   LLM client / agent       │  MCP      │  copilot-harness/       │
│   - your Python driver     │ ─stdio─▶  │  MCP stdio server       │
│   - Claude Code            │           │  exposes harness_* tools│
│   - Cursor                 │           │                         │
│   - Continue               │           │  → audit.db (SQLite)    │
│   - any MCP-capable agent  │           │  → .github/skills/      │
└────────────────────────────┘           │  → .github/memory/      │
                                          │  → .github/agents/      │
                                          │  → policy_engine.py    │
                                          │  → verifier / executor │
                                          └─────────────────────────┘
```

The tool catalog (`harness_*`) is documented in
[`docs/design.md`](./design.md). It's the same catalog every client
sees.

---

## 2. Install

```bash
cd copilot-harness
pip install -e .
```

This makes `copilot-harness serve` available as a console script. The
server is also runnable directly: `python copilot-harness/server.py`.

---

## 3. Two ways to drive it

### 3.1 — Register with an MCP-capable agent IDE

Anything that speaks MCP can use the harness without code.

**Claude Code** (`~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "harness": {
      "command": "python",
      "args": ["/absolute/path/to/copilot-harness/server.py"]
    }
  }
}
```

**Cursor / Continue** — similar shape; see each vendor's MCP config docs.

Once registered, the agent sees `harness_new_session`,
`harness_read_stage`, `harness_get_skill`, `harness_record_*` etc. in
its tool list and can use them like any other tool.

### 3.2 — Custom driver

Use [`examples/standalone_driver.py`](../examples/standalone_driver.py)
as a starting point. It:

1. Spawns `copilot-harness/server.py` as an MCP stdio subprocess.
2. Lists the tool catalog.
3. Calls `harness_new_session` + `harness_get_memory_entry` as a smoke test.
4. Sketches the agentic loop: LLM call → dispatch tool calls → feed
   results back → repeat until the LLM stops asking for tools.

```bash
# Smoke test the substrate.
python examples/standalone_driver.py --smoke

# Run one agentic turn (requires filling in `call_llm()` first).
python examples/standalone_driver.py --turn "list the project profile"
```

The `call_llm()` function in the reference driver is a vendor-agnostic
stub. Fill it in with your provider:

**Anthropic (Claude Messages API):**

```python
import anthropic
client = anthropic.Anthropic()
msg = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=2048,
    tools=tools,           # already in Anthropic shape via _mcp_to_anthropic_tool
    messages=messages,
)
return {
    "stop_reason": msg.stop_reason,
    "content": [block.model_dump() for block in msg.content],
}
```

**OpenAI (Chat Completions with tools):** the tool-spec wrapper differs
(`{"type": "function", "function": {...}}`), but the loop shape is
identical. Adjust `_mcp_to_anthropic_tool()` and the response parsing
accordingly.

---

## 4. What you get out of the box

Per the tool catalog ([`docs/design.md`](./design.md)):

- **Sessions** — `harness_new_session`, `harness_get_status`
- **Pipeline I/O** — `harness_read_stage` (firewall + skill injection), `harness_write_stage`
- **Skills** — `harness_get_skill`, `harness_list_skills`, `harness_get_reference`
- **Memory** — `harness_get_memory_entry` (the 3-tier memory)
- **Verifiers** — `harness_run_lint`, `harness_run_typecheck`, `harness_run_tests`
- **Audit** — `harness_record_stage_metric`, `harness_record_agent_cycle`,
  `harness_query_stage_metrics`, `harness_query_subagent_events`,
  `harness_query_agent_cycles`
- **Conversation** — `harness_append_message`, `harness_get_conversation`
- **Memory distillation** — `harness_append_failure_pattern`
- **Sub-agents** — `harness_spawn_subagent`, `harness_complete_subagent`,
  `harness_await_subagent`, `harness_list_subagents`
- **Project profile** — auto-populated by SessionStart and readable via
  `harness_get_memory_entry("project-profile")` (MVP item 4 / Track D.1).

Everything in `.github/skills/`, `.github/memory/`, `.github/agents/`,
and the `scripts/*.py` hooks works identically regardless of which LLM
sits in front. The skill router (MVP item 6) and skill `applies-to`
matching (MVP item 5), once shipped, will also be vendor-agnostic.

---

## 5. Known limits when running standalone (today)

The harness was designed substrate-first, but some ephemeral
compensating structure currently only exists on the TypeScript side:

| Limit | Where it lives today | When it goes away |
|---|---|---|
| **Cycle-loop guards** — path-rules preamble, intermediate-text salvage, empty-cycle bail-out, empty-project fallback | `copilot-harness-extension/src/runners/runAgentLM` (TS) | Either ports to Python, or dissolves as models improve (`harness-tier: ephemeral` per HI #9) |
| **BudgetEnforcer + credit accounting** | `pipelineBudgetCore.ts` (TS) | A Python equivalent would be ~150 lines; not yet on the roadmap because Track D.4 already shipped a TS version |
| **`runStageReviewGate` 4-button UX** | VS Code only — `vscode.window.showInformationMessage` | UI is inherently vendor-coupled; standalone equivalent would be CLI prompts |
| **Pipeline orchestration** — `runPipeline`, `runChunkedCodeAndReview`, `runAgentWithValidationRetry` | TS extension | Marked `harness-tier: ephemeral` — expected to dissolve; the substrate continues to work without them |

In practice this means: you can drive the harness against another LLM
**today** for any work that goes through the orchestrator/butler
surface (single agentic turn with tool-use). Full pipeline runs
(`/feature-dev` style 4-stage with retries) currently require the TS
extension. If you need pipeline-shaped runs from Python, a small
Python orchestrator (~300 lines, no LLM calls) could be added —
out of scope here.

---

## 6. Roadmap to fully standalone

This doc is the **first** of the standalone-extraction steps. See
[`docs/roadmap.md`](./roadmap.md) for the full sequence:

1. **This PR** — reference driver + this guide. Enables driving the
   existing core against any LLM today.
2. Rename the Python package `copilot-harness` → `agent-harness`.
3. Move root-level `scripts/`, `.github/skills`, `.github/memory`,
   `.github/agents`, `.github/pipelines` into the renamed package so
   the subtree is self-contained.
4. `git filter-repo` extract → fresh repo `Eurus7895/agent-harness` →
   PyPI publish.
5. VS Code extension switches from local subprocess to the published
   `agent-harness` PyPI package.

After step 5, this repo holds only the Copilot adapter; `agent-harness`
is the standalone substrate anyone can pip-install and drive against
any LLM with tool-use support.
