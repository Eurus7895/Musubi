"""Token budgeting for the standalone agent host.

musubi-tier: substrate
expires-when: never - token controls belong at the LM-call boundary, not
  in a provider-specific UI.
"""

from __future__ import annotations

import math
from typing import Literal

BudgetStatus = Literal["allow", "warn", "halt"]


def estimate_tokens_from_chars(chars: int) -> int:
    """Char-to-token approximation shared with the extension runner."""
    if chars <= 0:
        return 0
    return math.ceil(chars / 4)


class TokenBudgetEnforcer:
    """Running token accountant for one CLI turn."""

    def __init__(self, max_tokens: int, warn_at_ratio: float = 0.8) -> None:
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError(
                f"TokenBudgetEnforcer: max_tokens must be positive, got {max_tokens}"
            )
        if warn_at_ratio <= 0 or warn_at_ratio > 1:
            raise ValueError(
                "TokenBudgetEnforcer: warn_at_ratio must be in (0, 1], "
                f"got {warn_at_ratio}"
            )
        self.max_tokens = max_tokens
        self.warn_at_ratio = float(warn_at_ratio)
        self._tokens_used = 0
        self._warned = False

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._tokens_used)

    @property
    def warned(self) -> bool:
        return self._warned

    def preflight(self, estimated_tokens: int) -> BudgetStatus:
        projected = self._tokens_used + max(0, estimated_tokens)
        if projected > self.max_tokens:
            return "halt"
        if projected >= self.max_tokens * self.warn_at_ratio and not self._warned:
            return "warn"
        return "allow"

    def charge(self, actual_tokens: int) -> BudgetStatus:
        if not isinstance(actual_tokens, int) or actual_tokens < 0:
            raise ValueError(
                "TokenBudgetEnforcer.charge: actual_tokens must be non-negative, "
                f"got {actual_tokens}"
            )
        self._tokens_used += actual_tokens
        if self._tokens_used > self.max_tokens:
            return "halt"
        if self._tokens_used >= self.max_tokens * self.warn_at_ratio and not self._warned:
            self._warned = True
            return "warn"
        return "allow"


_STATUS_RANK: dict[BudgetStatus, int] = {"allow": 0, "warn": 1, "halt": 2}


def _stricter(a: BudgetStatus, b: BudgetStatus) -> BudgetStatus:
    """The more restrictive of two budget statuses (halt > warn > allow)."""
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


class ChildTokenBudget:
    """A per-stage sub-budget that charges the shared parent run budget too.

    A pipeline stage may spend at most its own `max_tokens` allowance AND may
    never push the parent run over its cap — every check returns the stricter
    of the two. `charge` debits the child and the parent exactly once, so the
    tokens a stage spends are reflected in the parent's `remaining`, and a
    later stage's `pipeline_stage_allowance` shrinks accordingly. This is how an
    early planner/designer loop is prevented from consuming coder/reviewer's
    share: its allowance caps it, and whatever it does spend is charged through.

    Duck-compatible with `TokenBudgetEnforcer` where the agent loop uses a
    budget: `preflight`, `charge`, `tokens_used`, `remaining`, `max_tokens`,
    `warn_at_ratio`, `warned`.
    """

    def __init__(
        self,
        parent: TokenBudgetEnforcer | ChildTokenBudget,
        max_tokens: int,
        warn_at_ratio: float = 0.8,
    ) -> None:
        self._parent = parent
        self._local = TokenBudgetEnforcer(max_tokens, warn_at_ratio)

    @property
    def max_tokens(self) -> int:
        return self._local.max_tokens

    @property
    def warn_at_ratio(self) -> float:
        return self._local.warn_at_ratio

    @property
    def tokens_used(self) -> int:
        return self._local.tokens_used

    @property
    def remaining(self) -> int:
        """Whatever is left under the stricter of the child and parent caps."""
        return min(self._local.remaining, self._parent.remaining)

    @property
    def warned(self) -> bool:
        return self._local.warned or self._parent.warned

    def preflight(self, estimated_tokens: int) -> BudgetStatus:
        return _stricter(
            self._local.preflight(estimated_tokens),
            self._parent.preflight(estimated_tokens),
        )

    def charge(self, actual_tokens: int) -> BudgetStatus:
        # Charge both exactly once; the reported status is the stricter one.
        local_status = self._local.charge(actual_tokens)
        parent_status = self._parent.charge(actual_tokens)
        return _stricter(local_status, parent_status)


def pipeline_stage_allowance(
    parent: TokenBudgetEnforcer | ChildTokenBudget,
    stages_remaining: int,
) -> int:
    """Fair-share token allowance for the next stage, reserving the rest.

    Splits the parent's *current* remaining evenly across the stages still to
    run, so no single stage can spend more than its share and starve those
    after it. Recomputed per stage against the live remaining, so a stage that
    underspends hands its slack to later stages, and one that overspends cannot
    (its allowance already capped it). Always at least 1 while budget remains.
    """
    if stages_remaining <= 0:
        raise ValueError("stages_remaining must be positive")
    remaining = parent.remaining
    fair_share = remaining // stages_remaining
    return max(1, min(remaining, fair_share))


class TokenBudgetExhaustedError(RuntimeError):
    """Raised when a token budget check halts a turn."""

    def __init__(
        self,
        *,
        phase: Literal["preflight", "postflight"],
        tokens_used: int,
        max_tokens: int,
        this_call_tokens: int,
    ) -> None:
        self.phase = phase
        self.tokens_used = tokens_used
        self.max_tokens = max_tokens
        self.this_call_tokens = this_call_tokens
        super().__init__(
            f"agent token budget exhausted at {phase}: {tokens_used} of "
            f"{max_tokens} tokens used after a {this_call_tokens}-token call"
        )
