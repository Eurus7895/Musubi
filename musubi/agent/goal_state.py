"""Compact root-owned goal state and worker feedback projection.

musubi-tier: substrate
expires-when: never - root must retain user intent while worker scaffolding
  changes independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from agent.change_assessment import (
    Band,
    ChangeAssessment,
    assess_manifest,
    parse_change_manifest,
)

SIMPLE_ROOT_TOKEN_TARGET = 3_000
DEFAULT_ROOT_TOKEN_TARGET = 8_000
MAX_SUMMARY_CHARS = 800
MAX_DETAIL_CHARS = 400

_FIELD_RE = re.compile(
    r"(?im)^\s*(status|summary|verification|remaining_gap)\s*:\s*(.*?)\s*$"
)
_SIMPLE_SCOPES = frozenset({"inspect", "simple_edit", "simple_artifact"})
#: The consultative route (`agent/scope.py`): the user asked to be advised,
#: not for a change. The root is the whole answer, so its decision phase gets
#: no tools at all.
ADVISORY_ROUTE = "advisory"
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
    #: Post-plan reassessment from the planner's bounded change manifest —
    #: None until `apply_planner_manifest` runs. Its route supersedes the
    #: lexical route for every later root decision.
    assessment: ChangeAssessment | None = None
    #: The only role the root may summon into mutation next. "planner" on a
    #: medium route until a manifest lands; "coder" once the manifest clears
    #: it; None when no deterministic order applies (or the route left the
    #: direct-worker path entirely).
    next_role: str | None = None
    #: One deterministic question the driver must return to the user before
    #: any further model call (set when the manifest routes to ask_scope).
    pending_clarification: str | None = None

    @classmethod
    def create(
        cls,
        intent: str,
        scope: str,
        route: str,
        assessment: ChangeAssessment | None = None,
    ) -> "GoalState":
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
            assessment=assessment,
            # Medium routes are planner-led: the coder gate opens only after
            # the planner's manifest reclassifies the blast radius.
            next_role="planner" if route == "planner_then_coder_check" else None,
        )

    def apply_planner_manifest(self, text: str) -> ChangeAssessment:
        """Reclassify this goal from the planner's bounded change manifest.

        A missing or invalid manifest fails CLOSED to one clarification — the
        planner could not commit to a blast radius, so no mutation role is
        legal until the user answers. The manifest verdict overwrites the
        lexical scope/route so an "11 files, 4 subsystems" plan can never
        proceed as a medium change.
        """
        manifest = parse_change_manifest(text)
        assessment = (
            assess_manifest(manifest)
            if manifest is not None
            else ChangeAssessment(
                Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, "ask_scope",
                ("missing-or-invalid-change-manifest",),
                "The planner could not produce a valid change manifest. "
                "Which files or deliverables should this change include?",
            )
        )
        self.assessment = assessment
        self.route = assessment.route
        self.scope = {
            "single_coder": "simple_artifact",
            "planner_then_coder_check": "medium_change",
            "plan_design_workflow": "large_feature",
            "ask_scope": "unknown",
        }[assessment.route]
        self.next_role = (
            "coder"
            if assessment.route in {"single_coder", "planner_then_coder_check"}
            else None
        )
        self.pending_clarification = assessment.clarifying_question
        return assessment

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
        order = ""
        if self.next_role is not None:
            order = f"next_role={self.next_role}\n"
        bands = ""
        if self.assessment is not None:
            bands = (
                f"assessment=ambiguity:{self.assessment.ambiguity.value},"
                f"impact:{self.assessment.impact.value},"
                f"risk:{self.assessment.risk.value},"
                f"route:{self.assessment.route}\n"
            )
        return (
            "[root-goal-state]\n"
            f"intent={self.intent}\n"
            f"scope={self.scope}\n"
            f"route={self.route}\n"
            f"{order}{bands}"
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
    if state.route == ADVISORY_ROUTE:
        # Checked before every other phase: an advisory turn must never reach
        # a tool. No worker can add evidence to "which auth model should I
        # pick?" — it names no file to read — so a spawn buys a multi-cycle
        # round trip that ends in a change manifest the user never asked for.
        # Withholding the catalog forces the root to answer in ONE cycle.
        return []
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
