"""Driver model generation and per-call effort accounting.

musubi-tier: substrate
expires-when: never - explicit driver and execution boundaries
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.budget import estimate_tokens_from_chars
from agent.vendors.base import LMResponse, LMRouter


@dataclass
class EffortCallResult:
    """Final response plus every vendor call made to obtain it."""

    response: LMResponse
    attempts: list[LMResponse]


@dataclass(frozen=True)
class CycleTokenUsage:
    """Normalized provider usage for one logical loop cycle."""

    tokens_in: int
    cached_input_tokens: int
    tokens_out: int
    source: str


def _call_with_effort(
    vendor: LMRouter,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    floor: int,
    ceiling: int,
) -> EffortCallResult:
    """Effort routing: start at a low output-token cap, escalate only on need.

    Most cycles emit a small tool_use block, so the floor cap costs nothing
    they needed. If a call truncates (`stop_reason == "max_tokens"`), re-issue
    the same request once at the ceiling so a real answer is never cut off.
    """
    resp = vendor.call(messages, tools, max_tokens=floor)
    attempts = [resp]
    if resp.stop_reason == "max_tokens" and floor < ceiling:
        resp = vendor.call(messages, tools, max_tokens=ceiling)
        attempts.append(resp)
    return EffortCallResult(response=resp, attempts=attempts)


def _cycle_token_usage(
    responses: LMResponse | list[LMResponse],
    input_estimate: int,
) -> CycleTokenUsage:
    attempts = responses if isinstance(responses, list) else [responses]
    totals = [
        _single_response_token_usage(resp, input_estimate)
        for resp in attempts
    ]
    return CycleTokenUsage(
        tokens_in=sum(item.tokens_in for item in totals),
        cached_input_tokens=sum(item.cached_input_tokens for item in totals),
        tokens_out=sum(item.tokens_out for item in totals),
        source=(
            "provider"
            if all(item.source == "provider" for item in totals)
            else "estimated"
        ),
    )


def _single_response_token_usage(
    resp: LMResponse,
    input_estimate: int,
) -> CycleTokenUsage:
    usage = resp.usage or {}
    provider_input = _usage_int(usage, "input_tokens", "prompt_tokens")
    tokens_in = provider_input if provider_input is not None else input_estimate
    output_estimate = estimate_tokens_from_chars(
        len(json.dumps(resp.content, default=str, ensure_ascii=False))
    )
    provider_output = _usage_int(usage, "output_tokens", "completion_tokens")
    tokens_out = provider_output if provider_output is not None else output_estimate
    cached = (
        _usage_int(usage, "cache_read_input_tokens", "cached_input_tokens")
        or _nested_usage_int(usage, ("prompt_tokens_details", "cached_tokens"))
        or 0
    )
    return CycleTokenUsage(
        tokens_in=max(0, tokens_in),
        cached_input_tokens=max(0, min(cached, tokens_in)),
        tokens_out=max(0, tokens_out),
        source=(
            "provider"
            if provider_input is not None and provider_output is not None
            else "estimated"
        ),
    )


def _usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None


def _nested_usage_int(usage: dict[str, Any], path: tuple[str, str]) -> int | None:
    value = usage.get(path[0])
    if not isinstance(value, dict):
        return None
    nested = value.get(path[1])
    if isinstance(nested, int):
        return nested
    if isinstance(nested, float):
        return int(nested)
    return None

