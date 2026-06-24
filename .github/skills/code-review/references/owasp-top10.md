# OWASP Top 10 for Agentic AI — Code Review Reference

Source: OWASP Top 10 for Agentic Applications 2026 (https://genai.owasp.org)

Use this reference when reviewing code that handles external input, authentication,
subprocess calls, file I/O, SQL, or inter-agent communication.

---

## AA01: Prompt Injection

**Risk:** Malicious input in user data, tool outputs, or retrieved content causes
an agent to deviate from its intended behavior.

**Check for:**
- Agent output stored in state without injection scan
- External data passed directly to context builders without sanitization
- Tool results reflected back to agents without filtering

**In harness code:** `context_builder.py` must scan every string before injection
into agent context. Look for calls to `scan_injection()` wrapping external data.

---

## AA02: Excessive Agency

**Risk:** Agent takes actions beyond its declared scope, affecting systems it should
not have access to.

**Check for:**
- Agents writing files outside `files_affected`
- Agents calling tools not in their `tools` field
- `shell=True` in subprocess — enables command injection

**In harness code:** `context_builder.py` validates tool call scope. `executor.py`
must never use `shell=True`.

---

## AA03: Memory Poisoning

**Risk:** Persistent storage is poisoned with adversarial content that influences
future sessions.

**Check for:**
- Pattern detector writing unvalidated content to `cross_session.db`
- `fail_patterns` table populated without sanitization

**In harness code:** All writes to `memory/cross_session.db` must go through
`verifier.scan_injection()` first.

---

## AA04: Insecure Direct Object Reference (IDOR) via Tool Calls

**Risk:** An agent accesses another session's data by manipulating a session ID.

**Check for:**
- `musubi_read_stage` that accepts session_id without verifying the caller
  owns or is authorized for that session
- Session IDs in URL/path parameters without ownership check

---

## AA05: Insecure Output Handling

**Risk:** Agent output is used downstream without validation, enabling injection
into subsequent agents or execution contexts.

**Check for:**
- Reviewer output used to build Coder context without filtering to `fix_instructions` only
- Skill-Builder output applied directly without human review gate

**In harness code:** `context_builder.build_context` for Coder on retry must
return `fix_instructions` only — not the full review output.

---

## AA06: Sensitive Information Disclosure

**Risk:** Secrets, credentials, or PII leak through agent outputs or logs.

**Check for:**
- `verifier.scan_secrets()` called before every `state.write_stage()`
- Log statements that include request bodies, API responses, or config values
- Test fixtures containing real credentials

**Patterns to detect:**
```
sk-[A-Za-z0-9]{32,}          ← OpenAI key
ghp_[A-Za-z0-9]{36}          ← GitHub PAT
[Pp]assword\s*=\s*["'][^"']+  ← hardcoded password
BEGIN (RSA |EC )?PRIVATE KEY  ← private key
```

---

## AA07: Improper Error Handling

**Risk:** Stack traces or internal state leaked in error responses, revealing
implementation details to downstream agents.

**Check for:**
- Bare `except:` blocks that swallow errors silently
- Exception messages returned directly to calling agents
- Missing `timeout` on subprocess calls

---

## AA08: Insecure Plugin/Skill Execution

**Risk:** Skill asset scripts execute arbitrary code or accept unvalidated inputs.

**Check for:**
- `musubi_run_asset` that does not validate `skill_id` and `asset_name`
  against an allowlist before constructing the path
- Path traversal: `assets/../../etc/passwd`
- Asset scripts that accept `shell=True` subprocess calls

**In harness code:** `skill_loader.py` must resolve and validate paths against
the `.github/skills/` base directory.

---

## AA09: Overreliance on LLM

**Risk:** Harness logic delegates decisions to the LLM that should be deterministic.

**Check for:** Any `anthropic`, `openai`, or other LLM SDK import in harness code.
Zero LLM calls inside the harness is a hard requirement.

---

## AA10: Model Theft / Denial of Service

**Risk:** Unbounded execution, infinite loops, or resource exhaustion in agent tasks.

**Check for:**
- Missing `timeout` on subprocess calls in `executor.py`
- Missing `max_attempts` check before triggering Coder retry
- Skill asset scripts without execution time limits
