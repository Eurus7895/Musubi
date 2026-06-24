"""OpenAI chat-completions wire converters — pure, SDK-free.

musubi-tier: substrate
expires-when: never — the OpenAI wire shape is the lingua franca shared by
  every OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama, and most
  on-prem gateways). Keeping the converters SDK-free lets both the SDK
  routers and the curl transport build/parse requests from one place.

The agent loop speaks the Anthropic-shaped content_blocks language defined in
`base.LMResponse`. These helpers convert in both directions:

    Anthropic-shaped  →  OpenAI wire   (request side)
    OpenAI wire        →  Anthropic-shaped  (response side)

No `openai` import here on purpose — `openai_router.py` (SDK transport) and
`curl_router.py` (curl transport) both depend on this module, neither the
reverse.
"""

from __future__ import annotations

import json
from typing import Any

# ── Request side: Anthropic-shaped → OpenAI wire ────────────────────────────


def tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
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
    `role: "tool"` message keyed by `tool_call_id`. This helper fans that out
    so the wire-level call sees N messages where the Anthropic version had one.
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


# ── Response side: OpenAI wire → Anthropic-shaped ───────────────────────────


def openai_message_to_blocks(message: Any) -> list[dict[str, Any]]:
    """OpenAI assistant message → Anthropic content blocks.

    Accepts either a wire dict (curl transport, parsed JSON) or an SDK message
    object (SDK transport) — both expose `content` and `tool_calls`.
    """
    blocks: list[dict[str, Any]] = []
    text = _get(message, "content") or ""
    if text:
        blocks.append({"type": "text", "text": text})
    for call in _get(message, "tool_calls") or []:
        fn = _get(call, "function")
        try:
            args = json.loads(_get(fn, "arguments") or "{}")
        except (AttributeError, TypeError, json.JSONDecodeError):
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": _get(call, "id") or "",
            "name": _get(fn, "name"),
            "input": args,
        })
    return blocks


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": _get(usage, "prompt_tokens"),
        "completion_tokens": _get(usage, "completion_tokens"),
        "total_tokens": _get(usage, "total_tokens"),
    }


def _get(obj: Any, key: str) -> Any:
    """Read `key` from a dict (curl/JSON) or an attribute (SDK object)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
