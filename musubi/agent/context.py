"""Driver-side context controls.

musubi-tier: substrate
expires-when: never - the token economics of the LM-call boundary are
  permanent. Every transform here is deterministic and zero-LLM (HI #1).
"""

from __future__ import annotations

import json
import os
from typing import Any

_BASE_SYSTEM = (
    "You are Musubi's standalone agent. You drive MCP tools to complete the "
    "user's software-engineering task and then report the outcome."
)

_VERBOSITY_NOTE = (
    "Be concise. Do not restate the task, the tool catalog, or context the "
    "user already has, and do not narrate what you are about to do. Prefer "
    "acting over explaining: call tools directly. When finished, give a short, "
    "direct answer covering only what changed or what was found - no preamble, "
    "no filler, no summary of your own process unless asked. "
    "If the request needs no tools - a greeting, or a question you can already "
    "answer - reply directly in one turn without calling any tool."
)

DEFAULT_EFFORT_FLOOR = 2048
DEFAULT_CONTEXT_BUDGET = 40_000


def build_system_prompt(extra: str | None = None) -> str:
    """Return the top-level agent system prompt plus verbosity steering."""
    parts = [_BASE_SYSTEM, _VERBOSITY_NOTE]
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)


def effort_floor() -> int:
    """Starting output-token cap per cycle."""
    raw = os.environ.get("MUSUBI_EFFORT_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_EFFORT_FLOOR


def context_budget() -> int:
    """Conversation-size budget in chars; 0 disables fitting."""
    raw = os.environ.get("MUSUBI_CONTEXT_BUDGET", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_CONTEXT_BUDGET


def _content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(json.dumps(block, default=str)) for block in content)
    return len(str(content))


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_content_chars(message.get("content")) for message in messages)


def _retrieve_hint(text: str) -> str:
    marker = 'musubi_retrieve("'
    start = text.find(marker)
    if start == -1:
        return ""
    end = text.find('")', start)
    if end == -1:
        return ""
    return " - recover with " + text[start:end + 2]


def fit_context(
    messages: list[dict[str, Any]],
    *,
    budget_chars: int | None = None,
    keep_last_turns: int = 4,
) -> list[dict[str, Any]]:
    """Elide bulky middle tool results while preserving message structure.

    The leading system message, first user task, and recent messages are kept.
    Eligible middle `tool_result` block contents are replaced biggest-first
    until the conversation fits. Blocks are not removed, so tool_use/tool_result
    pairing remains intact.
    """
    budget = context_budget() if budget_chars is None else budget_chars
    if budget <= 0:
        return messages
    total = _total_chars(messages)
    if total <= budget:
        return messages

    n_messages = len(messages)
    protected: set[int] = set()
    if messages and messages[0].get("role") == "system":
        protected.add(0)
        if n_messages > 1:
            protected.add(1)
    elif messages:
        protected.add(0)
    for index in range(max(0, n_messages - keep_last_turns), n_messages):
        protected.add(index)

    candidates: list[tuple[int, int, int]] = []
    for msg_index, message in enumerate(messages):
        if msg_index in protected:
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if block.get("type") != "tool_result":
                continue
            size = len(json.dumps(block.get("content"), default=str))
            if size > 200:
                candidates.append((size, msg_index, block_index))
    candidates.sort(reverse=True)

    out = list(messages)
    changed: dict[int, dict[str, Any]] = {}
    for _size, msg_index, block_index in candidates:
        if total <= budget:
            break
        msg = changed.get(msg_index)
        if msg is None:
            msg = dict(messages[msg_index])
            msg["content"] = [dict(block) for block in messages[msg_index]["content"]]
            changed[msg_index] = msg
            out[msg_index] = msg
        block = msg["content"][block_index]
        original = block.get("content")
        original_text = (
            original if isinstance(original, str) else json.dumps(original, default=str)
        )
        stub = (
            f"[context-trimmed: {len(original_text)} chars elided to save "
            f"tokens{_retrieve_hint(original_text)}]"
        )
        block["content"] = stub
        total -= max(0, len(original_text) - len(stub))

    return out
