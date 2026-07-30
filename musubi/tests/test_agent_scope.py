"""What survives of the pre-model classification layer.

musubi-tier: substrate test — pins the two properties the remaining regex must
have (it warns, it never routes) and the absence of everything deleted with
plan step 4.

The 32 tests this file used to hold asserted the behaviour of nineteen regexes:
which sentences read as broad, sensitive, consultative, or bounded. They were
correct about the code and wrong about the world — the layer they pinned is
gone, and pinning it harder was never going to make a sentence establish a
blast radius. What replaced it is tested where it can actually be checked:
`test_evidence.py` (facts about the record), `test_triage.py` (the root's own
declaration), `test_goal_state.py` (the mutation gate and the overrun stop),
`test_manifest.py` (arithmetic on the planner's declaration), and
`test_blast_radius.py` (the destructive stop at the tool boundary).
"""

from __future__ import annotations

import inspect

import agent.scope as scope_mod
from agent.routes import RouteKind
from agent.scope import DESTRUCTIVE_WARNING, ScopeKind, classify_task


def test_no_request_is_routed_before_a_model_reads_it() -> None:
    # Every request, of every shape, gets the same answer: the harness does not
    # know. That is the point of the deletion — the previous answers were
    # confident and unverifiable.
    for task in (
        "hi",
        "explain each option",
        "read agent/run.py",
        "add a dark theme to dashboard.html",
        "create a website",
        "migrate all 40 services to the new runtime",
        "",
    ):
        hint = classify_task(task)
        assert hint.route == RouteKind.ROOT_DECIDES, task
        assert hint.kind is ScopeKind.UNKNOWN, task
        assert hint.assessment is None, task


def test_the_one_surviving_question_answers_with_a_warning() -> None:
    warned = classify_task("delete all *-dashboard.html files")

    assert DESTRUCTIVE_WARNING in warned.warnings
    # A warning never moves the route. The hard stop is a measurement at the
    # tool boundary (`agent/blast_radius.py`), where the files can be counted.
    assert warned.route == RouteKind.ROOT_DECIDES


def test_the_warning_reaches_the_prompt() -> None:
    block = classify_task("remove the build folder").prompt_block()

    assert "warning=" in block
    assert "REFUSES any that deletes a file" in block
    # And it states its own ignorance rather than implying a decision.
    assert "no route was guessed" in block


def test_an_ordinary_request_carries_no_warning() -> None:
    hint = classify_task("add a dark theme to dashboard.html")

    assert hint.warnings == ()
    assert "warning=" not in hint.prompt_block()


def test_the_prompt_block_offers_no_route_guidance() -> None:
    # The block used to carry `route=` plus a paragraph telling the root which
    # worker to spawn — issued by regexes that had read no file. Both are gone,
    # and their absence is the assertion.
    block = classify_task("create a website").prompt_block()

    for gone in ("guidance=", "suggested_route=", "scope=", "Suggests:"):
        assert gone not in block, gone


def test_the_deleted_layer_is_actually_deleted() -> None:
    # cost-lever accounting for plan step 4. If any of these come back, the
    # expiry trigger this module's header declared has been un-fired.
    for name in (
        "assess_request",
        "BROAD_PRODUCT_QUESTION",
        "_classify_route",
        "_BROAD_PRODUCT_RE",
        "_ADVISORY_RE",
        "_INSPECT_RE",
        "_CASUAL_RE",
        "_VAGUE_RE",
        "_NO_SHORTCUT_RE",
        "_SIMPLE_EDIT_RE",
        "_ARTIFACT_RE",
        "_FRAMEWORK_RE",
        "_MULTIPART_RE",
        "_STATIC_FILE_RE",
        "_BOUNDED_ARTIFACT_RE",
        "is_simple_scope",
    ):
        assert not hasattr(scope_mod, name), name


def test_one_regex_remains() -> None:
    patterns = [
        name for name in vars(scope_mod)
        if name.endswith("_RE") or name.endswith("_QUESTION")
    ]

    assert patterns == ["_DESTRUCTIVE_FILE_RE"], patterns


def test_classify_task_takes_only_the_request() -> None:
    # `has_history` and `allow_clarification` existed to steer the clarification
    # halt, which no longer exists. A caller still passing them has not noticed.
    assert list(inspect.signature(classify_task).parameters) == ["task"]
