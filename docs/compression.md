# Compression Capability

Musubi compression is deterministic, reversible, and substrate-side. It
reduces model-visible tool/context payloads while storing the verbatim
original in the compression blob store. When the model needs exact source
text, it calls `musubi_retrieve(ref_id)`.

## Current Native Compressors

Token economics Step 3 adds smarter native compressors without adding
Headroom as a dependency and without adding any substrate-side LLM call:

- JSON smart-crush: shape, collection counts, path stats, and bounded
  first/last samples.
- Python structure compression: AST-derived imports, class signatures,
  function signatures, and method signatures; invalid Python falls back
  to conservative comment/blank stripping.
- Log pattern grouping: normalized repeated patterns with first and last
  examples.
- Heading-aware text outline: headings, paragraph counts, and bounded
  first/last snippets.

All compressors are lossy views over a retrievable original. The router
stores a blob only when the model-visible compressed result, including
retrieval marker overhead, is smaller than the original.

## Context Packing

Token economics Step 4 applies the same reversible compression at the
driver-side LM boundary. When a conversation exceeds
`MUSUBI_CONTEXT_BUDGET`, `agent/context.py::fit_context` protects the
system prompt, the first user task, and recent turns. It then compresses
old `tool_result` blocks before falling back to a short
`context-trimmed` stub. Tool-result blocks keep their `tool_use_id`, so
tool-use/tool-result pairing remains intact, and any compressed or
trimmed view keeps a `musubi_retrieve(ref_id)` marker when a ref is
available.

## Eval Gate

Token economics Step 5 adds a deterministic compression eval gate. The
default path makes no LLM call: it runs bundled JSON, Python, log, text,
and context-packing cases through the native compressors, then verifies
visible savings, verbatim retrieval, retrieve-marker availability, and
quality-proxy coverage for the structural summaries.

```powershell
python -m agent.compression_eval --output artifacts/compression/compression_benchmark_results.json
```

An optional manual probe can ask a real configured model whether it calls
`musubi_retrieve` when an exact detail is only recoverable from the
original. That probe lives in the standalone driver, not in the substrate:

```powershell
python -m agent.compression_eval --real-lm --profile azure.work
```

## Latest Artifact

The static capability artifact lives under
`artifacts/compression/`:

- `compression_capability_report.html`: visual report for browser review.
- `compression_benchmark_results.json`: machine-readable benchmark data.
- `compression_feature_results.png`: generated chart image.
- `compression_benchmark.db`: SQLite blob store used by the benchmark.

Latest benchmark summary:

| Metric | Value |
|---|---:|
| Payloads compressed | 4 / 4 |
| Retrieve verification | 4 / 4 OK |
| Payload original chars | 266,851 |
| Payload model-visible chars | 6,434 |
| Payload visible chars saved | 260,417 |
| Payload overall saving | 97.6% |
| Context original chars | 188,943 |
| Context packed chars | 3,490 |
| Context visible chars saved | 185,453 |
| Context overall saving | 98.2% |
| Retrieve markers | 5 |
| Quality proxy | OK |

The benchmark covers pretty JSON, a Python module, noisy logs, and
Markdown notes, plus one synthetic conversation packing scenario. In the
context scenario, one old tool result is compressed, one raw block is
trimmed, and the recent turn is preserved. `model_visible_chars`
includes retrieval marker overhead. Token counts and character savings
are stable evaluation metrics and budget inputs remain token-only.

## Verification

The artifact records this verification command:

```powershell
python -m pytest musubi\tests\test_mcp_gateway.py musubi\tests\test_context.py musubi\tests\test_agent_loop.py musubi\tests\test_compression.py musubi\tests\test_compression_eval.py -q -p no:cacheprovider
```

The recorded result is `109 passed, 1 warning`.
