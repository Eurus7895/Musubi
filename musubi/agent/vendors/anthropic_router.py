"""Anthropic Messages API router for the agent.

musubi-tier: substrate
expires-when: never — the LM-call boundary. Vendor-specific glue only.

Optional dependency: `anthropic` (install via `pip install -e .[anthropic]`
or `pip install anthropic`). Imported inside __init__ so the module
itself is import-safe — only constructing AnthropicRouter triggers
the dependency check.
"""

from __future__ import annotations

import os
from typing import Any

from agent.context import split_system_prompt
from agent.vendors.base import LMResponse, LMRouter

_DEFAULT_MODEL = "claude-haiku-4-5"


def _cache_enabled() -> bool:
    """CacheAligner is on by default; `MUSUBI_PROMPT_CACHE=0` opts out.

    Marking the static prefix (system + tool catalog) with `cache_control`
    lets Anthropic's prompt cache hit across the loop's cycles — the tool
    schemas alone are the largest, most repeated part of every request.
    Disable it for a gateway that rejects `cache_control`.
    """
    return os.environ.get("MUSUBI_PROMPT_CACHE", "").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _split_system(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Pop a leading `role:"system"` message (the top-level agent's convention)
    into Anthropic's separate `system` field. Sub-agents fold their prompt into
    a user message, so this is a no-op for them."""
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content")
        if isinstance(content, str):
            return content, messages[1:]
    return None, messages


def _system_param(system_text: str | None, cache: bool) -> Any:
    """Anthropic `system=`: a cache-marked text block when caching is on, else
    the plain string (or None to omit the field)."""
    if not system_text:
        return None
    if not cache:
        return system_text
    stable, extra = split_system_prompt(system_text)
    blocks: list[dict[str, Any]] = [{
        "type": "text",
        "text": stable,
        "cache_control": {"type": "ephemeral"},
    }]
    if extra:
        blocks.append({"type": "text", "text": extra})
    return blocks


def _cache_aligned_tools(
    tools: list[dict[str, Any]], cache: bool
) -> list[dict[str, Any]]:
    """Mark the last tool with `cache_control` so the whole tool block (the
    biggest static prefix) caches. Copies — never mutates the caller's list."""
    if not cache or not tools:
        return tools
    out = [dict(t) for t in tools]
    out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


class AnthropicRouter(LMRouter):
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic SDK not installed. Install it with "
                "`pip install anthropic` or `pip install -e .[anthropic]`."
            ) from exc
        self.model = model or _DEFAULT_MODEL
        # base_url/api_key default to None → SDK reads ANTHROPIC_API_KEY and the
        # public endpoint, matching prior behaviour. Set them for an on-prem
        # Anthropic-compatible gateway.
        self._client = anthropic.Anthropic(base_url=base_url, api_key=api_key)

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        cache = _cache_enabled()
        system_text, body = _split_system(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "tools": _cache_aligned_tools(tools, cache),
            "messages": body,
        }
        system_param = _system_param(system_text, cache)
        if system_param is not None:
            kwargs["system"] = system_param
        msg = self._client.messages.create(**kwargs)
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
