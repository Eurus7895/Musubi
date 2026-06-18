"""Vendor selection.

harness-tier: substrate
expires-when: never — the env-based default + explicit override is
  the resolution rule for every consumer.

`build_vendor` resolves:
    explicit `vendor` arg
        OR  ANTHROPIC_API_KEY in env → "anthropic"
        OR  OPENAI_API_KEY    in env → "openai"
        OR  error (no key, no choice).

To register a new vendor, add an `elif name == "<your-vendor>":` arm
that imports and instantiates your LMRouter subclass.
"""

from __future__ import annotations

import os

from butler.vendors.base import LMRouter


def build_vendor(name: str | None = None, *, model: str | None = None) -> LMRouter:
    """Construct the LMRouter for `name`, falling back to env detection."""
    resolved = (name or _detect_vendor()).lower().strip()

    if resolved == "anthropic":
        # Import lazily so users without `pip install anthropic` can
        # still use the openai vendor (and vice versa).
        from butler.vendors.anthropic_router import AnthropicRouter

        return AnthropicRouter(model=model)

    if resolved == "openai":
        from butler.vendors.openai_router import OpenAIRouter

        return OpenAIRouter(model=model)

    raise ValueError(
        f"Unknown butler vendor {resolved!r}. "
        f"Supported: 'anthropic', 'openai'."
    )


def _detect_vendor() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
        "or pass --vendor explicitly."
    )
