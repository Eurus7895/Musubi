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

from agent.run import run_agent
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
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
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

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        if _is_parent_followup(messages):
            return _text("done")
        idx = _child_index(messages)
        if idx is not None:
            self.barrier.wait()  # blocks until all N children are here
            return _text(f"explored worker {idx}")
        return _spawns(self.n)


def test_parallel_workers_run_concurrently_and_results_are_ordered() -> None:
    n = 3
    router = BarrierRouter(n)
    answer = asyncio.run(run_agent("delegate a scan", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    # The parent's follow-up turn (the LM call that returned "done") must carry
    # one tool_result per spawn, paired by id in input order.
    followups = [
        m for m in _last_followup(router) if isinstance(m.get("content"), list)
    ]
    results = [b for m in followups for b in m["content"] if b.get("type") == "tool_result"]
    assert len(results) == n
    for i, b in enumerate(results):
        assert b["tool_use_id"] == f"spawn-{i}"          # order preserved
        assert f"worker {i}" in b["content"]             # right worker's summary
        assert "error" not in b["content"].lower()        # barrier never broke


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


class CountingRouter(LMRouter):
    """No barrier — counts how many children actually ran, for the width cap."""

    name = "counting"
    model = "counting-1"

    def __init__(self, n: int, role: str) -> None:
        self.n = n
        self.role = role
        self._lock = threading.Lock()
        self.children = 0

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        if _is_parent_followup(messages):
            return _text("done")
        idx = _child_index(messages)
        if idx is not None:
            with self._lock:
                self.children += 1
            return _text(f"explored worker {idx}")
        return _spawns(self.n, role=self.role)


def test_per_role_width_cap_refuses_overflow_spawns() -> None:
    # 5 spawns of the same role in one turn; cap is 3 → only 3 children run,
    # 2 are refused without running a loop.
    router = CountingRouter(5, role="explorer")
    answer = asyncio.run(run_agent("fan out wide", router, _musubi_dir(), log=io.StringIO()))
    assert answer == "done"
    assert router.children == 3, f"expected 3 workers to run, got {router.children}"
