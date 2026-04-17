---
applyTo: "**/api/**"
priority: P3
---

# API Instructions — Domain Standard (P3)

## REST Conventions

- Use nouns for resource names, not verbs: `/sessions`, not `/createSession`.
- Use plural nouns: `/sessions/{id}`, not `/session/{id}`.
- HTTP methods map to CRUD: `POST` create, `GET` read, `PUT/PATCH` update, `DELETE` delete.
- Use `PATCH` for partial updates, `PUT` for full replacement.

## Status Codes

| Scenario | Code |
|----------|------|
| Created | 201 |
| Success, no body | 204 |
| Bad request / validation error | 400 |
| Unauthorized (missing auth) | 401 |
| Forbidden (authenticated but no access) | 403 |
| Not found | 404 |
| Conflict (e.g., duplicate) | 409 |
| Internal error | 500 |

Never return 200 for errors. Never use 500 for client errors.

## Request Validation

- Validate all request fields at the boundary before calling any domain logic.
- Return 400 with a structured error body listing all validation failures.

```json
{
    "error": "validation_error",
    "details": [
        {"field": "stage", "message": "must be one of: plan, design, code, review"},
        {"field": "output", "message": "required"}
    ]
}
```

## Response Envelopes

- Single resource: return the object directly (no wrapper).
- Collections: `{"items": [...], "total": N}`.
- Errors: `{"error": "error_code", "message": "human readable", "details": [...]}`.

## Versioning

- Version in the URL path: `/v1/sessions`, `/v2/sessions`.
- No version in headers.

## MCP Tool API

MCP tools exposed by `server.py` follow these conventions:

- Tool names: `harness_{verb}_{noun}` — e.g., `harness_write_stage`, `harness_get_skill`.
- All parameters documented with type and description.
- Return a dict with `{"ok": true, "result": ...}` on success.
- Return `{"ok": false, "error": "error_code", "message": "..."}` on failure.
- Never raise exceptions to the MCP caller — always return structured error dicts.

## Security

- No auth tokens in URL query parameters — use headers.
- Validate `Content-Type` on POST/PUT/PATCH requests.
- Set `Content-Type: application/json` on all JSON responses.
- See P1 security rules for injection prevention.
