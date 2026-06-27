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
| Original chars | 339,930 |
| Model-visible chars | 6,639 |
| Visible chars saved | 333,291 |
| Overall saving | 98.0% |

The benchmark covers pretty JSON, a Python module, noisy logs, and
Markdown notes. `model_visible_chars` includes the retrieval marker
overhead; `store_stats_body_only` in the JSON artifact records compressor
body size without that marker.

## Verification

The artifact records this verification command:

```powershell
python -m pytest musubi\tests\test_compression.py -q -p no:cacheprovider
```

The recorded result is `34 passed, 1 warning`.
