"""Standalone agent token accounting."""

from __future__ import annotations

import pytest

from agent import budget as budget_module
from agent.budget import (
    ChildTokenBudget,
    TokenBudgetEnforcer,
    TokenBudgetExhaustedError,
    estimate_tokens_from_chars,
    pipeline_stage_allowance,
    root_worker_allowance,
)


def test_budget_module_exports_token_accounting_only() -> None:
    assert not hasattr(budget_module, "RATES")
    assert not hasattr(budget_module, "estimate_call_credits")
    assert not hasattr(budget_module, "BudgetEnforcer")
    assert not hasattr(budget_module, "BudgetExhaustedError")


def test_estimate_tokens_from_chars_uses_four_char_ceil() -> None:
    assert estimate_tokens_from_chars(0) == 0
    assert estimate_tokens_from_chars(4) == 1
    assert estimate_tokens_from_chars(5) == 2


def test_token_budget_enforcer_warns_once_then_halts() -> None:
    budget = TokenBudgetEnforcer(max_tokens=1000, warn_at_ratio=0.8)
    assert budget.preflight(799) == "allow"
    assert budget.preflight(800) == "warn"
    assert budget.charge(700) == "allow"
    assert budget.charge(100) == "warn"
    assert budget.charge(50) == "allow"
    assert budget.charge(151) == "halt"
    assert budget.tokens_used == 1001


def test_token_budget_exhausted_error_carries_context() -> None:
    err = TokenBudgetExhaustedError(
        phase="postflight",
        tokens_used=1201,
        max_tokens=1000,
        this_call_tokens=250,
    )
    assert err.phase == "postflight"
    assert err.tokens_used == 1201
    assert err.max_tokens == 1000
    assert err.this_call_tokens == 250
    assert "token budget" in str(err)


def test_token_budget_enforcer_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        TokenBudgetEnforcer(0)
    with pytest.raises(ValueError):
        TokenBudgetEnforcer(10, warn_at_ratio=0)
    with pytest.raises(ValueError):
        TokenBudgetEnforcer(10).charge(-1)


# ── ChildTokenBudget: per-stage allowance charged through to the parent ─────


def test_child_token_budget_charges_child_and_parent_once() -> None:
    parent = TokenBudgetEnforcer(200_000)
    child = ChildTokenBudget(parent, 50_000)
    assert child.max_tokens == 50_000
    assert child.preflight(30_000) == "allow"
    assert child.charge(30_000) == "allow"
    # The spend is reflected in BOTH the child and the shared parent.
    assert child.tokens_used == 30_000
    assert parent.tokens_used == 30_000
    # 30k + 25k = 55k > the 50k child allowance → halt, even though the parent
    # run still has plenty of room.
    assert child.preflight(25_000) == "halt"


def test_child_token_budget_is_stricter_of_parent() -> None:
    parent = TokenBudgetEnforcer(40_000)
    parent.charge(30_000)  # parent nearly exhausted
    # Child allowance is larger than the parent's remaining 10k.
    child = ChildTokenBudget(parent, 50_000)
    # The child alone would allow 20k, but the parent caps it → halt.
    assert child.preflight(20_000) == "halt"
    assert child.remaining == 10_000  # min(child 50k, parent 10k)


def test_pipeline_stage_allowance_reserves_later_stage_shares() -> None:
    parent = TokenBudgetEnforcer(200_000)
    # Four stages remaining: each gets an even quarter of the live remaining.
    a_plan = pipeline_stage_allowance(parent, 4)
    assert a_plan == 50_000
    plan_budget = ChildTokenBudget(parent, a_plan)
    plan_budget.charge(a_plan)  # planner burns its whole share

    a_design = pipeline_stage_allowance(parent, 3)
    assert a_design == 50_000  # remaining 150k // 3
    design_budget = ChildTokenBudget(parent, a_design)
    design_budget.charge(a_design)  # designer burns its whole share

    # Coder + reviewer still hold a positive, unspent reserve — the early
    # stages could NOT reach into it.
    assert parent.remaining == 100_000
    assert pipeline_stage_allowance(parent, 2) == 50_000


def test_pipeline_stage_allowance_rejects_nonpositive_remaining() -> None:
    parent = TokenBudgetEnforcer(1_000)
    with pytest.raises(ValueError):
        pipeline_stage_allowance(parent, 0)


def test_pipeline_stage_allowance_never_returns_zero_while_budget_remains() -> None:
    parent = TokenBudgetEnforcer(1_000)
    parent.charge(999)
    # 1 token left across 4 stages → floor of 1, never 0.
    assert pipeline_stage_allowance(parent, 4) == 1


# ── No-progress budget breaker ──────────────────────────────────────────────


def _orch_with(outcomes):
    from agent.run import Orchestration, WorkerOutcome
    orch = Orchestration(parent_session_id="root")
    for role, status, files in outcomes:
        orch.worker_outcomes.append(
            WorkerOutcome(role=role, status=status, summary="s", touched_files=files)
        )
    return orch


