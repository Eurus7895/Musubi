"""Parallel worker dispatch (Increment 4).

Proves that several workers spawned in one model turn run CONCURRENTLY, that
their results come back paired to the right tool_use in input order, and that
the per-role width guard refuses overflow spawns.

Concurrency is proven with a `threading.Barrier`: each worker's (blocking)
`vendor.call` runs in a thread via `asyncio.to_thread`, and every worker must
reach the barrier before any is released. If dispatch serialized, the barrier
would time out and surface as an error instead of a clean summary.
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
from pathlib import Path
from typing import Any

from agent.run import Orchestration, _spawn_overflow_reasons, run_agent
from agent.scope import ScopeHint, ScopeKind
from agent.vendors.base import LMResponse, LMRouter


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


def _spawns(n: int, role: str = "explorer") -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[
        {
            "type": "tool_use", "id": f"spawn-{i}", "name": "musubi_spawn_subagent",
            "input": {"role": role, "brief": f"worker {i}"},
        }
        for i in range(n)
    ])


def _is_parent_followup(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), str)
        and "[root-goal-state]" in m["content"]
        for m in messages
    )


def _child_index(messages: list[dict[str, Any]]) -> int | None:
    for m in messages:
        c = m.get("content")
        if isinstance(c, str) and "## Brief" in c:
            hit = re.search(r"worker (\d+)", c)
            return int(hit.group(1)) if hit else -1
    return None


class BarrierRouter(LMRouter):
    """Serves parent + N children; children rendezvous on a barrier so the test
    fails unless they truly overlap."""

    name = "barrier"
    model = "barrier-1"

    def __init__(self, n: int) -> None:
        self.n = n
        self.barrier = threading.Barrier(n, timeout=8)
        self.completed: set[int] = set()

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        names = {tool["name"] for tool in tools}
        if _is_parent_followup(messages):
            return _text("done")
        idx = _child_index(messages)
        if idx is not None:
            self.barrier.wait()  # blocks until all N children are here
            self.completed.add(idx)
            return _text(f"explored worker {idx}")
        if "musubi_begin_direct" in names:
            return LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use",
                "id": "mode-direct",
                "name": "musubi_begin_direct",
                "input": {
                    "target_intent": "modify",
                    "target_path": ".",
                    "worker_role": "explorer",
                },
            }])
        return _spawns(self.n)


def test_parallel_workers_run_concurrently_then_compact_root_followup() -> None:
    n = 2
    router = BarrierRouter(n)
    answer = asyncio.run(run_agent("delegate a scan", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    # Both children reached the barrier and completed. The root sees a bounded
    # goal-state delta instead of replaying their raw tool_result blocks.
    assert router.completed == {0, 1}
    followup = str(_last_followup(router))
    assert "[root-goal-state]" in followup
    assert "subagent error" not in followup.lower()


# Capture the messages of the parent's final LM call for the assertion above.
_LAST: dict[str, list] = {}


def _last_followup(router: BarrierRouter) -> list[dict[str, Any]]:
    return _LAST.get("messages", [])


_orig_call = BarrierRouter.call


def _capturing_call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
    if _is_parent_followup(messages):
        _LAST["messages"] = messages
    return _orig_call(self, messages, tools, max_tokens=max_tokens)


BarrierRouter.call = _capturing_call  # type: ignore[method-assign]


def test_per_role_width_cap_refuses_overflow_spawns() -> None:
    # Nested workers have no root scope cap. The flat per-role width still
    # refuses the fourth and fifth same-role calls in one batch.
    calls = _spawns(5, role="explorer").content
    refused = _spawn_overflow_reasons(
        calls,
        io.StringIO(),
        role="coder",
        scope_hint=None,
        orchestration=Orchestration(
            parent_session_id="root-session",
            parent_agent_name="coder",
            depth=1,
        ),
    )

    assert set(refused) == {"spawn-3", "spawn-4"}


def test_scope_does_not_cap_workers_across_model_cycles() -> None:
    orchestration = Orchestration(parent_session_id="root-session")
    scope = ScopeHint(
        kind=ScopeKind.SIMPLE_ARTIFACT,
        route="single_coder",
        reason="one artifact",
    )
    first = [{
        "type": "tool_use",
        "id": "spawn-first",
        "name": "musubi_spawn_subagent",
        "input": {"role": "coder", "brief": "build it"},
    }]
    second = [{
        "type": "tool_use",
        "id": "spawn-second",
        "name": "musubi_spawn_subagent",
        "input": {"role": "coder", "brief": "retry it"},
    }]

    assert _spawn_overflow_reasons(
        first,
        io.StringIO(),
        role="agent",
        scope_hint=scope,
        orchestration=orchestration,
    ) == {}
    refused = _spawn_overflow_reasons(
        second,
        io.StringIO(),
        role="agent",
        scope_hint=scope,
        orchestration=orchestration,
    )

    assert refused == {}


def test_generic_root_worker_ceiling_is_cumulative() -> None:
    orchestration = Orchestration(parent_session_id="root-session")
    scope = ScopeHint(
        kind=ScopeKind.SIMPLE_ARTIFACT,
        route="single_coder",
        reason="one artifact",
    )

    for index in range(3):
        refused = _spawn_overflow_reasons(
            [{
                "type": "tool_use",
                "id": f"spawn-{index}",
                "name": "musubi_spawn_subagent",
                "input": {"role": "coder", "brief": "build it"},
            }],
            io.StringIO(),
            role="agent",
            scope_hint=scope,
            orchestration=orchestration,
        )
        assert refused == {}

    refused = _spawn_overflow_reasons(
        [{
            "type": "tool_use",
            "id": "spawn-fourth",
            "name": "musubi_spawn_subagent",
            "input": {"role": "coder", "brief": "retry it"},
        }],
        io.StringIO(),
        role="agent",
        scope_hint=scope,
        orchestration=orchestration,
    )

    assert refused == {"spawn-fourth": "root worker ceiling (3) reached"}


class SequentialRetryRouter(LMRouter):
    name = "sequential-retry"
    model = "sequential-retry-1"

    def __init__(self) -> None:
        self.children = 0
        self.replacement_context_seen = False

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        names = {tool["name"] for tool in tools}
        idx = _child_index(messages)
        if idx is not None:
            self.children += 1
            self.replacement_context_seen = any(
                isinstance(message.get("content"), str)
                and "[worker-replacement]" in message["content"]
                for message in messages
            )
            if self.children == 1:
                return _text("[incomplete] first worker could not finish")
            return _text("worker finished")
        if "musubi_begin_direct" in names:
            return LMResponse(stop_reason="tool_use", content=[{
                "type": "tool_use",
                "id": "mode-direct",
                "name": "musubi_begin_direct",
                "input": {
                    "target_intent": "create",
                    "target_path": "dashboard.html",
                    "worker_role": "coder",
                },
            }])
        if self.children < 2:
            return _spawns(1, role="coder")
        return _text("done")


def test_single_coder_route_allows_sequential_replacement_worker() -> None:
    router = SequentialRetryRouter()
    log = io.StringIO()

    answer = asyncio.run(
        run_agent("create a dashboard.html", router, _musubi_dir(), log=log)
    )

    assert answer == "done"
    assert router.children == 2
    assert router.replacement_context_seen is True
    assert "root worker ceiling" not in log.getvalue()
