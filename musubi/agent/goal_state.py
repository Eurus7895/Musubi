"""Compact root-owned goal state and worker feedback projection.

musubi-tier: substrate
expires-when: never - root must retain user intent while worker scaffolding
  changes independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from agent.manifest import (
    Band,
    ChangeAssessment,
    assess_manifest,
    parse_change_manifest,
)
from agent.routes import RouteKind
from agent.textfmt import bounded as _bounded

SIMPLE_ROOT_TOKEN_TARGET = 3_000
DEFAULT_ROOT_TOKEN_TARGET = 8_000
MAX_SUMMARY_CHARS = 800
MAX_DETAIL_CHARS = 400

_FIELD_RE = re.compile(
    r"(?im)^\s*(status|summary|verification|remaining_gap)\s*:\s*(.*?)\s*$"
)
#: Sizes at which the root may want to read full skill text into its OWN
#: context rather than pushing a skill to the worker it summons. Only
#: `assess_manifest` produces these, and only after a planner has read code —
#: so the wider surface is now bought with evidence rather than guessed from
#: the request. Every turn starts `unknown` and therefore lean, which is the
#: conservative direction: the previous code widened by default and narrowed on
#: a lexical hunch.
_WIDE_SCOPES = frozenset({"medium_change", "large_feature"})
#: Trailing barren turns before the root is told to stop planning. Three is
#: the point at which the traced conversation had already spent two planner
#: round trips and two question walls without a single file on disk.
NO_PROGRESS_TURN_THRESHOLD = 3
#: What a large change actually requires, in order, once the planner's
#: manifest establishes the blast radius. "Large" means MORE REVIEW, not a
#: different launcher: the root may already spawn each of these roles ad-hoc
#: (`MAIN_SUBAGENT_ALLOWLIST["agent"]`), so it runs the chain itself. Locked
#: decision #4 forbids spawning an entire *pipeline*, which this never does.
LARGE_ROLE_CHAIN: tuple[str, ...] = ("designer", "coder", "reviewer")
#: Roles whose order the goal state enforces. Anything else the root summons
#: (an explorer for workspace facts, an investigator for a failure) is free.
ORDERED_ROLES: frozenset[str] = frozenset(
    {"planner", "designer", "coder", "reviewer"}
)
#: Roles that WRITE. The sufficiency gate applies to these and nothing else:
#: refusing a read-only worker for lack of evidence would refuse the very thing
#: that supplies it.
MUTATION_ROLES: frozenset[str] = frozenset({"coder", "designer"})
#: Roles whose report establishes a fact about the workspace. A coder's report
#: says something was written, which is a different claim from "the target was
#: found", so it does not clear the gate. `planner` is here because a planner
#: reads the workspace before it can commit to a blast radius — its OUTCOME is
#: the evidence, not its manifest (see `evidence_gap`).
EVIDENCE_ROLES: frozenset[str] = frozenset(
    {"explorer", "investigator", "finder", "planner"}
)
#: The only status that establishes anything. An allowlist, not a denylist:
#: workers also finish `escalated` (hit the turn cap) and `abandoned`
#: (cascade-killed), and both were passing a "not failed" test while carrying
#: no findings at all — an explorer that ran out of cycles was opening the
#: mutation gate on the strength of having been spawned.
_SUCCEEDED: str = "done"
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
    #: Conversation-scoped cost, loaded from `agent_turns` at turn start. The
    #: per-turn budget is process-scoped and resets on every chat message, so
    #: these are the only numbers that can see a multi-turn spend loop.
    chat_turns: int = 0
    chat_tokens: int = 0
    #: Trailing count of prior turns that ended without writing a file.
    chat_barren_turns: int = 0
    #: Driver-owned files produced from the planner response. They are planning
    #: inputs, not delivered user artifacts, and therefore never reset the
    #: no-progress counter.
    planning_artifacts: tuple[str, ...] = ()
    #: Roles still owed after `next_role`, in order. Non-empty only on a large
    #: change, where the chain is the whole point of the classification.
    role_chain: tuple[str, ...] = ()
    #: `files_expected` from the accepted manifest. Kept so the declaration can
    #: be CHECKED against what a later worker actually touched — with the
    #: lexical risk gates gone, the manifest is the sole input to routing, and
    #: a declaration nobody verifies is trusted rather than governed.
    declared_files_expected: int | None = None
    #: Did the REQUEST name a path inside the workspace? From the evidence
    #: vector at turn start (`agent/evidence.py`). Static for the turn — the
    #: user's sentence does not change while the turn runs — which is why it is
    #: stored rather than recomputed. The other two inputs to the sufficiency
    #: gate below are live, and read from this object each time it is asked.
    target_named: bool = False

    @classmethod
    def create(
        cls,
        intent: str,
        scope: str,
        route: str,
        assessment: ChangeAssessment | None = None,
    ) -> "GoalState":
        # Same inversion as the tool surface: lean until a manifest says
        # otherwise. A turn whose size nobody has established yet gets the
        # smaller target, which is the direction that fails cheap.
        target = (
            DEFAULT_ROOT_TOKEN_TARGET
            if scope in _WIDE_SCOPES
            else SIMPLE_ROOT_TOKEN_TARGET
        )
        return cls(
            intent=intent,
            scope=scope,
            route=route,
            root_token_target=target,
            assessment=assessment,
            # Medium routes are planner-led: the coder gate opens only after
            # the planner's manifest reclassifies the blast radius.
            next_role="planner" if route == RouteKind.PLANNER_THEN_CODER_CHECK else None,
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
                Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, RouteKind.ASK_SCOPE,
                ("missing-or-invalid-change-manifest",),
                "The planner could not produce a valid change manifest. "
                "Which files or deliverables should this change include?",
            )
        )
        self.assessment = assessment
        self.route = assessment.route
        self.scope = {
            RouteKind.SINGLE_CODER: "simple_artifact",
            RouteKind.PLANNER_THEN_CODER_CHECK: "medium_change",
            RouteKind.PLAN_DESIGN_WORKFLOW: "large_feature",
            RouteKind.ASK_SCOPE: "unknown",
        }[assessment.route]
        if assessment.route == RouteKind.PLAN_DESIGN_WORKFLOW:
            # A large change is not a refusal. It means the remaining work owes
            # a design and an independent review before it is done, so the root
            # runs that chain with the roles it is already allowed to spawn.
            self.next_role = LARGE_ROLE_CHAIN[0]
            self.role_chain = LARGE_ROLE_CHAIN[1:]
        else:
            self.next_role = (
                "coder"
                if assessment.route
                in {RouteKind.SINGLE_CODER, RouteKind.PLANNER_THEN_CODER_CHECK}
                else None
            )
            self.role_chain = ()
        self.pending_clarification = assessment.clarifying_question
        self.declared_files_expected = (
            manifest.files_expected if manifest is not None else None
        )
        return assessment

    def overrun_stop(self) -> str | None:
        """Why no further writer may be summoned, or None.

        `manifest_overrun` has always computed this; until now its only
        consequence was a paragraph in the decision block, which the model was
        free to read and continue past. With the lexical risk gates gone the
        manifest is the SOLE input to routing, so a declaration nobody enforces
        is trusted rather than governed: declare one file, clear the cheap
        route, touch eleven, and nothing stops the twelfth.

        Deliberately not terminal. The run keeps whatever it has already
        written and the root may still report, re-plan, or ask — what it may
        not do is summon another writer on a radius that has already been
        exceeded. Making it fatal would throw away completed work to punish a
        declaration, and the append-only stage store exists precisely so a
        wrong attempt can be superseded rather than lost.
        """
        breach = self.manifest_overrun()
        if breach is None:
            return None
        declared, actual = breach
        return (
            f"the change has outgrown its plan: the manifest declared "
            f"{declared} file(s) and workers have touched {actual}. No further "
            f"writer may be summoned on this radius — report what was touched, "
            f"or spawn 'planner' to re-declare the remainder"
        )

    def evidence_gap(self) -> str | None:
        """Why a mutation worker may not be summoned yet, or None.

        The enforceable core of "collect enough information first". Two ways to
        know what a turn targets, and a mutation worker needs one:

        1. **the request named it** — a path resolving inside the workspace
           root, established at turn start by `agent/evidence.py`;
        2. **somebody looked** — a read-only worker (explorer, investigator,
           finder, or planner) came back `done`.

        With neither, a coder is being sent at a guess. That is the traced
        failure in its expensive form: the cheap version asked the same
        question three times and spent nothing, while this version spawns a
        worker that writes files nobody asked for, in a place nobody named.

        **An accepted manifest is deliberately NOT one of the ways.** It was,
        and that was wrong: `ChangeManifest` carries `files_expected`,
        `subsystems`, and a handful of flags — counts and labels, no paths. A
        planner can legally declare `files_expected=0` with no subsystems, and
        that manifest would have cleared this gate while identifying nothing.
        What the planner establishes is that it READ the workspace, which its
        `done` outcome already records; the manifest is a size, and size is not
        a location. Keying on the outcome rather than the manifest also fixes a
        second hole: `apply_planner_manifest` only runs when
        `next_role == "planner"`, so on a `single_coder` route — exactly where
        this gate fires — a planner spawned to clear it never set
        `declared_files_expected` at all, and the refusal's own advice could
        not be followed.

        Only `done` counts. Workers also finish `escalated` (out of cycles) and
        `abandoned` (cascade-killed); both carry no findings, and both were
        clearing this gate on the strength of having been spawned.

        Fail-closed and cheap to satisfy: the refusal names the roles that can
        supply what is missing. Read fresh at every spawn, because (2) becomes
        true DURING a turn — the whole point is that the root can fix this
        itself rather than returning to the user.
        """
        if self.target_named:
            return None
        if any(
            outcome.role in EVIDENCE_ROLES and outcome.status == _SUCCEEDED
            for outcome in self.outcomes
        ):
            return None
        return (
            "nothing establishes what this turn targets: the request names no "
            "path inside the workspace, and no read-only worker has come back "
            "with findings. Spawn 'explorer' (or 'planner', which reads before "
            "it plans) and retry once it reports"
        )

    def reject_planning_artifacts(self, reason: str) -> ChangeAssessment:
        """Fail closed when the planner did not produce the two-file contract."""
        assessment = ChangeAssessment(
            Band.HIGH,
            Band.UNKNOWN,
            Band.UNKNOWN,
            RouteKind.ASK_SCOPE,
            ("missing-or-invalid-planning-artifacts",),
            reason,
        )
        self.assessment = assessment
        self.route = assessment.route
        self.scope = "unknown"
        self.next_role = None
        self.role_chain = ()
        self.pending_clarification = reason
        self.planning_artifacts = ()
        self.declared_files_expected = None
        return assessment

    def manifest_overrun(self) -> tuple[int, int] | None:
        """`(declared, actual)` when mutation exceeded the declared radius.

        With the lexical risk gates removed, the manifest is the only input to
        routing, so an unverified declaration would be *trusted* rather than
        governed: a worker could declare one file, clear the cheap route, and
        then touch eleven. This compares the declaration against the files
        workers actually reported touching. Returns None while the change is
        within its declared radius, or when no manifest was accepted.
        """
        if self.declared_files_expected is None:
            return None
        touched: set[str] = set()
        for outcome in self.outcomes:
            touched.update(outcome.touched_files)
        if len(touched) > self.declared_files_expected:
            return self.declared_files_expected, len(touched)
        return None

    def record_root_usage(self, *, tokens_in: int, tokens_out: int) -> None:
        self.root_calls += 1
        self.root_tokens_in += max(0, int(tokens_in))
        self.root_tokens_out += max(0, int(tokens_out))

    def record_outcome(self, **kwargs: Any) -> OutcomePacket:
        packet = OutcomePacket.from_worker(**kwargs)
        self.outcomes.append(packet)
        # Advance the ordered chain only on a SUCCESSFUL run of the role that
        # was owed. A failed designer must not open the coder gate — the
        # recovery path gets its one same-role replacement first.
        #
        # `planner` is excluded on purpose: the caller runs this BEFORE
        # `apply_planner_manifest`, and that method owns the transition out of
        # planning. Advancing here would clear `next_role` and the manifest
        # would never be read.
        if (
            packet.role != "planner"
            and packet.role == self.next_role
            and packet.status == "done"
        ):
            self.next_role = self.role_chain[0] if self.role_chain else None
            self.role_chain = self.role_chain[1:]
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
            remaining = (
                " then " + " → ".join(self.role_chain) if self.role_chain else ""
            )
            order = f"next_role={self.next_role}{remaining}\n"
        # Bands only — NOT `assessment.route`. Two components decide the route
        # from the same sentence, and on every sensitive request they disagree:
        # `assess_request` reads "make a payments dashboard" as a bounded
        # artifact (single_coder) while `classify_task` withholds the shortcut
        # (planner_then_coder_check). Rendering both put two contradictory
        # orders in one prompt. `self.route` above is the one that governs, so
        # it is the only one the model is shown; the bands still carry what the
        # assessment actually knows.
        bands = ""
        if self.assessment is not None:
            bands = (
                f"assessment=ambiguity:{self.assessment.ambiguity.value},"
                f"impact:{self.assessment.impact.value},"
                f"risk:{self.assessment.risk.value}\n"
            )
        conversation = ""
        if self.chat_turns:
            conversation = (
                f"conversation_usage=turns:{self.chat_turns},"
                f"tokens:{self.chat_tokens},"
                f"turns_without_a_file:{self.chat_barren_turns}\n"
            )
        stall = ""
        if self.chat_barren_turns >= NO_PROGRESS_TURN_THRESHOLD:
            stall = (
                f"conversation_warning=The last {self.chat_barren_turns} turns "
                "of this conversation ended without writing a file. More "
                "planning is not the gap. Either summon a worker that "
                "produces the artifact, or ask ONE question — do not spawn "
                "another planner.\n"
            )
        overrun = ""
        breach = self.manifest_overrun()
        if breach is not None:
            declared, actual = breach
            overrun = (
                f"manifest_overrun=declared:{declared},touched:{actual}\n"
                "The change outgrew the radius its plan was routed on. Do not "
                "widen it further: stop and report what was touched, or "
                "re-plan the remainder explicitly.\n"
            )
        planning = ""
        if self.planning_artifacts:
            planning = (
                "planning_artifacts="
                + ",".join(self.planning_artifacts)
                + "\nPass both files to the next worker. plan.md is the "
                "implementation contract; manifest.json is the bounded "
                "governance declaration.\n"
            )
        return (
            "[root-goal-state]\n"
            f"intent={self.intent}\n"
            f"scope={self.scope}\n"
            f"route={self.route}\n"
            f"{order}{bands}{conversation}{stall}{overrun}{planning}"
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
    if recovery_outcome:
        # Recovery is a DECISION phase, not a work phase: a worker failed and
        # the root has to choose whether to replace it. Handing over the whole
        # catalog here inverted that — the root took the read tools and went
        # investigating itself (a `grep` across 392 files, two reads, a
        # retrieve), spent both analysis cycles, and halted with
        # `_recovery_incomplete` having never spawned a replacement. The only
        # affordance is the decision it exists to make; once the analysis
        # cycles are spent, `decision_only` narrows that further to the spawn
        # itself.
        allowed_recovery = (
            {_SPAWN_TOOL} if decision_only
            else {_SPAWN_TOOL, _SKILL_SELECT_TOOL}
        )
        return [
            tool for tool in tools if tool.get("name") in allowed_recovery
        ]
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
    if state.scope in _WIDE_SCOPES:
        allowed.update(_SKILL_READ_TOOLS)
    return [tool for tool in tools if tool.get("name") in allowed]
