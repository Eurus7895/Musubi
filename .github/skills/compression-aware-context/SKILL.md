---
name: compression-aware-context
description: Use Musubi compression summaries, retrieve markers, and compression stats safely.
musubi-tier: substrate
expires-when: never (compression markers are part of the model-visible substrate contract)
triggers:
  - musubi_retrieve
  - compressed output
  - retrieve marker
  - compression stats
  - token savings
tools:
  - musubi_retrieve
  - musubi_compression_stats
  - musubi_compress
---

# Compression-aware Context

Use this skill when tool output, file content, logs, JSON, code, or prose has
been compressed before reaching the model.

## Rules

- Treat compressed content as a structural summary, not as the verbatim source.
- If exact text, exact field values, exact stack frames, or exact code bodies
  matter, call `musubi_retrieve(ref_id)` before making claims.
- Never invent details hidden behind a retrieve marker.
- Use `musubi_compression_stats()` when the user asks how much compression
  helped or when reporting end-of-session savings.
- If a payload is small or not actually compressed, continue normally.

## Workflow

1. Read the compressed summary for shape and relevance.
2. Identify whether the task needs exact original detail.
3. Retrieve only the specific `ref_id` needed for correctness.
4. Continue from the retrieved original when exactness matters.
5. Report token or character savings as measured runtime data, not as a price
   or credit guarantee.
