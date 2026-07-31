"""Compact root goal state and terminal worker feedback projection."""

from __future__ import annotations

from agent.textfmt import TRUNCATION_MARK
from agent.goal_state import GoalState, OutcomePacket, root_decision_tools
from agent.routes import RouteKind
from agent.scope import classify_task


def test_decision_block_surfaces_planning_artifact_paths() -> None:
    state = GoalState.create(
        "a simple front end page", "medium_change", "planner_then_coder_check",
    )
    state.planning_artifacts = (
        ".musubi/goals/abc/plan.md",
        ".musubi/goals/abc/manifest.json",
    )
    block = state.render_decision_block()
    assert "planning_artifacts=.musubi/goals/abc/plan.md" in block
    assert ".musubi/goals/abc/manifest.json" in block
    assert "pass both files to the next worker" in block.lower()


def test_decision_block_surfaces_conversation_cost_and_stall() -> None:
    # The per-turn budget resets on every chat message, so without these
    # numbers the root cannot tell turn six from turn one.
    state = GoalState.create(
        "build the page", "medium_change", "planner_then_coder_check",
    )
    state.chat_turns = 6
    state.chat_tokens = 109_494
    state.chat_barren_turns = 3

    block = state.render_decision_block()
    assert "conversation_usage=turns:6,tokens:109494,turns_without_a_file:3" in block
    assert "conversation_warning=" in block
    assert "do not spawn another planner" in block.lower()


def test_decision_block_omits_conversation_lines_on_a_fresh_chat() -> None:
    state = GoalState.create(
        "build the page", "medium_change", "planner_then_coder_check",
    )

    block = state.render_decision_block()
    assert "conversation_usage=" not in block
    assert "conversation_warning=" not in block


def test_the_root_surface_widens_only_on_a_planners_verdict() -> None:
    # The advisory branch that withheld the whole catalog is gone with the
    # regex that produced it. What remains is an inversion worth pinning: a
    # turn whose size nobody has established gets the LEAN surface, and the
    # skill-reading tools arrive only once `assess_manifest` calls the
    # planner's declaration medium or large.
    tools = [
        {"name": name}
        for name in (
            "musubi_spawn_subagent",
            "musubi_recommend_skills",
            "musubi_get_skill",
            "musubi_get_reference",
        )
    ]

    unknown = GoalState.create("anything", "unknown", RouteKind.ROOT_DECIDES)
    lean = {t["name"] for t in root_decision_tools(tools, unknown)}
    assert lean == {"musubi_spawn_subagent", "musubi_recommend_skills"}

    medium = GoalState.create("anything", "medium_change", RouteKind.PLANNER_THEN_CODER_CHECK)
    wide = {t["name"] for t in root_decision_tools(tools, medium)}
    assert "musubi_get_skill" in wide and "musubi_get_reference" in wide


def test_outcome_packet_projects_worker_contract() -> None:
    packet = OutcomePacket.from_worker(
        role="coder",
        status="done",
        summary=(
            "status: done\n"
            "summary: created dashboard\n"
            "verification: valid HTML\n"
            "remaining_gap: none"
        ),
        touched_files={"artifacts/nyc.html"},
    )

    assert packet.status == "done"
    assert packet.summary == "created dashboard"
    assert packet.verification == "valid HTML"
    assert packet.remaining_gap is None
    assert packet.touched_files == ("artifacts/nyc.html",)


def test_outcome_packet_bounds_unstructured_fallback() -> None:
    packet = OutcomePacket.from_worker(
        role="planner",
        status="done",
        summary="x" * 5000,
        touched_files=(),
    )

    assert len(packet.summary) <= 800
    assert TRUNCATION_MARK in packet.summary
    assert packet.summary.endswith(" chars]")


def test_goal_state_keeps_exact_intent_and_root_only_usage() -> None:
    state = GoalState.create(
        "create NYC dashboard", "simple_artifact", "single_coder",
    )
    state.record_root_usage(tokens_in=1200, tokens_out=100)
    state.record_outcome(
        role="coder",
        status="done",
        summary="summary: ready",
        touched_files={"nyc.html"},
    )

    block = state.render_decision_block()

    assert "intent=create NYC dashboard" in block
    assert "root_usage=calls:1,input:1200,output:100,target:3000" in block
    assert "coder (done)" in block


def test_simple_root_surface_is_spawn_only() -> None:
    tools = [
        {"name": name}
        for name in (
            "musubi_spawn_subagent",
            "musubi_read_file",
            "musubi_get_skill",
        )
    ]
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )

    assert [tool["name"] for tool in root_decision_tools(tools, state)] == [
        "musubi_spawn_subagent",
    ]


