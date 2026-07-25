"""Scope-aware routing hints for the standalone root agent."""

from __future__ import annotations

from agent.change_assessment import Band, assess_request
from agent.scope import ScopeKind, classify_task


def test_consultative_question_routes_to_advisory_without_workers() -> None:
    # A request to be ADVISED carries no deliverable, so it must not fall
    # through to the mutation catch-all and summon a planner.
    for task in (
        "explain each",
        "choose the best for me",
        "which one is better",
        "what is the difference",
        "compare these approaches",
        "should i use a managed provider",
        "recommend an approach",
    ):
        hint = classify_task(task)
        assert hint.kind is ScopeKind.ADVISORY, task
        assert hint.route == "advisory", task
        assert hint.requires == (), task

    block = classify_task("explain each").prompt_block().lower()
    assert "answer directly" in block
    assert "do not spawn a worker" in block


def test_advisory_beats_critical_risk_gate_for_a_pure_question() -> None:
    # "which auth provider should I choose?" is a question ABOUT auth, not a
    # change TO auth. The critical-risk gate must not force a
    # plan/design/review workflow onto a request that mutates nothing.
    hint = classify_task("which auth provider should i choose")

    assert hint.kind is ScopeKind.ADVISORY
    assert hint.route == "advisory"


def test_bare_follow_up_is_advisory_only_with_conversation_history() -> None:
    # `classify_task` sees ONE message, so a bare noun carries no signal and
    # falls to the mutation catch-all. With prior turns on record it reads as
    # conversation instead — the traced "Okta" turn cost a 96s planner round
    # trip to answer a question that named no file.
    for task in ("Okta", "skill?", "these are complicated", "the second one"):
        assert classify_task(task, has_history=True).route == "advisory", task
        # Without history the classification is unchanged from before.
        assert classify_task(task).route != "advisory", task


def test_follow_up_inheritance_only_moves_toward_the_cheaper_route() -> None:
    # A follow-up carrying real work must keep its own classification: history
    # may never be used to escalate, only to answer more cheaply.
    for task in (
        "add auth to the app",
        "fix the login bug",
        "delete all *-dashboard.html files",
        "open C:\\Workspace\\Musubi",
        "a simple front end page first, prepare a file for the plan",
    ):
        with_history = classify_task(task, has_history=True)
        assert with_history.route != "advisory", task
        assert with_history.route == classify_task(task).route, task


def test_advisory_never_swallows_a_mutation_or_a_path_question() -> None:
    # Three exclusions keep the advisory branch narrow. A mutation verb, a
    # diagnostic, or any concrete path target all disqualify it — notably
    # "explain <file>" needs a worker that actually reads the file.
    for task in (
        "compare the two configs and update the stale one",
        "explain why the build is failing",
        "explain musubi/agent/run.py",
        "explain the codebase",
        "choose a name and rename the module",
    ):
        hint = classify_task(task)
        assert hint.kind is not ScopeKind.ADVISORY, task
        assert hint.route != "advisory", task


def test_reach_to_path_routes_to_read_only_single_explorer() -> None:
    hint = classify_task(
        r"could you reach to C:\Workspace\09_CD_Team\21_A2lPatcher\a2l-patcher-stla"
    )

    assert hint.kind is ScopeKind.INSPECT
    assert hint.route == "single_explorer"
    assert "read-only" in hint.reason
    block = hint.prompt_block().lower()
    assert "explorer" in block and "do not spawn a planner or coder" in block


def test_read_file_is_inspection_not_a_change() -> None:
    # The filename `run.py` embeds the mutation verb "run"; it must not be read
    # as intent to change anything.
    hint = classify_task("read run.py")

    assert hint.kind is ScopeKind.INSPECT
    assert hint.route == "single_explorer"


def test_list_and_show_directory_requests_are_inspection() -> None:
    for task in (
        "open the src folder",
        "show me what is in ./musubi/agent",
        "list the files in the tests directory",
        "look at the auth module",
    ):
        hint = classify_task(task)
        assert hint.kind is ScopeKind.INSPECT, task
        assert hint.route == "single_explorer", task


def test_explicit_edit_is_not_intercepted_by_inspection_route() -> None:
    # A read-only verb next to a mutation verb ("find and replace ... in run.py")
    # stays a change, not an inspection.
    hint = classify_task("find and replace TODO in run.py")

    assert hint.kind is ScopeKind.SIMPLE_EDIT
    assert hint.route == "single_coder"


def test_bare_intent_without_a_path_is_not_inspection() -> None:
    # No concrete path/dir target, so "open a PR" must not route to an explorer.
    hint = classify_task("open a PR")

    assert hint.kind is not ScopeKind.INSPECT


def test_directory_named_after_a_verb_is_still_inspection() -> None:
    # A directory named after a mutation verb ("build", "run") is a target, not
    # an action — reading it must not be knocked out of the inspect route.
    for task in ("open build directory", "show run folder", "list the build directory"):
        hint = classify_task(task)
        assert hint.kind is ScopeKind.INSPECT, task
        assert hint.route == "single_explorer", task


