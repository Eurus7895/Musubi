---
name: api-design
description: Designs REST APIs and MCP tool interfaces with consistent conventions. Use when the user is designing an API, endpoint, REST interface, MCP tool, or tool schema.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - rest api
  - endpoint
  - mcp tool
  - tool schema
  - interface design
  - http method
  - request schema
---

## Purpose

Produce well-structured API and MCP tool interface designs that are consistent,
secure, and implementable without ambiguity.

## Procedure

1. **Identify resources.** What are the nouns? (sessions, stages, skills, results)
   Each noun becomes a resource path.

2. **Map operations to HTTP methods.**
   POST → create, GET → read, PATCH → partial update, DELETE → remove.
   Avoid custom verbs in paths.

3. **Define request and response schemas.**
   - Required vs optional fields
   - Types for every field
   - Example values

4. **Define error responses.**
   Every endpoint has a 400 and 500 response schema.
   Use consistent `{"error": "code", "message": "..."}` envelope.

5. **For MCP tools:** name as `musubi_{verb}_{noun}`. Document all parameters
   with type and description. Return `{"ok": bool, "result": ...}` on success,
   `{"ok": false, "error": "code", "message": "..."}` on failure.

6. **Security pass.** No auth tokens in query params. Validate all inputs.
   Rate limit considerations for execution endpoints.

## Assets

`openapi-template.yaml` — use as starting point for new API definitions.
Run via: `musubi_run_asset("api-design", "openapi-template.yaml", {"title": "...", "version": "..."})`
Returns a populated OpenAPI 3.0 skeleton.

## When to Load References

- Load `rest-principles.md` when: designing a new resource endpoint, or when
  unsure about HTTP method choice, status code, or URL structure