def test_non_simple_root_surface_keeps_spawn_and_skill_tools() -> None:
    tools = [
        {"name": name}
        for name in (
            "musubi_read_file",
            "musubi_spawn_subagent",
            "musubi_recommend_skills",
            "musubi_get_skill",
            "musubi_get_reference",
        )
    ]
    state = GoalState.create(
        "add authentication", "medium_change", "planner_then_coder_check",
    )

    assert [tool["name"] for tool in root_decision_tools(tools, state)] == [
        "musubi_spawn_subagent",
        "musubi_recommend_skills",
        "musubi_get_skill",
        "musubi_get_reference",
    ]


def test_recovery_offers_only_the_decision_it_exists_to_make() -> None:
    # Recovery is a DECISION phase: a worker failed, and the root chooses
    # whether to replace it. Handing over the read tools inverted that — the
    # root went investigating itself (a grep across 392 files, two reads, a
    # retrieve), burned both analysis cycles, and halted without ever spawning
    # a replacement.
    tools = [
        {"name": "musubi_read_file"},
        {"name": "musubi_spawn_subagent"},
        {"name": "musubi_recommend_skills"},
    ]
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )

    assert [t["name"] for t in root_decision_tools(
        tools, state, recovery_outcome=True,
    )] == ["musubi_spawn_subagent", "musubi_recommend_skills"]
    assert root_decision_tools(
        tools, state, recovery_outcome=True, decision_only=True,
    ) == [{"name": "musubi_spawn_subagent"}]


def test_spawn_exhausted_root_surface_offers_no_tools() -> None:
    # Once the worker ceiling is spent, offering the spawn tool only lets the
    # root burn cycles on refused spawns; withhold every tool so it must
    # conclude from the evidence it has.
    tools = [
        {"name": name}
        for name in (
            "musubi_spawn_subagent",
            "musubi_recommend_skills",
            "musubi_get_skill",
        )
    ]
    state = GoalState.create(
        "add authentication", "medium_change", "planner_then_coder_check",
    )

    assert root_decision_tools(tools, state, spawn_exhausted=True) == []


def test_active_failure_recovery_outranks_spawn_exhaustion() -> None:
    # A pending worker failure keeps the spawn affordance even when the worker
    # ceiling is spent — the ceiling-driven halt is handled by the loop's
    # recovery path, not by starving the root mid-decision. What recovery does
    # NOT restore is the read surface.
    tools = [
        {"name": "musubi_read_file"},
        {"name": "musubi_spawn_subagent"},
    ]
    state = GoalState.create(
        "create dashboard", "simple_artifact", "single_coder",
    )

    assert root_decision_tools(
        tools, state, recovery_outcome=True, spawn_exhausted=True,
    ) == [{"name": "musubi_spawn_subagent"}]


# ── planner manifest reclassification ────────────────────────────────────────

ELEVEN_FILE_MANIFEST = (
    '<change_manifest>{"files_expected":11,"subsystems":'
    '["config","routes","components","styles"],"public_contract":false,'
    '"data_migration":false,"security_sensitive":false,'
    '"external_side_effects":false,"destructive":false,"blocking_decisions":[],'
    '"validation_commands":2}</change_manifest>'
)


def test_medium_goal_requires_planner_before_coder() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    assert state.next_role == "planner"


def test_simple_goal_has_no_role_order_constraint() -> None:
    state = GoalState.create("create page", "simple_artifact", "single_coder")
    assert state.next_role is None


def test_a_turn_starts_with_no_assessment_at_all() -> None:
    # `assess_request` used to hand a ChangeAssessment to every turn before any
    # model ran. Bands over one sentence are exactly the judgment this track
    # removed; the only assessment now comes from `apply_planner_manifest`,
    # after a planner has read code.
    hint = classify_task("Add authentication to the app")
    state = GoalState.create(
        "Add authentication to the app", hint.kind.value, hint.route,
        assessment=hint.assessment,
    )

    assert hint.assessment is None
    assert state.assessment is None
    assert state.next_role is None, "no role order is owed before a manifest"


