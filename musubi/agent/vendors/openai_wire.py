"""OpenAI chat-completions wire converters — pure, SDK-free.

musubi-tier: substrate
expires-when: never — the OpenAI wire shape is the lingua franca shared by
  every OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama, and most
  on-prem gateways). Keeping the converters SDK-free lets both the SDK
  routers and the curl transport build/parse requests from one place.

Prompt caching for OpenAI-compatible providers is automatic when the provider
supports it. There is no shared request-side `cache_control` knob like
Anthropic's, so this module normalizes reported cached-token usage instead.

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

# Model families that reject the legacy `max_tokens` field and require
# `max_completion_tokens` instead (the o-series reasoning models and gpt-5+).
# Matched against the leading segment of the model/deployment id so suffixed
# variants (`o1-mini`, `gpt-5-nano`, …) are covered without an exhaustive list.
_MAX_COMPLETION_TOKENS_PREFIXES: tuple[str, ...] = (
    "o1",
    "o3",
    "o4",
    "gpt-5",
)


def token_budget_field(model: str) -> str:
    """Return the request field name for the output-token cap.

    OpenAI's newer model families (o-series, gpt-5) reject the legacy
    `max_tokens` parameter with an `unsupported_parameter` error and require
    `max_completion_tokens`. Older models (gpt-4o, gpt-4, gpt-3.5) keep
    `max_tokens`. Azure/on-prem deployment ids embed the family name, so a
    prefix match on the normalised id selects the right field for both.
    """
    normalised = (model or "").strip().lower()
    if normalised.startswith(_MAX_COMPLETION_TOKENS_PREFIXES):
        return "max_completion_tokens"
    return "max_tokens"


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
        # Fan out tool_result blocks into separate role:"tool" messages, one
        # per tool_call_id. A user turn may MIX tool_result blocks with text
        # (the root's recovery-analysis window appends a text block to the
        # tool-results message — see agent/run.py). Every tool_result must
        # still be emitted, or the preceding assistant `tool_calls` is left
        # unanswered and an OpenAI-family vendor rejects the request with
        # "insufficient tool messages following tool_calls message". So we
        # walk the blocks: tool results become `tool` messages (kept first so
        # they immediately follow the assistant turn), and any trailing text
        # becomes one extra `user` message rather than replacing them.
        if isinstance(content, list):
            tool_msgs: list[dict[str, Any]] = []
            text_parts: list[str] = []
            for b in content:
                btype = b.get("type")
                if btype == "tool_result":
                    tool_msgs.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id", ""),
                        "content": _coerce_tool_result_content(b.get("content")),
                    })
                elif btype == "text":
                    text_parts.append(b.get("text", ""))
            if tool_msgs:
                out_msgs = list(tool_msgs)
                trailing = "".join(text_parts)
                if trailing:
                    out_msgs.append({"role": "user", "content": trailing})
                return out_msgs
            return [{"role": "user", "content": "".join(text_parts)}]

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


def finish_reason_to_stop(finish_reason: Any) -> str:
    """Map OpenAI-compatible finish reasons to the loop's stop reasons."""
    reason = str(finish_reason or "").strip().lower()
    if reason == "length":
        return "max_tokens"
    if reason == "tool_calls":
        return "tool_use"
    return "end_turn"


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    out = {
        "prompt_tokens": _get(usage, "prompt_tokens"),
        "completion_tokens": _get(usage, "completion_tokens"),
        "total_tokens": _get(usage, "total_tokens"),
    }
    cached = _get(_get(usage, "prompt_tokens_details"), "cached_tokens")
    if cached:
        out["cache_read_input_tokens"] = cached
    return out


def _get(obj: Any, key: str) -> Any:
    """Read `key` from a dict (curl/JSON) or an attribute (SDK object)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
