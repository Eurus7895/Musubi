"""Deterministic, reversible input compression for the substrate.

musubi-tier: substrate
expires-when: never — token cost is permanent; this is durable infra.

Reduces the tokens the driver's model reads by compressing input *before*
it leaves a `musubi_*` tool, while keeping the verbatim original in an
append-only store so the model can pull it back via `musubi_retrieve`
(CCR-style reversibility) and the audit trail stays faithful.

Zero LLM calls — every compressor is deterministic, pure Python (HI #1).
Idea credit: headroom (github.com/headroomlabs-ai/headroom); the learned
text compressor is deliberately NOT adopted (it would be a model call).
"""

from .router import CompressResult, compress, detect_kind, retrieve

__all__ = ["compress", "retrieve", "detect_kind", "CompressResult"]
