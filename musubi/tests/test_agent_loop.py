"""Tests for the agent loop driving a real harness MCP server.

musubi-tier: substrate test — pins the cycle-loop contract. Uses a
canned-response FakeRouter to keep the test hermetic; the real harness
MCP server IS spawned (we want to catch breakage there).
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest

from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter


# ── Test infrastructure: FakeRouter replays a canned response list ─────────


class FakeRouter(LMRouter):
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[LMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        # Capture the call for assertions in tests.
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeRouter ran out of canned responses")
        return self._responses.pop(0)


def _musubi_dir() -> Path:
    """The agent-harness package directory (this file's grandparent)."""
    return Path(__file__).resolve().parent.parent


# ── Loop terminates immediately on end_turn ────────────────────────────────


def test_loop_returns_text_when_model_does_not_use_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "no tools needed."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent("ping", router, _musubi_dir(), log=log))
    assert answer == "no tools needed."
    # Tool catalog WAS handed to the model (the harness server is real).
    assert router.calls[0]["tools"], "expected the MCP tool catalog in the first call"


# ── Loop dispatches a real tool, feeds result back, terminates ─────────────


def test_loop_dispatches_real_tool_and_feeds_result_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two-cycle interaction:
      cycle 0: model asks for musubi_new_session
      cycle 1: model emits the final text after seeing the result.
    We verify the harness response is fed back as a tool_result block."""
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "call-1",
                "name": "musubi_new_session",
                "input": {"request": "smoke from agent loop test"},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "session opened."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent("open a session", router, _musubi_dir(), log=log))
    assert answer == "session opened."
    # The second call must contain the tool_result fed back to the model.
    second_call_messages = router.calls[1]["messages"]
    user_results = [m for m in second_call_messages if m["role"] == "user"
                    and isinstance(m["content"], list)]
    assert user_results, "expected a user message carrying tool_result blocks"
    blocks = user_results[-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "call-1"
    assert "session_id" in blocks[0]["content"], "musubi_new_session must return a session_id"


# ── Loop honours max_cycles ───────────────────────────────────────────────


def test_loop_aborts_after_max_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the model loops on tool_use forever, the agent bails so the
    user isn't billed indefinitely."""
    looping_response = LMResponse(
        stop_reason="tool_use",
        content=[{
            "type": "tool_use",
            "id": "x",
            "name": "musubi_get_active_session",
            "input": {},
        }],
    )
    router = FakeRouter([looping_response, looping_response, looping_response])
    log = io.StringIO()
    with pytest.raises(RuntimeError, match="exceeded 2 cycles"):
        asyncio.run(run_agent(
            "loop forever", router, _musubi_dir(), max_cycles=2, log=log,
        ))


# ── Tool-result content propagation: tool errors don't crash the loop ─────


def test_loop_passes_tool_error_to_model_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool call that fails must surface as a tool_result content
    string, not as an exception that kills the loop. The model then
    decides whether to retry or stop. We trigger it by calling a tool
    with intentionally bad args (e.g. wrong type for `session_id`)."""
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "bad-call",
                "name": "musubi_get_active_session",
                # Intentionally pass an unexpected kwarg to provoke a tool error.
                "input": {"nonexistent_param": True, "another": [1, 2, 3]},
            }],
        ),
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "ack."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent(
        "bad tool", router, _musubi_dir(), log=log,
    ))
    # Either the harness tolerates the extra kwargs (returns ok) OR the
    # call errors and the result content carries the error message; in
    # both cases the loop completes and returns the final text.
    assert answer == "ack."
    assert len(router.calls) == 2, "loop should have completed both cycles"
