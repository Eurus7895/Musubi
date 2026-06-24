"""OpenAI Chat Completions router for the agent (SDK transport).

musubi-tier: substrate
expires-when: never — the LM-call boundary. Vendor-specific glue only.

OpenAI's tool-call shape differs from Anthropic's: tools are wrapped in
`function: {name, description, parameters}`, and assistant responses carry
`tool_calls: [...]` alongside `content`. The wire conversion lives in
`openai_wire.py` (SDK-free) so the curl transport can share it; this module is
the thin `openai` SDK shell.

`base_url` / `api_key` are accepted so the same router drives any
OpenAI-compatible endpoint (OpenAI cloud, a self-hosted gateway, Ollama's
`/v1`). Endpoints that must be reached through `curl` (on-prem proxy / CA /
mTLS), including Azure's deployment-in-path URL, use `curl_router.py` instead.

Optional dependency: `openai` (install via `pip install -e .[openai]`
or `pip install openai`).
"""

from __future__ import annotations

from typing import Any

from agent.vendors.base import LMResponse, LMRouter
from agent.vendors.openai_wire import (
    openai_message_to_blocks,
    to_openai_messages,
    tool_to_openai,
    usage_to_dict,
)

# Re-export for back-compat: callers/tests import these from openai_router.
__all__ = [
    "OpenAIRouter",
    "openai_message_to_blocks",
    "to_openai_messages",
    "tool_to_openai",
    "usage_to_dict",
]

_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIRouter(LMRouter):
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK not installed. Install it with "
                "`pip install openai` or `pip install -e .[openai]`."
            ) from exc
        self.model = model or _DEFAULT_MODEL
        # `base_url`/`api_key` are None for the default OpenAI cloud path; the
        # SDK then reads OPENAI_API_KEY from env, matching prior behaviour.
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        oa_messages = to_openai_messages(messages)
        oa_tools = [tool_to_openai(t) for t in tools]
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            tools=oa_tools or None,
            messages=oa_messages,
        )
        choice = resp.choices[0]
        content_blocks = openai_message_to_blocks(choice.message)
        stop = "tool_use" if choice.finish_reason == "tool_calls" else "end_turn"
        return LMResponse(
            stop_reason=stop,
            content=content_blocks,
            usage=usage_to_dict(getattr(resp, "usage", None)),
        )
