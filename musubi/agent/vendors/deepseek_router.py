"""DeepSeek Chat Completions router for the agent.

musubi-tier: substrate
expires-when: never - DeepSeek is another LMRouter inject point, not a
  substrate-side model call.

DeepSeek exposes an OpenAI-compatible API, so this router is a small preset
over OpenAIRouter: DeepSeek's default base URL, default env key, and current
default model.
"""

from __future__ import annotations

import os
from typing import Any

from agent.vendors.openai_router import OpenAIRouter

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


class DeepSeekRouter(OpenAIRouter):
    name = "deepseek"

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_query: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            model=model or DEFAULT_DEEPSEEK_MODEL,
            base_url=base_url or DEFAULT_DEEPSEEK_BASE_URL,
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
            default_query=default_query,
        )
