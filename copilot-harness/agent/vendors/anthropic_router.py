"""Anthropic Messages API router for the agent.

harness-tier: substrate
expires-when: never — the LM-call boundary. Vendor-specific glue only.

Optional dependency: `anthropic` (install via `pip install -e .[anthropic]`
or `pip install anthropic`). Imported inside __init__ so the module
itself is import-safe — only constructing AnthropicRouter triggers
the dependency check.
"""

from __future__ import annotations

from typing import Any

from agent.vendors.base import LMResponse, LMRouter

_DEFAULT_MODEL = "claude-haiku-4-5"


class AnthropicRouter(LMRouter):
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic SDK not installed. Install it with "
                "`pip install anthropic` or `pip install -e .[anthropic]`."
            ) from exc
        self.model = model or _DEFAULT_MODEL
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            tools=tools,
            messages=messages,
        )
        # Block objects → plain dicts so the loop is vendor-agnostic.
        content = [_block_to_dict(block) for block in msg.content]
        usage = _usage_to_dict(getattr(msg, "usage", None))
        return LMResponse(
            stop_reason=str(msg.stop_reason or ""),
            content=content,
            usage=usage,
        )


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Convert one Anthropic content block to a plain dict.

    Block objects expose `.model_dump()` (pydantic) or `.dict()`. Falls
    back to attribute scrape for hand-mocked blocks in tests.
    """
    if hasattr(block, "model_dump"):
        return dict(block.model_dump())
    if hasattr(block, "dict"):
        return dict(block.dict())
    # Best effort for test mocks.
    result: dict[str, Any] = {"type": getattr(block, "type", "unknown")}
    for attr in ("text", "id", "name", "input"):
        value = getattr(block, attr, None)
        if value is not None:
            result[attr] = value
    return result


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return dict(usage.model_dump())
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }
