"""Cycle-exhaustion salvage (bugfix).

A model that calls a tool on every cycle never hits the loop's text-break path,
so before this guard the whole turn hard-failed with "exceeded N cycles" even
when the model had produced perfectly good text alongside its tool calls. The
root agent now salvages that last assistant text instead of erroring.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


class AlwaysToolsRouter(LMRouter):
    """Emits text PLUS a read-file tool call on every cycle — never stops on
    its own, so the loop exhausts."""

    name = "always-tools"
    model = "always-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        return LMResponse(stop_reason="tool_use", content=[
            {"type": "text", "text": "Hello! Working on it."},
            {"type": "tool_use", "id": "t1", "name": "musubi_read_file",
             "input": {"path": "README.md"}},
        ])


def test_exhaustion_salvages_last_assistant_text() -> None:
    answer = asyncio.run(
        run_agent("hello", AlwaysToolsRouter(), _musubi_dir(),
                  max_cycles=3, log=io.StringIO())
    )
    assert answer == "Hello! Working on it."


class PureToolRouter(LMRouter):
    """Emits a tool call with NO text every cycle — nothing to salvage."""

    name = "pure-tool"
    model = "pure-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        return LMResponse(stop_reason="tool_use", content=[
            {"type": "tool_use", "id": "t1", "name": "musubi_read_file",
             "input": {"path": "README.md"}},
        ])


def test_exhaustion_with_no_text_still_raises() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="exceeded"):
        asyncio.run(
            run_agent("hello", PureToolRouter(), _musubi_dir(),
                      max_cycles=2, log=io.StringIO())
        )
