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
        run_agent("create a report file", AlwaysToolsRouter(), _musubi_dir(),
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
        run_agent("create a report file", ForcedAnswerRouter(), _musubi_dir(),
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
        run_agent("create a report file", PureToolRouter(), _musubi_dir(),
                  max_cycles=2, log=io.StringIO())
    )

    assert "incomplete" in answer.lower()
    assert "2 cycles" in answer


def test_token_budget_exhaustion_returns_incomplete_answer() -> None:
    answer = asyncio.run(
        run_agent(
            "create a report file",
            PureToolRouter(),
            _musubi_dir(),
            max_cycles=2,
            max_tokens=1,
            log=io.StringIO(),
        )
    )

    assert "incomplete" in answer.lower()
    assert "token budget exhausted" in answer.lower()


class BudgetAccountingRouter(AlwaysToolsRouter):
    """Tracks calls so the budget-halt test pins one completed cycle."""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        self.calls += 1
        response = super().call(messages, tools, max_tokens=max_tokens)
        response.usage = {"input_tokens": 1_000, "output_tokens": 5_500}
        return response


def test_token_budget_halt_marks_salvaged_text_incomplete() -> None:
    router = BudgetAccountingRouter()
    answer = asyncio.run(
        run_agent(
            "create a report file",
            router,
            _musubi_dir(),
            max_cycles=3,
            # The router reports 6,500 provider tokens: enough headroom for one
            # completed cycle (and salvageable text), while the next preflight
            # must halt independently of prompt/tool-schema size.
            max_tokens=7_000,
            log=io.StringIO(),
        )
    )

    assert answer.startswith("[incomplete]")
    assert "token budget exhausted" in answer.lower()
    assert "Hello! Working on it." in answer
    assert router.calls == 1