def test_diagnostic_find_is_not_read_only_inspection() -> None:
    # "find why X is failing" needs an investigator (Bash/tests), not a
    # read-only explorer, so it must fall through the inspect route.
    for task in (
        "find why pytest is failing in the auth module",
        "show why the build folder is broken and fails",
    ):
        assert classify_task(task).kind is not ScopeKind.INSPECT, task


def test_find_and_move_or_copy_is_a_mutation_not_inspection() -> None:
    # A filesystem move/copy is a change; pairing it with a read-only verb must
    # not intercept it into the read-only explorer route.
    for task in (
        "find and move src/foo.py to src/bar.py",
        "find and copy config.py to backup.py",
    ):
        assert classify_task(task).kind is not ScopeKind.INSPECT, task


def test_classifies_known_file_edit_as_simple_edit() -> None:
    hint = classify_task("Update weather-dashboard.html to refresh every 5 minutes")

    assert hint.kind is ScopeKind.SIMPLE_EDIT
    assert hint.route == "single_coder"
    assert not hasattr(hint, "max_workers")
    assert "known file" in hint.reason
    assert "initial routing recommendation" in hint.prompt_block()


def test_classifies_small_artifact_as_simple_artifact_without_html_special_case() -> None:
    hint = classify_task("Create a compact CSV report showing current sales")

    assert hint.kind is ScopeKind.SIMPLE_ARTIFACT
    assert hint.route == "single_coder"
    assert not hasattr(hint, "max_workers")
    assert "start with one coder" in hint.prompt_block().lower()
    assert "artifact" in hint.reason


def test_large_risky_feature_requires_plan_design_workflow() -> None:
    hint = classify_task("Add billing auth, database migration, and public API endpoints")

    assert hint.kind is ScopeKind.LARGE_FEATURE
    assert hint.route == "plan_design_workflow"
    assert "plan" in hint.requires
    assert "design" in hint.requires


def test_medium_change_routes_through_planner_before_coder() -> None:
    hint = classify_task("Improve the dashboard weather display")

    assert hint.kind is ScopeKind.MEDIUM_CHANGE
    assert hint.route == "planner_then_coder_check"
    assert not hasattr(hint, "max_workers")
    assert "plan" in hint.requires
    assert "implementation" in hint.requires
    assert "verification" in hint.requires


def test_vague_request_asks_scope_before_spawning() -> None:
    hint = classify_task("fix this")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "ask_scope"
    assert not hasattr(hint, "max_workers")


def test_greeting_routes_to_direct_answer_without_workers() -> None:
    hint = classify_task("hi")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "direct_answer"
    assert not hasattr(hint, "max_workers")


def test_delete_file_request_routes_to_manual_destructive_answer() -> None:
    hint = classify_task("delete all *-dashboard.html files")

    assert hint.kind is ScopeKind.UNKNOWN
    assert hint.route == "manual_destructive"
    assert not hasattr(hint, "max_workers")


# ── deterministic ambiguity/impact/risk assessment ──────────────────────────


def test_bare_website_creation_requires_clarification() -> None:
    result = assess_request("create a new website")
    assert (result.ambiguity, result.impact, result.risk) == (
        Band.HIGH, Band.UNKNOWN, Band.UNKNOWN,
    )
    assert result.route == "ask_scope"
    assert result.clarifying_question == (
        "What should the website do, and should it be a static page or use "
        "a specific framework?"
    )


def test_constrained_single_file_website_is_simple() -> None:
    result = assess_request(
        "Create a static single-file website at landing.html with hero, "
        "features, and contact sections"
    )
    assert result.ambiguity is Band.LOW
    assert result.impact is Band.LOW
    assert result.risk is Band.LOW
    assert result.route == "single_coder"


def test_specific_framework_scaffold_is_medium() -> None:
    result = assess_request(
        "Create a Next.js app-router scaffold with home/about routes, shared "
        "navbar/footer, TypeScript, and a production build check"
    )
    assert result.impact is Band.MEDIUM
    assert result.route == "planner_then_coder_check"


def test_auth_database_payment_site_is_large() -> None:
    result = assess_request(
        "Build a website with authentication, a customer database, and payments"
    )
    assert result.risk is Band.HIGH
    assert result.route == "plan_design_workflow"


def test_single_critical_term_routes_to_plan_design_workflow() -> None:
    # The deterministic critical-risk gate must fire on ONE token: "add
    # authentication" was previously downgraded to a medium change because the
    # legacy _LARGE_RISK_RE threshold needs two tokens.
    hint = classify_task("Add authentication to the app")
    assert hint.kind is ScopeKind.LARGE_FEATURE
    assert hint.route == "plan_design_workflow"
    assert "plan" in hint.requires and "review" in hint.requires


def test_each_critical_risk_category_routes_to_plan_design_workflow() -> None:
    requests = (
        "Add login to the app",
        "Change user permissions",
        "Add payments to the checkout",
        "Create customer databases",
        "Run data migrations",
        "Change security settings",
        "Change the public API contract",
    )
    for request in requests:
        assessment = assess_request(request)
        assert assessment.risk is Band.HIGH, request
        assert assessment.route == "plan_design_workflow", request
        hint = classify_task(request)
        assert hint.kind is ScopeKind.LARGE_FEATURE, request
        assert hint.route == "plan_design_workflow", request