def test_eleven_file_manifest_reclassifies_goal_as_large() -> None:
    state = GoalState.create("create site", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(ELEVEN_FILE_MANIFEST)
    assert state.scope == "large_feature"
    assert state.route == "plan_design_workflow"
    # Large is a chain, not a halt: the remaining work owes a design and an
    # independent review before it is done.
    assert state.next_role == "designer"
    assert state.role_chain == ("coder", "reviewer")


def test_small_manifest_opens_the_coder_gate() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(
        'status: done\n'
        '<change_manifest>{"files_expected":1,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"blocking_decisions":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert state.next_role == "coder"
    assert state.pending_clarification is None
    block = state.render_decision_block()
    assert "next_role=coder" in block
    assert "assessment=" in block


def test_missing_manifest_fails_closed_to_clarification() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest("status: done\nsummary: did some planning")
    assert state.route == "ask_scope"
    assert state.next_role is None
    assert state.pending_clarification is not None
    assert "change manifest" in state.pending_clarification


def test_manifest_blocking_decisions_set_pending_clarification() -> None:
    state = GoalState.create("add route", "medium_change", "planner_then_coder_check")
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":3,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"blocking_decisions":["deployment target"],'
        '"validation_commands":1}</change_manifest>'
    )
    assert state.route == "ask_scope"
    assert state.pending_clarification is not None
    assert "deployment target" in state.pending_clarification


# ── typed recovery decisions ─────────────────────────────────────────────────

from agent.run import FailureKind, RecoveryAction, WorkerOutcome, decide_recovery  # noqa: E402


def _turn_cap_outcome(*, files: tuple[str, ...]) -> WorkerOutcome:
    return WorkerOutcome(
        role="coder", status="escalated", summary="unfinished scaffold",
        touched_files=files, brief="create the scaffold",
        failure_kind=FailureKind.TURN_CAP,
    )


def test_first_turn_cap_with_files_auto_replaces() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=("app/page.tsx",)),
        same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.AUTO_REPLACE


def test_repeated_turn_cap_halts_instead_of_looping() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=("app/page.tsx",)),
        same_role_failures=2, worker_slots=1,
    ) is RecoveryAction.HALT


def test_turn_cap_without_files_needs_root_analysis() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=()),
        same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.ROOT_ANALYZE


def test_exhausted_worker_slots_halt() -> None:
    assert decide_recovery(
        _turn_cap_outcome(files=("app/page.tsx",)),
        same_role_failures=1, worker_slots=0,
    ) is RecoveryAction.HALT


def test_budget_and_policy_failures_halt_fail_closed() -> None:
    for kind in (FailureKind.BUDGET, FailureKind.POLICY):
        outcome = WorkerOutcome(
            role="coder", status="escalated", summary="stopped",
            touched_files=("app/page.tsx",), brief="b", failure_kind=kind,
        )
        assert decide_recovery(
            outcome, same_role_failures=1, worker_slots=1,
        ) is RecoveryAction.HALT


def test_blocked_failure_routes_to_root_analysis() -> None:
    outcome = WorkerOutcome(
        role="coder", status="escalated", summary="[blocked] too large",
        touched_files=("app/page.tsx",), brief="b",
        failure_kind=FailureKind.BLOCKED,
    )
    assert decide_recovery(
        outcome, same_role_failures=1, worker_slots=1,
    ) is RecoveryAction.ROOT_ANALYZE


