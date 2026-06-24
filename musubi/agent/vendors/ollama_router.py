"""Ollama router — local models via Ollama's OpenAI-compatible endpoint.

musubi-tier: substrate
expires-when: never — the LM-call boundary. Vendor-specific glue only.

Ollama (https://ollama.com) serves local models behind an OpenAI-compatible
`/v1` API *with tool-calling*, so this is a thin preset over `OpenAIRouter`:
point the OpenAI SDK at the local Ollama host and reuse every wire converter.
No new dependency — rides the `[openai]` extra. No API key: Ollama ignores it,
but the SDK requires the field to be non-empty, so we pass a placeholder.

Host resolution: explicit `base_url` arg → `OLLAMA_HOST` env → localhost.
"""

from __future__ import annotations

import os

from agent.vendors.openai_router import OpenAIRouter

_DEFAULT_MODEL = "llama3.1"
_DEFAULT_HOST = "http://localhost:11434"


class OllamaRouter(OpenAIRouter):
    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        host = base_url or os.environ.get("OLLAMA_HOST") or _DEFAULT_HOST
        # Ollama's OpenAI-compatible surface lives under /v1; tolerate a host
        # that already includes it.
        v1 = host if host.rstrip("/").endswith("/v1") else host.rstrip("/") + "/v1"
        super().__init__(
            model=model or _DEFAULT_MODEL,
            base_url=v1,
            api_key=api_key or "ollama",  # placeholder; Ollama ignores it
        )
