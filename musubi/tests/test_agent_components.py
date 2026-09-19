"""Behavior and import boundaries for the incremental component extraction."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent.collector import collect_context
from agent.context import ContextBudgetExceededError
from agent.decider import interpret_response
from agent.thinker import _call_with_effort, _cycle_token_usage
from agent.vendors.base import LMResponse


def test_context_identity_tracks_tool_permissions_and_input() -> None:
    messages = [{"role": "user", "content": "Inspect the failure"}]
    first = collect_context(messages, [], budget_chars=2000, compression_db_path=None)
    changed = collect_context(
        messages, [{"name": "read_logs"}], budget_chars=2000,
        compression_db_path=None,
    )
    repeated = collect_context(messages, [], budget_chars=2000, compression_db_path=None)
    assert first.input_hash == repeated.input_hash
    assert first.input_hash != changed.input_hash
    assert messages == [{"role": "user", "content": "Inspect the failure"}]


def test_collector_cannot_hide_oversized_tool_schema() -> None:
    with pytest.raises(ContextBudgetExceededError):
        collect_context(
            [{"role": "user", "content": "goal"}],
            [{"name": "tool", "description": "x" * 5000}],
            budget_chars=1000, compression_db_path=None,
        )


def test_decider_drops_markup_without_executing_or_losing_tool_proposal() -> None:
    action = {"type": "tool_use", "id": "call-1", "name": "read_logs", "input": {}}
    result = interpret_response(LMResponse(
        stop_reason="tool_use",
        content=[{"type": "text", "text": "<tool_call>bad prose</tool_call>"}, action],
    ))
    assert result.discarded_markup
    assert result.text == ""
    assert result.tool_uses == [action]
    # A proposal carries no permission or completed-goal verdict.
    assert not hasattr(result, "verified")


def test_thinker_accounts_for_every_attempt_including_truncation() -> None:
    class Router:
        def __init__(self) -> None:
            self.caps: list[int] = []

        def call(self, messages, tools, *, max_tokens):
            self.caps.append(max_tokens)
            return LMResponse(
                stop_reason="max_tokens" if len(self.caps) == 1 else "end_turn",
                content=[{"type": "text", "text": "candidate"}],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    router = Router()
    result = _call_with_effort(router, [], [], floor=16, ceiling=32)
    usage = _cycle_token_usage(result.attempts, input_estimate=999)
    assert router.caps == [16, 32]
    assert (usage.tokens_in, usage.tokens_out) == (20, 10)


@pytest.mark.parametrize("name", [
    "collector", "thinker", "decider", "run_state", "runtime_tools", "adaptive_control",
])
def test_components_do_not_import_the_entrypoint_or_concrete_provider(name: str) -> None:
    path = Path(__file__).parents[1] / "agent" / f"{name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    forbidden = ("agent.run", "openai", "anthropic", "typesafe")
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imports for prefix in forbidden
    ), imports


def test_compatibility_exports_share_one_implementation() -> None:
    from agent import adaptive_control, decider, run, run_state, runtime_tools, thinker

    assert run.Orchestration is run_state.Orchestration
    assert run.decide_recovery is decider.decide_recovery
    assert run._call_with_effort is thinker._call_with_effort
    assert run._call_tool_text is runtime_tools._call_tool_text
    assert run._handle_root_control_tool is adaptive_control._handle_root_control_tool
