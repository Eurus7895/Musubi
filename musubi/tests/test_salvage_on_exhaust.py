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


class ForcedAnswerRouter(LMRouter):
    """Pure tool calls while tools are offered, but answers in words once the
    final no-tools call arrives — like a real model that over-eagerly tools."""

    name = "forced"
    model = "forced-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        if not tools:  # the forced no-tools final call → must answer
            return LMResponse(
                stop_reason="end_turn",
                content=[{"type": "text", "text": "Hi there!"}],
            )
        return LMResponse(stop_reason="tool_use", content=[
            {"type": "tool_use", "id": "t1", "name": "musubi_read_file",
             "input": {"path": "README.md"}},
        ])


def test_exhaustion_forces_a_no_tools_final_answer() -> None:
    answer = asyncio.run(
        run_agent("hello", ForcedAnswerRouter(), _musubi_dir(),
                  max_cycles=2, log=io.StringIO())
    )
    assert answer == "Hi there!"


class PureToolRouter(LMRouter):
    """Never produces text even when no tools are offered — nothing to recover."""

    name = "pure-tool"
    model = "pure-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        return LMResponse(stop_reason="tool_use", content=[
            {"type": "tool_use", "id": "t1", "name": "musubi_read_file",
             "input": {"path": "README.md"}},
        ])


def test_exhaustion_with_no_recoverable_text_returns_incomplete_answer() -> None:
    answer = asyncio.run(
        run_agent("hello", PureToolRouter(), _musubi_dir(),
                  max_cycles=2, log=io.StringIO())
    )

    assert "incomplete" in answer.lower()
    assert "2 cycles" in answer
