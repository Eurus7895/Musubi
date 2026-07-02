# Agent Tool Argument Elision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop large file-write payloads from being replayed to the model after successful `musubi_write_file`, `musubi_append_file`, and `musubi_edit_file` calls.

**Architecture:** Keep tool dispatch unchanged: the MCP tool still receives the full file content. Before the next LM call, compact assistant `tool_use.input` fields that contain large file payloads into deterministic metadata stubs, preserving tool ids, names, paths, offsets, and hashes. This is context elision, not ordinary compression, because the full artifact already lives in the workspace file and/or audit trail.

**Tech Stack:** Python 3, existing Musubi agent loop, `musubi/agent/context.py`, pytest.

## Global Constraints

- Do not change `DEFAULT_EFFORT_FLOOR`, `EFFORT_CEILING`, token-budget semantics, or provider pricing logic.
- Do not compress or mutate arguments before dispatch; tools must receive exact original arguments.
- Preserve assistant `tool_use` and user `tool_result` pairing.
- Preserve ordering guarantees for file mutation tools.
- Keep root `agent` tool surface unchanged.
- Keep `.vscode/mcp.json` unstaged.

---

## File Structure

- Modify `musubi/agent/context.py`: add deterministic elision for large file mutation `tool_use.input` payloads and call it from `fit_context`.
- Modify `musubi/tests/test_context.py`: unit tests for elision behavior in the context fitter.
- Modify `musubi/tests/test_agent_loop.py`: integration test proving raw content is dispatched once but not replayed into the next LM call.
- Modify `.github/agents/workers/coder.agent.md`: tell coders not to rely on raw append chunks remaining in context.
- Modify `docs/roadmap.md`: note payload elision as the next chunk-safe file transport follow-up.

---

### Task 1: Add Failing Context-Fitter Tests

**Files:**
- Modify: `musubi/tests/test_context.py`

**Interfaces:**
- Consumes: `fit_context(messages, budget_chars, keep_last_turns, compression_db_path)`
- Produces: tests that require large file mutation arguments to be elided even when total context is under the normal budget.

- [ ] **Step 1: Add tests for large write/append args**

Add these tests near the existing `fit_context` tests:

```python
def test_fit_context_elides_large_file_tool_use_inputs_even_under_budget() -> None:
    raw = "<html>" + ("A" * 2400) + "</html>"
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "create dashboard"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "append-1",
                    "name": "musubi_append_file",
                    "input": {
                        "path": "dashboard.html",
                        "content": raw,
                        "expected_offset": 0,
                    },
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "append-1",
                    "content": '{"status":"ok","bytes_written":2413,"total_bytes":2413}',
                }
            ],
        },
    ]

    out = fit_context(msgs, budget_chars=1_000_000)

    payload = out[2]["content"][0]["input"]
    assert payload["path"] == "dashboard.html"
    assert payload["expected_offset"] == 0
    assert payload["content"].startswith("[musubi:elided-tool-arg")
    assert "chars=" in payload["content"]
    assert "bytes=" in payload["content"]
    assert "sha256=" in payload["content"]
    assert raw not in json.dumps(out)
    assert msgs[2]["content"][0]["input"]["content"] == raw


def test_fit_context_elides_large_edit_file_strings() -> None:
    old = "old-line\n" + ("B" * 1600)
    new = "new-line\n" + ("C" * 1700)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "edit file"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "edit-1",
                    "name": "musubi_edit_file",
                    "input": {
                        "path": "src/app.py",
                        "old_string": old,
                        "new_string": new,
                    },
                }
            ],
        },
    ]

    out = fit_context(msgs, budget_chars=1_000_000)

    payload = out[2]["content"][0]["input"]
    assert payload["old_string"].startswith("[musubi:elided-tool-arg")
    assert payload["new_string"].startswith("[musubi:elided-tool-arg")
    assert old not in json.dumps(out)
    assert new not in json.dumps(out)
```

- [ ] **Step 2: Add tests that small/non-file tool calls stay unchanged**

```python
def test_fit_context_keeps_small_file_tool_use_inputs() -> None:
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "small"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "write-1",
                    "name": "musubi_write_file",
                    "input": {"path": "note.txt", "content": "short"},
                }
            ],
        },
    ]

    assert fit_context(msgs, budget_chars=1_000_000) is msgs


def test_fit_context_keeps_non_file_tool_use_inputs() -> None:
    raw = "X" * 3000
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "run command"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "cmd-1",
                    "name": "musubi_run_command",
                    "input": {"command": raw},
                }
            ],
        },
    ]

    assert fit_context(msgs, budget_chars=1_000_000) is msgs
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_context.py -q
```

