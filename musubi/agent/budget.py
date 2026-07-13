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
