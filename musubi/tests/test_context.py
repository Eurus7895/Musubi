"""Tests for driver-side context controls — verbosity steering, effort
routing, deterministic context fitting, and the Anthropic CacheAligner.

musubi-tier: substrate test — pins the zero-LLM token-economy transforms.
"""

from __future__ import annotations

import json

from agent.context import (
    DEFAULT_EFFORT_FLOOR,
    build_system_prompt,
    context_budget,
    effort_floor,
    fit_context,
)

# ── Verbosity steering ───────────────────────────────────────────────────────

def test_system_prompt_carries_terse_instruction():
    p = build_system_prompt()
    assert "concise" in p.lower()
    assert "do not restate" in p.lower()


def test_system_prompt_appends_extra_after_steering():
    p = build_system_prompt("task-specific note")
    assert p.endswith("task-specific note")
    assert "concise" in p.lower()


# ── Effort routing ───────────────────────────────────────────────────────────

def test_effort_floor_default(monkeypatch):
    monkeypatch.delenv("MUSUBI_EFFORT_TOKENS", raising=False)
    assert effort_floor() == DEFAULT_EFFORT_FLOOR


def test_effort_floor_env_override(monkeypatch):
    monkeypatch.setenv("MUSUBI_EFFORT_TOKENS", "512")
    assert effort_floor() == 512


def test_effort_floor_ignores_garbage(monkeypatch):
    monkeypatch.setenv("MUSUBI_EFFORT_TOKENS", "lots")
    assert effort_floor() == DEFAULT_EFFORT_FLOOR


# ── IntelligentContext (deterministic fitting) ───────────────────────────────

def _convo_with_big_results() -> list[dict]:
    big = "X" * 5000
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "the task"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "t", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": big}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "b", "name": "t", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b", "content": big}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]


def test_fit_context_noop_when_under_budget():
    msgs = _convo_with_big_results()
    out = fit_context(msgs, budget_chars=1_000_000)
    assert out is msgs  # same object, untouched


def test_fit_context_disabled_when_budget_zero():
    msgs = _convo_with_big_results()
    assert fit_context(msgs, budget_chars=0) is msgs


def test_fit_context_elides_oldest_largest_first():
    msgs = _convo_with_big_results()
    out = fit_context(msgs, budget_chars=6000, keep_last_turns=2)
    # The oldest big tool_result (index 3) is elided; structure preserved.
    elided = out[3]["content"][0]["content"]
    assert "context-trimmed" in elided
    assert out[3]["content"][0]["type"] == "tool_result"
    assert out[3]["content"][0]["tool_use_id"] == "a"
    # Protected: system, task, and the original input list are intact objects.
    assert out[0] is msgs[0]
    assert out[1] is msgs[1]


def test_fit_context_keeps_recent_turns():
    msgs = _convo_with_big_results()
    out = fit_context(msgs, budget_chars=6000, keep_last_turns=2)
    # The last tool_result (index 5) is within keep_last_turns → untouched.
    assert out[5]["content"][0]["content"] == "X" * 5000


def test_fit_context_preserves_retrieve_marker():
    marker = 'see [musubi:compressed ref=abc; call musubi_retrieve("abc123") ok]'
    body = "Y" * 5000 + "\n" + marker
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": body}]},
        {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
    ]
    out = fit_context(msgs, budget_chars=500, keep_last_turns=1)
    stub = out[2]["content"][0]["content"]
    assert 'musubi_retrieve("abc123")' in stub


def test_fit_context_does_not_mutate_input():
    msgs = _convo_with_big_results()
    before = json.dumps(msgs)
    fit_context(msgs, budget_chars=6000, keep_last_turns=2)
    assert json.dumps(msgs) == before  # original untouched; copies returned


def test_context_budget_env_override(monkeypatch):
    monkeypatch.setenv("MUSUBI_CONTEXT_BUDGET", "1234")
    assert context_budget() == 1234


# ── CacheAligner (Anthropic prefix caching) ──────────────────────────────────

def test_split_system_pops_leading_system():
    from agent.vendors.anthropic_router import _split_system
    sys_text, body = _split_system([
        {"role": "system", "content": "you are x"},
        {"role": "user", "content": "hi"},
    ])
    assert sys_text == "you are x"
    assert body == [{"role": "user", "content": "hi"}]


def test_split_system_noop_without_leading_system():
    from agent.vendors.anthropic_router import _split_system
    msgs = [{"role": "user", "content": "hi"}]
    sys_text, body = _split_system(msgs)
    assert sys_text is None
    assert body is msgs


def test_cache_aligned_tools_marks_last_only():
    from agent.vendors.anthropic_router import _cache_aligned_tools
    tools = [{"name": "a"}, {"name": "b"}]
    out = _cache_aligned_tools(tools, cache=True)
    assert "cache_control" not in out[0]
    assert out[-1]["cache_control"] == {"type": "ephemeral"}
    # Caller's list is not mutated.
    assert "cache_control" not in tools[-1]


def test_cache_aligned_tools_passthrough_when_disabled():
    from agent.vendors.anthropic_router import _cache_aligned_tools
    tools = [{"name": "a"}]
    assert _cache_aligned_tools(tools, cache=False) is tools


def test_system_param_block_when_cached_string_when_not():
    from agent.vendors.anthropic_router import _system_param
    assert _system_param("hi", cache=False) == "hi"
    blocks = _system_param("hi", cache=True)
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert _system_param(None, cache=True) is None


def test_cache_enabled_default_and_optout(monkeypatch):
    from agent.vendors.anthropic_router import _cache_enabled
    monkeypatch.delenv("MUSUBI_PROMPT_CACHE", raising=False)
    assert _cache_enabled() is True
    monkeypatch.setenv("MUSUBI_PROMPT_CACHE", "0")
    assert _cache_enabled() is False
