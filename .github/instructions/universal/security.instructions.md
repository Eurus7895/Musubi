---
applyTo: "**"
priority: P1
---

# Security Instructions — Universal (P1, never overridden)

These rules apply to every agent, every file, every output. No P2, P3, or P4
instruction can override them.

## Secrets

- **Never** output API keys, tokens, passwords, private keys, or connection strings
  in any format — code, comments, JSON, logs, or prose.
- Environment variables must be read via `os.environ.get("KEY")` with explicit handling
  for missing values. Never provide a secret as a default fallback.
- If code requires a secret to function, document the required environment variable
  name and raise a clear error if it is absent.

```python
# correct
api_key = os.environ.get("API_KEY")
if not api_key:
    raise EnvironmentError("API_KEY environment variable is required")

# wrong
api_key = os.environ.get("API_KEY", "sk-hardcoded-key-here")
```

## Input Validation

- Validate all input at system boundaries: HTTP request bodies, CLI arguments,
  file contents from external sources, MCP tool call parameters.
- Reject or sanitize before processing. Never process raw untrusted input.
- Use allowlists where possible. Denylists miss edge cases.

## Injection Prevention

- SQL: use parameterized queries only. Never format SQL strings with user data.
- Shell: never pass user data to `subprocess` without explicit argument separation
  (`shell=False`, args as list).
- Path traversal: resolve and validate all paths against an allowed base directory.

```python
# correct — SQL parameterized
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

# wrong — SQL injection
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# correct — subprocess
subprocess.run(["ruff", "check", file_path], shell=False)

# wrong — shell injection
subprocess.run(f"ruff check {file_path}", shell=True)
```

## Subprocess and Code Execution

- Never execute arbitrary strings as code (`eval`, `exec`, dynamic `import`).
- Subprocess calls must use `shell=False` and list arguments.
- Set timeouts on all subprocess calls. Default: 30 seconds.
- Capture stdout and stderr. Never let subprocess output reach a user unfiltered.

## Output Safety

- Never include raw user input in responses without sanitization.
- Never reflect back data from session state that was not validated by `verifier.py`.
- Agent output must not contain instructions that could hijack downstream agents.
  (See injection detection in `context_builder.py`.)

## Cryptography

- Never implement custom cryptography.
- Use `secrets` module for token generation, not `random`.
- Use established libraries (e.g., `cryptography`, `bcrypt`) for any hashing or encryption.

## File Access

- Restrict file operations to the project directory.
- Validate that resolved paths stay within the allowed base before opening.
- Never follow symlinks to locations outside the allowed base.

## Logging

- Never log secrets, credentials, or full request bodies from external sources.
- Sanitize before logging. Mark sensitive fields as `[REDACTED]`.
