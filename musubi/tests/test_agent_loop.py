"""Tests for the agent loop driving a real harness MCP server.

musubi-tier: substrate test - pins the cycle-loop contract. Uses a
canned-response FakeRouter to keep the test hermetic; the real harness
MCP server IS spawned (we want to catch breakage there).
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter


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
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeRouter ran out of canned responses")
        return self._responses.pop(0)


def _musubi_dir() -> Path:
    """The agent-harness package directory (this file's grandparent)."""
    return Path(__file__).resolve().parent.parent


def test_server_env_forwards_musubi_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spawned server must see MUSUBI_* config while unrelated secrets stay out."""
    from agent.run import _server_env

    monkeypatch.setenv("MUSUBI_COMPRESS", "1")
    monkeypatch.setenv("MUSUBI_ROOT", "/some/dir")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-leak")
    env = _server_env()
    assert env["MUSUBI_COMPRESS"] == "1"
    assert env["MUSUBI_ROOT"] == "/some/dir"
    assert "UNRELATED_SECRET" not in env
    assert "PATH" in env


def test_call_with_effort_escalates_on_max_tokens() -> None:
    """A truncated call is retried once at the ceiling."""
    from agent.context import DEFAULT_EFFORT_FLOOR
    from agent.run import EFFORT_CEILING, _call_with_effort

    router = FakeRouter([
        LMResponse(stop_reason="max_tokens", content=[{"type": "text", "text": ""}]),
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    resp = _call_with_effort(router, [{"role": "user", "content": "hi"}], [])
    assert resp.stop_reason == "end_turn"
    assert [c["max_tokens"] for c in router.calls] == [
        DEFAULT_EFFORT_FLOOR,
        EFFORT_CEILING,
    ]


def test_call_with_effort_no_escalation_when_complete() -> None:
    from agent.context import DEFAULT_EFFORT_FLOOR
    from agent.run import _call_with_effort

    router = FakeRouter([
        LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "ok"}]),
    ])
    _call_with_effort(router, [{"role": "user", "content": "hi"}], [])
    assert len(router.calls) == 1
    assert router.calls[0]["max_tokens"] == DEFAULT_EFFORT_FLOOR


def test_loop_returns_text_when_model_does_not_use_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "no tools needed."}],
        ),
    ])
    log = io.StringIO()
    answer = asyncio.run(run_agent("ping", router, _musubi_dir(), log=log))
    assert answer == "no tools needed."
    assert router.calls[0]["tools"], "expected the MCP tool catalog in the first call"


def test_loop_dispatches_real_tool_and_feeds_result_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    second_call_messages = router.calls[1]["messages"]
    user_results = [
        m
        for m in second_call_messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert user_results, "expected a user message carrying tool_result blocks"
    blocks = user_results[-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "call-1"
    assert "session_id" in blocks[0]["content"], "musubi_new_session must return a session_id"


def test_loop_aborts_after_max_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_loop_passes_tool_error_to_model_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = FakeRouter([
        LMResponse(
            stop_reason="tool_use",
            content=[{
                "type": "tool_use",
                "id": "bad-call",
                "name": "musubi_get_active_session",
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
    assert answer == "ack."
    assert len(router.calls) == 2, "loop should have completed both cycles"


class _ExplodingRouter(LMRouter):
    """A vendor whose call fails like a real network/proxy error would."""

    name = "boom"
    model = "boom-1"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001, ARG002
        raise RuntimeError("curl exited 56 ... 407 proxy auth required")


def test_resolve_vendor_labels_which_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_resolve_vendor` returns a human label of how the endpoint was picked,
    so the startup log can show which profile is in effect."""
    from agent import run as run_mod

    cfg = tmp_path / ".musubi" / "llm.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({
        "default": "ollama.local",
        "ollama": {"local": {"model": "llama3.1"}},
    }), encoding="utf-8")
    monkeypatch.setenv("MUSUBI_LLM_CONFIG", str(cfg))
    # Avoid importing a real vendor SDK — only the label logic is under test.
    monkeypatch.setattr(run_mod, "build_from_profile", lambda prof: "ROUTER")

    _, default_src = run_mod._resolve_vendor(None)
    assert default_src == "ollama.local (llm.json default)"

    _, profile_src = run_mod._resolve_vendor("ollama.local")
    assert profile_src == "ollama.local (--profile)"


def test_vendor_error_surfaces_clean_not_as_exception_group() -> None:
    """A vendor.call failure inside the loop must reach the caller as a plain
    RuntimeError with the underlying message — NOT anyio's BaseExceptionGroup
    wall raised at AsyncExitStack teardown (the Windows curl-407 traceback)."""
    log = io.StringIO()
    with pytest.raises(RuntimeError, match="407 proxy auth") as ei:
        asyncio.run(run_agent("hi", _ExplodingRouter(), _musubi_dir(), log=log))
    # The message is a clean one-liner, not a nested group dump.
    assert not isinstance(ei.value, BaseExceptionGroup)