def test_manifest_overrun_is_detected_and_surfaced() -> None:
    # With the lexical risk gates gone the manifest is the ONLY input to
    # routing, so a declaration nobody checks is trusted rather than governed.
    # Declare one file, clear the cheap route, then touch three.
    state = GoalState.create(
        "add the page", "medium_change", "planner_then_coder_check",
    )
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["markup"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"blocking_decisions":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert state.declared_files_expected == 1
    assert state.manifest_overrun() is None

    state.record_outcome(
        role="coder",
        status="done",
        summary="summary: built it",
        touched_files={"a.html", "b.css", "c.js"},
    )

    assert state.manifest_overrun() == (1, 3)
    block = state.render_decision_block()
    assert "manifest_overrun=declared:1,touched:3" in block
    assert "do not widen it further" in block.lower()


def test_no_overrun_within_the_declared_radius() -> None:
    state = GoalState.create(
        "add the page", "medium_change", "planner_then_coder_check",
    )
    state.apply_planner_manifest(
        '<change_manifest>{"files_expected":3,"subsystems":["markup"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"blocking_decisions":[],"validation_commands":1}'
        '</change_manifest>'
    )
    state.record_outcome(
        role="coder",
        status="done",
        summary="summary: built it",
        touched_files={"a.html", "b.css"},
    )

    assert state.manifest_overrun() is None
    assert "manifest_overrun=" not in state.render_decision_block()


def test_the_decision_block_shows_bands_only_once_a_manifest_exists() -> None:
    # Two components used to decide the route from the same sentence and
    # disagreed on every sensitive request, putting contradictory orders into
    # one prompt. There is only one classifier left, and it runs after a
    # planner reads code — so there is nothing left to contradict.
    task = "make a payments dashboard"
    hint = classify_task(task)
    state = GoalState.create(
        intent=task, scope=hint.kind.value, route=hint.route,
        assessment=hint.assessment,
    )

    block = state.render_decision_block()
    assert "ambiguity=" not in block
    assert "impact=" not in block


def _blind_goal() -> GoalState:
    """A turn where nothing establishes what is being changed."""
    return GoalState.create("make it faster", "simple_artifact", "single_coder")


def test_a_turn_that_establishes_nothing_refuses_a_mutation_worker() -> None:
    gap = _blind_goal().evidence_gap()

    assert gap is not None
    # The refusal must be actionable: naming the two roles that can supply what
    # is missing is what lets the root fix this without asking the user.
    assert "explorer" in gap and "planner" in gap


def test_a_named_target_is_enough() -> None:
    state = _blind_goal()
    state.target_named = True

    assert state.evidence_gap() is None


def test_an_explorer_report_is_enough() -> None:
    state = _blind_goal()
    state.record_outcome(
        role="explorer", status="done", summary="summary: found src/app.py",
        touched_files=set(),
    )

    assert state.evidence_gap() is None


def test_an_accepted_manifest_is_not_a_target() -> None:
    # PR #166 review. `ChangeManifest` carries files_expected, subsystems, and
    # flags — counts and labels, no paths. A planner may legally declare
    # `files_expected=0` with no subsystems, so treating "a manifest exists" as
    # target evidence cleared the gate while identifying nothing. Size is not a
    # location.
    state = _blind_goal()
    state.declared_files_expected = 2

    assert state.evidence_gap() is not None


def test_a_planners_own_outcome_is_evidence() -> None:
    # What the planner establishes is that it READ the workspace, which its
    # `done` outcome already records. Keying on the outcome also survives the
    # second hole: `apply_planner_manifest` only runs when next_role ==
    # "planner", so on a single_coder route — exactly where this gate fires —
    # a planner spawned to clear it never set declared_files_expected at all,
    # and the refusal's own advice could not be followed.
    state = _blind_goal()
    state.record_outcome(
        role="planner", status="done", summary="summary: 1 file, agent/run.py",
        touched_files=set(),
    )

    assert state.evidence_gap() is None


def test_only_a_finished_worker_counts() -> None:
    # `escalated` (out of cycles) and `abandoned` (cascade-killed) carry no
    # findings, and a "not failed" test let both open the mutation gate on the
    # strength of having been spawned.
    for status in ("escalated", "abandoned", "failed", "error"):
        state = _blind_goal()
        state.record_outcome(
            role="explorer", status=status, summary="summary: ran out",
            touched_files=set(),
        )
        assert state.evidence_gap() is not None, status


def test_a_coders_report_does_not_establish_the_target() -> None:
    # "Something was written" is a different claim from "the target was found".
    # If a coder's own outcome cleared the gate, one blind spawn would unlock
    # every spawn after it.
    state = _blind_goal()
    state.record_outcome(
        role="coder", status="done", summary="summary: wrote a file",
        touched_files={"guess.txt"},
    )

    assert state.evidence_gap() is not None


def test_a_failed_explorer_establishes_nothing() -> None:
    state = _blind_goal()
    state.record_outcome(
        role="explorer", status="failed", summary="summary: could not read it",
        touched_files=set(),
    )

    assert state.evidence_gap() is not None


def test_the_gate_covers_writers_only() -> None:
    # Refusing a read-only worker for lack of evidence would refuse the very
    # thing that supplies it — a deadlock, not a gate.
    from agent.goal_state import EVIDENCE_ROLES, MUTATION_ROLES

    assert MUTATION_ROLES == {"coder", "designer"}
    assert not (MUTATION_ROLES & EVIDENCE_ROLES)


# ── plan step 5: an exceeded declaration stops the next writer ──────────────


def _planned_goal(declared: int) -> GoalState:
    state = GoalState.create("widen the thing", "medium_change", "planner_then_coder_check")
    state.target_named = True
    state.declared_files_expected = declared
    return state


def test_a_declaration_within_its_radius_stops_nothing() -> None:
    state = _planned_goal(3)
    state.record_outcome(
        role="coder", status="done", summary="summary: done",
        touched_files={"a.py", "b.py"},
    )

    assert state.overrun_stop() is None


def test_an_exceeded_declaration_refuses_the_next_writer() -> None:
    # With the lexical risk gates gone the manifest is the SOLE input to
    # routing, so a declaration nobody enforces is trusted rather than
    # governed: declare one file, clear the cheap route, touch eleven.
    state = _planned_goal(1)
    state.record_outcome(
        role="coder", status="done", summary="summary: done",
        touched_files={"a.py", "b.py", "c.py"},
    )

    stop = state.overrun_stop()
    assert stop is not None
    assert "declared 1 file(s)" in stop and "touched 3" in stop
    # Actionable, like every other gate here: it names what can still be done.
    assert "planner" in stop


def test_the_stop_is_not_terminal() -> None:
    # Making it fatal would throw away completed work to punish a declaration.
    # What it forbids is one thing: another writer on the same radius.
    state = _planned_goal(1)
    state.record_outcome(
        role="coder", status="done", summary="summary: done",
        touched_files={"a.py", "b.py"},
    )

    assert state.overrun_stop() is not None
    # Re-declaring clears it, which is what the message tells the root to do.
    state.declared_files_expected = 5
    assert state.overrun_stop() is None
