"""Tests for the agent vendor abstraction — factory + wire converters.

harness-tier: substrate test — every vendor router must round-trip
through the same content_blocks shape so the loop is vendor-agnostic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.vendors import LMResponse, LMRouter
from agent.vendors.factory import build_vendor
from agent.vendors.openai_router import (
    openai_message_to_blocks,
    to_openai_messages,
)


# ── Factory env detection ──────────────────────────────────────────────────


def test_factory_explicit_anthropic_requires_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the user explicitly asks for anthropic but the SDK isn't
    available, the factory raises with a clear install hint."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # If anthropic is installed in this env, this test is a no-op; skip.
    try:
        import anthropic  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
            build_vendor("anthropic")


def test_factory_rejects_unknown_vendor() -> None:
    with pytest.raises(ValueError, match="Unknown agent vendor"):
        build_vendor("cohere")


def test_factory_errors_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key"):
        build_vendor()


def test_factory_prefers_anthropic_when_both_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented precedence: anthropic wins the env race."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        pytest.skip("anthropic SDK not installed; precedence test n/a")
    vendor = build_vendor()
    assert vendor.name == "anthropic"


# ── LMResponse / LMRouter contract ─────────────────────────────────────────


def test_lmresponse_minimal_construction() -> None:
    resp = LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "hi"}])
    assert resp.stop_reason == "end_turn"
    assert resp.content[0]["text"] == "hi"
    assert resp.usage is None


def test_lmrouter_is_abstract() -> None:
    """A subclass that forgets `call` must not instantiate."""
    with pytest.raises(TypeError):
        LMRouter()  # type: ignore[abstract]


# ── OpenAI wire converters: messages out (Anthropic → OpenAI) ──────────────


def test_openai_messages_str_user_passthrough() -> None:
    messages = [{"role": "user", "content": "hello"}]
    assert to_openai_messages(messages) == [{"role": "user", "content": "hello"}]


def test_openai_messages_assistant_text_plus_tool_use() -> None:
    """Anthropic assistant turn with text + tool_use becomes one OpenAI
    assistant message with `tool_calls`."""
    messages = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "thinking..."},
            {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}},
        ],
    }]
    out = to_openai_messages(messages)
    assert len(out) == 1
    msg = out[0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "thinking..."
    assert msg["tool_calls"][0]["id"] == "t1"
    assert msg["tool_calls"][0]["function"]["name"] == "lookup"


def test_openai_messages_tool_results_fan_out() -> None:
    """A user message containing N tool_results MUST become N
    role:'tool' messages — that's the OpenAI wire format. The pre-fix
    converter only emitted the first one."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "A"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "B"},
            {"type": "tool_result", "tool_use_id": "t3", "content": "C"},
        ],
    }]
    out = to_openai_messages(messages)
    assert len(out) == 3
    assert [m["tool_call_id"] for m in out] == ["t1", "t2", "t3"]
    assert [m["role"] for m in out] == ["tool", "tool", "tool"]
    assert [m["content"] for m in out] == ["A", "B", "C"]


def test_openai_messages_tool_result_content_coerced_to_string() -> None:
    messages = [{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": {"k": "v"}},
        ],
    }]
    out = to_openai_messages(messages)
    assert out[0]["content"] == '{"k": "v"}'


# ── OpenAI wire converters: response in (OpenAI → Anthropic blocks) ────────


def test_openai_blocks_text_only() -> None:
    msg = SimpleNamespace(content="hello world", tool_calls=None)
    assert openai_message_to_blocks(msg) == [{"type": "text", "text": "hello world"}]


def test_openai_blocks_tool_call_only() -> None:
    """The response with no `content` field still yields the tool_use block."""
    call = SimpleNamespace(
        id="t1",
        function=SimpleNamespace(name="lookup", arguments='{"q":"x"}'),
    )
    msg = SimpleNamespace(content=None, tool_calls=[call])
    blocks = openai_message_to_blocks(msg)
    assert blocks == [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}}]


def test_openai_blocks_malformed_tool_args_become_empty_dict() -> None:
    """A vendor occasionally returns invalid JSON in arguments — the
    block must still parse with an empty input dict, never raise."""
    call = SimpleNamespace(
        id="t1",
        function=SimpleNamespace(name="lookup", arguments="{not valid json}"),
    )
    msg = SimpleNamespace(content="", tool_calls=[call])
    blocks = openai_message_to_blocks(msg)
    assert blocks == [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {}}]


def test_openai_blocks_mixed_text_and_tool_use() -> None:
    call = SimpleNamespace(
        id="t1",
        function=SimpleNamespace(name="fn", arguments="{}"),
    )
    msg = SimpleNamespace(content="here goes", tool_calls=[call])
    blocks = openai_message_to_blocks(msg)
    assert [b["type"] for b in blocks] == ["text", "tool_use"]
