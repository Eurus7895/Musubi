"""Compact root-owned goal state and worker feedback projection.

musubi-tier: substrate
expires-when: never - root must retain user intent while worker scaffolding
  changes independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

SIMPLE_ROOT_TOKEN_TARGET = 3_000
DEFAULT_ROOT_TOKEN_TARGET = 8_000
MAX_SUMMARY_CHARS = 800
MAX_DETAIL_CHARS = 400

_FIELD_RE = re.compile(
    r"(?im)^\s*(status|summary|verification|remaining_gap)\s*:\s*(.*?)\s*$"
)
_SIMPLE_SCOPES = frozenset({"simple_edit", "simple_artifact"})
_SPAWN_TOOL = "musubi_spawn_subagent"
# Skill selection is available to the root in EVERY scope, including simple
# artifacts: the root ranks the catalog with `musubi_recommend_skills` and
# passes the chosen `pushed_skill_id` into the spawn (option 3). This is one
# cheap tool definition — it returns ids + titles, not skill bodies — so it
# does not blow the simple-scope root-token target.
_SKILL_SELECT_TOOL = "musubi_recommend_skills"
# The content-loading skill tools stay gated to broader scopes, where the root
# may itself read a skill. A simple-scope root never needs to pull full skill
# text into its own context — it delegates the reading to the worker it pushes
# the skill to.
_SKILL_READ_TOOLS = frozenset({
    "musubi_get_skill",
    "musubi_get_reference",
})


def _bounded(value: str, limit: int) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    suffix = "… [truncated]"
    return compact[: limit - len(suffix)].rstrip() + suffix


def _fields(text: str) -> dict[str, str]:
    return {
        match.group(1).lower(): match.group(2)
        for match in _FIELD_RE.finditer(text)
    }


@dataclass(frozen=True)
class OutcomePacket:
    """Bounded terminal worker evidence shown to the root controller."""

    role: str
    status: str
    summary: str
    touched_files: tuple[str, ...] = ()
    verification: str | None = None
    remaining_gap: str | None = None

    @classmethod
    def from_worker(
        cls,
        *,
        role: str,
        status: str,
        summary: str,
        touched_files: Iterable[str],
    ) -> "OutcomePacket":
        parsed = _fields(summary)
        verification = (
            _bounded(parsed.get("verification", ""), MAX_DETAIL_CHARS) or None
        )
        gap = _bounded(parsed.get("remaining_gap", ""), MAX_DETAIL_CHARS) or None
        if gap is not None and gap.lower() in {"none", "n/a", "no", "nothing"}:
            gap = None
        return cls(
            role=role,
            status=status,
            summary=_bounded(
                parsed.get("summary", summary), MAX_SUMMARY_CHARS,
            ),
            touched_files=tuple(sorted(set(touched_files))),
            verification=verification,
            remaining_gap=gap,
        )


@dataclass
class GoalState:
    """Current-run control state owned by the root, never by a child worker."""

    intent: str
    scope: str
    route: str
    root_token_target: int
    root_calls: int = 0
    root_tokens_in: int = 0
    root_tokens_out: int = 0
    outcomes: list[OutcomePacket] = field(default_factory=list)

    @classmethod
    def create(cls, intent: str, scope: str, route: str) -> "GoalState":
        target = (
            SIMPLE_ROOT_TOKEN_TARGET
            if scope in _SIMPLE_SCOPES
            else DEFAULT_ROOT_TOKEN_TARGET
        )
        return cls(
            intent=intent,
            scope=scope,
            route=route,
            root_token_target=target,
        )

    def record_root_usage(self, *, tokens_in: int, tokens_out: int) -> None:
        self.root_calls += 1
        self.root_tokens_in += max(0, int(tokens_in))
        self.root_tokens_out += max(0, int(tokens_out))

    def record_outcome(self, **kwargs: Any) -> OutcomePacket:
        packet = OutcomePacket.from_worker(**kwargs)
        self.outcomes.append(packet)
        return packet

    def render_decision_block(self) -> str:
        latest = self.outcomes[-1] if self.outcomes else None
        worker = "none"
        if latest is not None:
            files = ", ".join(latest.touched_files) or "none"
            worker = (
                f"{latest.role} ({latest.status}); files={files}; "
                f"summary={latest.summary}; "
                f"verification={latest.verification or 'none'}; "
                f"remaining_gap={latest.remaining_gap or 'none'}"
            )
        return (
            "[root-goal-state]\n"
            f"intent={self.intent}\n"
            f"scope={self.scope}\n"
            f"route={self.route}\n"
            f"root_usage=calls:{self.root_calls},input:{self.root_tokens_in},"
            f"output:{self.root_tokens_out},target:{self.root_token_target}\n"
            f"latest_worker={worker}\n"
            "decision=Compare the latest evidence with the original intent. "
            "Stop if the goal is satisfied; otherwise summon only the cheapest "
            "worker needed for the remaining gap.\n"
            "[/root-goal-state]"
        )


def root_decision_tools(
    tools: list[dict[str, Any]],
    state: GoalState,
    *,
    recovery_outcome: bool = False,
    decision_only: bool = False,
    spawn_exhausted: bool = False,
) -> list[dict[str, Any]]:
    """Return the model-visible root tools for the current decision phase."""
    if recovery_outcome and not decision_only:
        return list(tools)
    if spawn_exhausted:
        # The root has spent its worker budget: every further `musubi_spawn_*`
        # is refused by the ceiling gate, so offering the spawn tool only lets
        # the model burn the rest of its cycle budget on refused spawns before
        # the loop salvages a placeholder. Withhold every tool so the root is
        # forced to conclude from the worker evidence it already has, ending the
        # turn within one cycle instead of spinning to the cycle cap.
        return []
    # Spawn plus skill *selection* in every scope, so the root can push a
    # skill to the worker it summons even for a simple artifact.
    allowed = {_SPAWN_TOOL, _SKILL_SELECT_TOOL}
    if state.scope not in _SIMPLE_SCOPES:
        allowed.update(_SKILL_READ_TOOLS)
    return [tool for tool in tools if tool.get("name") in allowed]