def test_no_progress_trip_fires_on_failures_with_high_spend() -> None:
    from agent.run import _no_progress_budget_trip
    budget = TokenBudgetEnforcer(max_tokens=1000)
    budget.charge(800)  # 80% spent
    orch = _orch_with([("coder", "escalated", ("weather.html",))])
    trip = _no_progress_budget_trip(budget, orch)
    assert trip is not None
    assert "stopped early" in trip


def test_no_progress_trip_silent_below_ratio() -> None:
    from agent.run import _no_progress_budget_trip
    budget = TokenBudgetEnforcer(max_tokens=1000)
    budget.charge(500)  # 50% < 70%
    orch = _orch_with([("coder", "escalated", ())])
    assert _no_progress_budget_trip(budget, orch) is None


def test_no_progress_trip_silent_when_artifact_delivered() -> None:
    """A worker that completed done WITH files means real progress — never trip
    even at high spend."""
    from agent.run import _no_progress_budget_trip
    budget = TokenBudgetEnforcer(max_tokens=1000)
    budget.charge(900)
    orch = _orch_with([
        ("coder", "escalated", ()),
        ("coder", "done", ("weather.html",)),
    ])
    assert _no_progress_budget_trip(budget, orch) is None


def test_no_progress_trip_silent_without_any_failure() -> None:
    """No failed/escalated worker yet → nothing to abort even at high spend
    (a single long-running worker hasn't returned)."""
    from agent.run import _no_progress_budget_trip
    budget = TokenBudgetEnforcer(max_tokens=1000)
    budget.charge(900)
    assert _no_progress_budget_trip(budget, _orch_with([])) is None


def test_no_progress_trip_done_without_files_does_not_count() -> None:
    """A planner completing done (no files) is not artifact delivery; a run
    that then only escalates should still trip."""
    from agent.run import _no_progress_budget_trip
    budget = TokenBudgetEnforcer(max_tokens=1000)
    budget.charge(800)
    orch = _orch_with([
        ("planner", "done", ()),
        ("coder", "escalated", ("weather.html",)),
    ])
    assert _no_progress_budget_trip(budget, orch) is not None


def test_no_progress_trip_fires_for_three_preworker_plan_failures() -> None:
    from agent.goal_state import GoalState
    from agent.routes import RouteKind
    from agent.run import _no_progress_budget_trip

    budget = TokenBudgetEnforcer(max_tokens=1000)
    budget.charge(800)
    state = GoalState.create("add export", "unknown", RouteKind.ROOT_DECIDES)
    state.begin_plan()
    for _ in range(3):
        state.record_planning_contract_failure("invalid_change_manifest")
    orch = _orch_with([])
    orch.goal_state = state

    trip = _no_progress_budget_trip(budget, orch)
    assert trip is not None
    assert "three consecutive planning-contract failures" in trip


# ── a direct worker gets a slice too, not the whole run ─────────────────────


def test_root_worker_allowance_reserves_a_recovery_share() -> None:
    """The traced budget failure. A direct worker was handed the parent
    enforcer itself: one coder charged 200,580 of a 200,000-token run across
    eight cycles while the root had spent 9,685, and when it failed there was
    nothing left to continue with."""
    run = TokenBudgetEnforcer(200_000)
    run.charge(9_685)  # the root's first three cycles, from the trace

    # Three worker slots, this one included: a third, not the remainder.
    first = root_worker_allowance(run, 3)
    assert first == 63_438
    worker = ChildTokenBudget(run, first)
    worker.charge(first)

    # What the failed run did not have: a reserve the worker could not reach.
    assert run.remaining == 126_877
    assert root_worker_allowance(run, 2) == 63_438


def test_root_worker_allowance_halts_the_worker_not_the_run() -> None:
    """The share is a cap on the worker, and the parent still sees the spend —
    so a worker that overruns stops itself while the root stays alive."""
    run = TokenBudgetEnforcer(100_000)
    worker = ChildTokenBudget(run, root_worker_allowance(run, 3))

    assert worker.charge(33_333) == "halt" or worker.remaining == 0
    assert run.remaining > 0  # the root can still spawn a replacement


def test_worker_budget_wraps_only_when_there_is_a_ceiling_to_divide_by() -> None:
    from agent.subagent import _worker_budget

    class _Orch:
        max_root_workers = 3
        spawned_workers = 1  # incremented before dispatch: counts this worker

    run = TokenBudgetEnforcer(200_000)
    wrapped = _worker_budget(run, _Orch())
    assert isinstance(wrapped, ChildTokenBudget)
    assert wrapped.max_tokens == root_worker_allowance(run, 3)

    # No orchestration means no worker ceiling and no root continuation to
    # reserve for; the budget passes through rather than being invented.
    assert _worker_budget(run, None) is run
    assert _worker_budget(None, _Orch()) is None
