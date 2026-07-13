"""Vendor-agnostic LMRouter interface + LMResponse shape.

musubi-tier: substrate
expires-when: never — the agent's only requirement on a vendor is
  this interface. New vendors slot in by implementing it.

The content-block shape mirrors Anthropic's Messages API because it's
the more expressive of the two common shapes (text + tool_use can
interleave in a single response, and tool_use carries a stable
tool_use_id we echo back as tool_result). OpenAI responses are
converted into this shape by the OpenAI router.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LMResponse:
    """One assistant turn from the LLM.

    Fields:
        stop_reason   "tool_use" | "end_turn" | "max_tokens" | ...
                      The agent loop terminates when this is NOT
                      "tool_use". Anything else is treated as the
                      model's signal that it's done.
        content       Anthropic-shaped list of content blocks. The loop
                      walks it for `tool_use` blocks to dispatch and
                      reads the trailing `text` blocks for the final
                      answer.
        usage         Optional dict surfacing vendor-side token counts.
                      Used for per-cycle token audit. Shape is
                      vendor-specific; the loop normalizes it.
    """

    stop_reason: str
    content: list[dict[str, Any]]
    usage: dict[str, Any] | None = None


class LMRouter(ABC):
    """Vendor-agnostic LM call boundary for the agent loop.

    Subclasses live in agent/vendors/<name>_router.py and are
    registered in agent/vendors/factory.py::build_vendor().
    """

    #: Human-readable identifier for logs ("anthropic", "openai", ...).
    name: str = "unknown"

    #: The model id passed on every call. Subclass __init__ resolves
    #: the user-supplied / default value.
    model: str = ""

    #: Optional operator-set per-call output cap resolved from llm.json.
    max_output_tokens: int | None = None

    @abstractmethod
    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        """One blocking LLM call. Returns the assistant turn.

        `messages` is Anthropic-shaped: [{"role", "content"}, ...]
        where `content` is either a string or a list of content blocks.
        `tools` is the Anthropic tool spec: [{"name", "description",
        "input_schema"}, ...]. Vendor routers convert internally if
        their API uses a different wire shape (OpenAI's `tools` field
        wraps each tool under `function: {...}`).
        """
