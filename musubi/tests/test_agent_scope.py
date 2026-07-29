"""Scope-aware routing hints for the standalone root agent."""

from __future__ import annotations

from agent.change_assessment import BROAD_PRODUCT_QUESTION, Band, assess_request
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


def test_sensitive_multi_area_request_is_denied_the_coder_shortcut() -> None:
    # Lexical text cannot establish blast radius, so this no longer claims to
    # be "large" — that verdict belongs to `assess_manifest`, after the planner
    # has read the code. What the sentence CAN justify is withholding the
    # single_coder shortcut so a read-only planner looks first.
    hint = classify_task("Add billing auth, database migration, and public API endpoints")

    assert hint.kind is ScopeKind.MEDIUM_CHANGE
    assert hint.route == "planner_then_coder_check"
    assert "plan" in hint.requires


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


def test_answered_clarification_never_asks_the_same_question_again() -> None:
    # The traced loop: turn 1 "create a website" asked the canned question,
    # turn 2 answered it ("a weather checking website"), and because
    # `classify_task` reads one message the answer classified identically —
    # same question, three turns, zero files. With the conversation's one
    # clarification already spent, the merged request must go to a planner.
    merged = (
        "create a website\n\n"
        "[clarification answer] i would like to create a weather checking website"
    )
    assert classify_task(merged).route == "ask_scope"

    hint = classify_task(merged, has_history=True, allow_clarification=False)
    assert hint.kind is ScopeKind.MEDIUM_CHANGE
    assert hint.route == "planner_then_coder_check"
    assert hint.requires == ("plan", "implementation", "verification")
    # The assessment rides along with the halt stripped out, so nothing
    # downstream can resurrect the question from it.
    assert hint.assessment is not None
    assert hint.assessment.route == "planner_then_coder_check"
    assert hint.assessment.clarifying_question is None
    assert "clarification-answered" in hint.assessment.evidence


def test_spent_clarification_also_releases_a_vague_follow_up() -> None:
    # "fix this" halts on its own; as the answer to a question already asked it
    # carries the prior request's content and must route rather than re-ask.
    assert classify_task("fix this").route == "ask_scope"
    assert classify_task(
        "fix this", allow_clarification=False,
    ).route == "planner_then_coder_check"
    # An EMPTY message is the one thing still worth a question: there is no
    # merged text to plan from.
    assert classify_task("", allow_clarification=False).route == "ask_scope"


def test_spent_clarification_only_removes_halts_never_adds_one() -> None:
    # The flag may only move routing toward doing the work. Every other route
    # must classify identically with or without it, so a wrong flag can never
    # escalate a greeting, an inspection, or a deletion into a mutation.
    for task in (
        "hi",
        "delete all *-dashboard.html files",
        "open C:\\Workspace\\Musubi",
        "explain each",
        "update the header text in landing.html",
        "add authentication to the app",
    ):
        assert (
            classify_task(task, allow_clarification=False).route
            == classify_task(task).route
        ), task


# ── deterministic ambiguity/impact/risk assessment ──────────────────────────


def test_bare_website_creation_requires_clarification() -> None:
    result = assess_request("create a new website")
    assert (result.ambiguity, result.impact, result.risk) == (
        Band.HIGH, Band.UNKNOWN, Band.UNKNOWN,
    )
    assert result.route == "ask_scope"
    assert result.clarifying_question == (
        BROAD_PRODUCT_QUESTION
    )


def test_the_clarifying_question_is_one_its_answer_can_settle() -> None:
    # A question the asker cannot act on is not a governance step. The earlier
    # wording led with "What should the website do?", which nothing in
    # `assess_request` tests — so the honest answer "a weather checking
    # website" re-matched `_BROAD_PRODUCT_RE` with no escape hatch touched and
    # drew the identical sentence back. Every natural answer to the question as
    # written must now change the verdict ON ITS OWN, without relying on the
    # one-question-per-stall escape to break the tie.
    assert assess_request("create a website").clarifying_question == (
        BROAD_PRODUCT_QUESTION
    )
    for answer, expected in (
        ("a static page", "single_coder"),
        ("just a single static page", "single_coder"),
        ("a static single-file page showing the weather", "single_coder"),
        ("react", "planner_then_coder_check"),
        ("next.js please", "planner_then_coder_check"),
    ):
        merged = f"create a website\n\n[clarification answer] {answer}"
        assert classify_task(merged).route == expected, answer


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


def test_assess_request_never_claims_a_change_is_large() -> None:
    # "Large" has exactly one source of truth: the planner's manifest. No
    # sentence, however alarming its vocabulary, may return the large route
    # from pure text analysis.
    for request in (
        "Build a website with authentication, a customer database, and payments",
        "Add billing auth, database migration, and public API endpoints",
        "rewrite the entire user system",
        "migrate all 40 services to the new runtime",
    ):
        assert assess_request(request).route != "plan_design_workflow", request


def test_sensitive_request_runs_instead_of_being_refused() -> None:
    # The old keyword gate answered "add authentication" with a canned
    # pipeline recommendation and zero model calls. A request must never be
    # refused on vocabulary alone; it runs, planner-first.
    hint = classify_task("Add authentication to the app")
    assert hint.kind is ScopeKind.MEDIUM_CHANGE
    assert hint.route == "planner_then_coder_check"
    assert "plan" in hint.requires and "verification" in hint.requires


def test_every_sensitive_area_loses_the_lone_coder_shortcut() -> None:
    # A mistake in these areas is invisible — the page still renders and the
    # tests still pass — so a read-only planner must read the code and file a
    # manifest before anything mutates. Includes the vocabulary the old list
    # was blind to (SSO, Okta, passwords, sessions, plural "payments").
    requests = (
        "Add login to the app",
        "Change user permissions",
        "Add payments to the checkout",
        "Create customer databases",
        "Run data migrations",
        "Change security settings",
        "Change the public API contract",
        "wire up Okta for the web app",
        "add SSO to the app",
        "let users sign in with Google",
        "store user passwords in the users table",
        "add a session cookie so users stay signed in",
        "create the payments dashboard page",
        "create login.html",
    )
    for request in requests:
        hint = classify_task(request)
        assert hint.route == "planner_then_coder_check", request
        assert hint.kind is ScopeKind.MEDIUM_CHANGE, request


def test_ordinary_requests_keep_the_shortcut() -> None:
    # The guard must stay narrow: nothing sensitive, nothing withheld.
    assert classify_task("create a dashboard page").route == "single_coder"
    assert classify_task("update the title in index.html").route == "single_coder"