Expected: the new elision tests fail because `fit_context` currently only compresses/trims `tool_result` blocks, not assistant `tool_use.input`.

---

### Task 2: Implement Deterministic Tool Argument Elision

**Files:**
- Modify: `musubi/agent/context.py`

**Interfaces:**
- Produces: `_elide_large_file_tool_inputs(messages, min_chars=800) -> list[dict[str, Any]]`
- Produces: `_elided_tool_arg_stub(tool_name: str, field: str, value: str) -> str`
- Consumed by: `fit_context`

- [ ] **Step 1: Add constants and import**

At the top of `musubi/agent/context.py`, add `hashlib` and these constants:

```python
import hashlib
```

```python
FILE_TOOL_ARG_ELISION_MIN_CHARS = 800
_FILE_TOOL_ARG_FIELDS = {
    "musubi_write_file": ("content",),
    "musubi_append_file": ("content",),
    "musubi_edit_file": ("old_string", "new_string"),
}
```

- [ ] **Step 2: Add helper functions**

Add these helpers before `fit_context`:

```python
def _elided_tool_arg_stub(tool_name: str, field: str, value: str) -> str:
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return (
        f"[musubi:elided-tool-arg tool={tool_name} field={field} "
        f"chars={len(value)} bytes={len(encoded)} sha256={digest}; "
        "argument was already sent to the MCP tool]"
    )


def _should_elide_tool_arg(value: Any, min_chars: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) >= min_chars
        and not value.startswith("[musubi:elided-tool-arg")
    )


def _elide_large_file_tool_inputs(
    messages: list[dict[str, Any]],
    *,
    min_chars: int = FILE_TOOL_ARG_ELISION_MIN_CHARS,
) -> list[dict[str, Any]]:
    out = messages
    changed_messages: dict[int, dict[str, Any]] = {}

    def editable_message(index: int) -> dict[str, Any]:
        nonlocal out
        if out is messages:
            out = list(messages)
        msg = changed_messages.get(index)
        if msg is None:
            msg = dict(messages[index])
            msg["content"] = [
                dict(block) if isinstance(block, dict) else block
                for block in messages[index].get("content", [])
            ]
            changed_messages[index] = msg
            out[index] = msg
        return msg

    for msg_index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            fields = _FILE_TOOL_ARG_FIELDS.get(name)
            if not fields:
                continue
            raw_input = block.get("input")
            if not isinstance(raw_input, dict):
                continue
            replacements = {
                field: _elided_tool_arg_stub(name, field, raw_input[field])
                for field in fields
                if _should_elide_tool_arg(raw_input.get(field), min_chars)
            }
            if not replacements:
                continue
            msg = editable_message(msg_index)
            editable_block = dict(msg["content"][block_index])
            editable_input = dict(raw_input)
            editable_input.update(replacements)
            editable_block["input"] = editable_input
            msg["content"][block_index] = editable_block

    return out
```

- [ ] **Step 3: Call elision before the normal budget check**

At the start of `fit_context`, after budget handling and before calculating `total`, add:

```python
    messages = _elide_large_file_tool_inputs(messages)
```

The beginning of `fit_context` should read:

```python
    budget = context_budget() if budget_chars is None else budget_chars
    if budget <= 0:
        return messages
    messages = _elide_large_file_tool_inputs(messages)
    total = _total_chars(messages)
    if total <= budget:
        return messages
```

- [ ] **Step 4: Run context tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_context.py -q
```

Expected: all `test_context.py` tests pass.

---

### Task 3: Prove Dispatch Still Receives Full Content But Replay Is Elided

**Files:**
- Modify: `musubi/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `_run_loop`, `FakeRouter`, `_FakeToolSession`
- Produces: regression coverage for the exact failure mode from `agent "create html dashboard ..."`

- [ ] **Step 1: Add integration test**

Add this test after the dispatch normalization tests:

