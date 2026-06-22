# REST Principles — API Design Reference

Use this reference when designing new endpoints or reviewing existing ones for
convention consistency.

---

## Resource Naming

- Use nouns, not verbs: `/sessions`, not `/createSession`
- Use plural: `/sessions/{id}`, not `/session/{id}`
- Hierarchical resources: `/sessions/{id}/stages/{stage}`
- Lowercase, hyphens for multi-word: `/skill-results`, not `/skillResults`

## HTTP Methods

| Method | Idempotent | Safe | Use for |
|--------|-----------|------|---------|
| GET | yes | yes | Read (no side effects) |
| POST | no | no | Create, or trigger action |
| PUT | yes | no | Full replacement |
| PATCH | no | no | Partial update |
| DELETE | yes | no | Remove |

**Never** use GET for operations with side effects.

## Status Codes

| Code | Name | Use when |
|------|------|---------|
| 200 | OK | Successful GET, PUT, PATCH |
| 201 | Created | Successful POST that creates a resource |
| 204 | No Content | Successful DELETE or action with no response body |
| 400 | Bad Request | Client sent invalid data; list all validation errors |
| 401 | Unauthorized | No or invalid authentication credentials |
| 403 | Forbidden | Authenticated but lacks permission |
| 404 | Not Found | Resource does not exist |
| 409 | Conflict | Duplicate create, state conflict (e.g., completed stage) |
| 422 | Unprocessable | Syntactically valid but semantically wrong |
| 500 | Internal Error | Server-side unexpected failure |

## URL Structure

```
/v{major}/resources                    ← collection
/v{major}/resources/{id}               ← single resource
/v{major}/resources/{id}/sub-resources ← nested collection (max 2 levels)
```

No more than 2 nesting levels. Flatten if deeper.

## Request/Response Conventions

- Always `Content-Type: application/json`
- Response body for single resource: the object directly (no wrapper)
- Response body for collections: `{"items": [...], "total": N}`
- Error body: `{"error": "snake_case_code", "message": "human readable", "details": [...]}`

## Pagination

Use offset-based pagination for SQLite backends:

```
GET /v1/sessions?limit=50&offset=0
→ {"items": [...], "total": 142}
```

## Versioning

- Version in path: `/v1/`, `/v2/`
- Increment major version on breaking changes only
- Run old and new version in parallel during migration period

## MCP Tool Conventions (harness-specific)

MCP tools are function calls, not HTTP — but follow the same principles:

- Tool name: `harness_{verb}_{noun}` (see naming-conventions P4 instructions)
- All parameters: typed, described, required vs optional documented
- Success: `{"ok": true, "result": {...}}`
- Failure: `{"ok": false, "error": "snake_case_code", "message": "human readable"}`
- Never raise exceptions to MCP caller — always return structured error dict
- Validate all inputs before processing; return `{"ok": false, "error": "validation_error"}`
  with field-level details on bad input

## Idempotency

For `harness_write_stage`: if the same stage is written twice with the same
attempt number, the second write must be rejected with `409 Conflict` equivalent:
`{"ok": false, "error": "stage_already_written", "message": "..."}`.

This protects append-only state integrity.
