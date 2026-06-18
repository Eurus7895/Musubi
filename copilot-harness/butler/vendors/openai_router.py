"""OpenAI Chat Completions router for the butler.

harness-tier: substrate
expires-when: never — the LM-call boundary. Vendor-specific glue only.

OpenAI's tool-call shape differs from Anthropic's: tools are wrapped in
`function: {name, description, parameters}`, and assistant responses
carry `tool_calls: [...]` alongside `content`. This router converts in
both directions so the butler's loop only ever speaks the
Anthropic-shaped content_blocks language defined in base.LMResponse.

Optional dependency: `openai` (install via `pip install -e .[openai]`
or `pip install openai`).
"""

from __future__ import annotations

import json
from typing import Any

from butler.vendors.base import LMResponse, LMRouter

_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIRouter(LMRouter):
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK not installed. Install it with "
                "`pip install openai` or `pip install -e .[openai]`."
            ) from exc
        self.model = model or _DEFAULT_MODEL
        self._client = openai.OpenAI()  # reads OPENAI_API_KEY

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        oa_messages = to_openai_messages(messages)
        oa_tools = [_tool_to_openai(t) for t in tools]
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
            usage=_usage_to_dict(getattr(resp, "usage", None)),
        )


# ── Wire-shape converters (exposed for unit tests) ──────────────────────────


def _tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic tool spec → OpenAI function spec."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten an Anthropic-shaped message list into OpenAI's format.

    The interesting case is a user message whose content is a list of
    tool_result blocks: OpenAI expects each tool result as its own
    `role: "tool"` message keyed by `tool_call_id`. This helper fans
    that out so the wire-level call sees N messages where the
    Anthropic version had one.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        out.extend(_message_to_openai_list(message))
    return out


def _message_to_openai_list(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role", "user")
    content = message.get("content", "")

    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if role == "assistant":
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                })
        out: dict[str, Any] = {"role": "assistant"}
        if text_parts:
            out["content"] = "".join(text_parts)
        if tool_calls:
            out["tool_calls"] = tool_calls
        out.setdefault("content", "")  # OpenAI requires the field
        return [out]

    if role == "user":
        # Fan out tool_result blocks into separate role:"tool" messages.
        if isinstance(content, list) and content and all(
            b.get("type") == "tool_result" for b in content
        ):
            return [
                {
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id", ""),
                    "content": _coerce_tool_result_content(b.get("content")),
                }
                for b in content
            ]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        return [{"role": "user", "content": text}]

    return [{"role": role, "content": str(content)}]


def _coerce_tool_result_content(value: Any) -> str:
    """OpenAI's tool-result content must be a string."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def openai_message_to_blocks(message: Any) -> list[dict[str, Any]]:
    """OpenAI assistant message → Anthropic content blocks."""
    blocks: list[dict[str, Any]] = []
    text = getattr(message, "content", None) or ""
    if text:
        blocks.append({"type": "text", "text": text})
    for call in getattr(message, "tool_calls", None) or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except (AttributeError, json.JSONDecodeError):
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": getattr(call, "id", ""),
            "name": call.function.name,
            "input": args,
        })
    return blocks


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