```python
def test_run_loop_elides_large_file_tool_args_before_next_model_call(
    tmp_path: Path,
) -> None:
    from agent import run as run_mod

    raw = "<html>" + ("A" * 2400) + "</html>"
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[
                {
                    "type": "tool_use",
                    "id": "append-1",
                    "name": "musubi_append_file",
                    "input": {
                        "path": "dashboard.html",
                        "content": raw,
                        "expected_offset": 0,
                    },
                }
            ],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "dashboard written."}],
        ),
    ])
    session = _FakeToolSession('{"status":"ok","bytes_written":2413,"total_bytes":2413}')

    answer, cycles = asyncio.run(
        run_mod._run_loop(
            session,
            router,
            [{"name": "musubi_append_file", "description": "", "input_schema": {}}],
            [{"role": "user", "content": "create html dashboard"}],
            max_cycles=2,
            log=io.StringIO(),
            role="coder",
            audit_db_path=tmp_path / "audit.db",
        )
    )

    assert answer == "dashboard written."
    assert cycles == 2
    assert session.calls == [
        (
            "musubi_append_file",
            {"path": "dashboard.html", "content": raw, "expected_offset": 0},
        )
    ]

    replay = json.dumps(router.calls[1]["messages"])
    assert raw not in replay
    assert "[musubi:elided-tool-arg" in replay
    assert "dashboard.html" in replay
```

- [ ] **Step 2: Run the new test and verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py::test_run_loop_elides_large_file_tool_args_before_next_model_call -q
```

Expected: PASS.

- [ ] **Step 3: Run focused agent-loop tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_loop.py -q
```

Expected: PASS.

---

### Task 4: Update Coder Prompt And Roadmap

**Files:**
- Modify: `.github/agents/workers/coder.agent.md`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Produces: guidance that matches runtime behavior.

- [ ] **Step 1: Update coder guidance**

In `.github/agents/workers/coder.agent.md`, add one short rule near the existing large artifact guidance:

```markdown
- After large `musubi_write_file`, `musubi_append_file`, or `musubi_edit_file`
  calls, assume the raw payload may be elided from your later context. Use
  file reads, size checks, grep, or concise summaries when you need to inspect
  the artifact again.
```

- [ ] **Step 2: Update roadmap note**

In `docs/roadmap.md`, under the existing agent catalog worker modes or chunk-safe file transport note, add:

```markdown
- Follow-up: elide large file mutation tool arguments from agent replay after
  dispatch, preserving path/offset/hash metadata while keeping the workspace
  file as source of truth.
```

- [ ] **Step 3: Run a docs-safe smoke test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_context.py musubi/tests/test_agent_loop.py::test_run_loop_elides_large_file_tool_args_before_next_model_call -q
```

Expected: PASS.

---

### Task 5: Regression Verification, Commit, And Push

**Files:**
- No additional code files.

**Interfaces:**
- Produces: a pushed branch with the token-replay fix.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_context.py musubi/tests/test_agent_loop.py musubi/tests/test_subagent_orchestrator.py -q
```

Expected: PASS. If unrelated Windows shell tests fail in broader suites, record them separately and do not hide them.

- [ ] **Step 2: Run vendor/truncation regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_agent_vendors.py musubi/tests/test_salvage_on_exhaust.py -q
```

Expected: PASS.

- [ ] **Step 3: Inspect git status**

Run:

```powershell
git status -sb
```

Expected: only intended files changed, plus the known local `.vscode/mcp.json` if still present.

- [ ] **Step 4: Stage intended files only**

Run:

```powershell
git add musubi/agent/context.py musubi/tests/test_context.py musubi/tests/test_agent_loop.py .github/agents/workers/coder.agent.md docs/roadmap.md docs/superpowers/plans/2026-07-02-agent-tool-argument-elision.md
```

- [ ] **Step 5: Commit**

Run:

```powershell
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "fix(agent): elide large file tool arguments"
```

- [ ] **Step 6: Push**

Run:

```powershell
git push
```

Expected: current branch updates on the remote.

---

## Self-Review

- Spec coverage: this plan addresses the current token spike by eliding large `tool_use.input` payloads, not by raising output limits or changing budget accounting.
- Placeholder scan: no implementation step depends on an unspecified helper or unnamed file.
- Type consistency: all helper names and test expectations use the same `musubi:elided-tool-arg` marker.
- Known non-goal: this does not reduce the first-time output cost of generating a large file chunk. It reduces the repeated input cost caused by replaying already-dispatched chunks in later cycles.
