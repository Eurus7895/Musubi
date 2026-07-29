"""The routing vocabulary is closed and fully covered.

musubi-tier: substrate test — pins that every route a classifier can emit is
a declared `RouteKind`, and that every declared kind carries prompt guidance.
"""

from __future__ import annotations

from agent.change_assessment import (
    ChangeManifest,
    assess_manifest,
    assess_request,
)
from agent.routes import RouteKind
from agent.scope import ScopeHint, ScopeKind, classify_task


def _manifest(**over: object) -> ChangeManifest:
    base = dict(
        files_expected=1, subsystems=("markup",), public_contract=False,
        data_migration=False, security_sensitive=False,
        external_side_effects=False, destructive=False, unknowns=(),
        validation_commands=1,
    )
    base.update(over)
    return ChangeManifest(**base)  # type: ignore[arg-type]


def test_every_emitted_route_is_a_declared_kind() -> None:
    # A route drove 43 bare string literals across four modules, and a typo in
    # any of them fails SILENTLY — `route == "single_codr"` is just False, so
    # the request quietly takes another path with no error anywhere.
    valid = set(RouteKind)
    corpus = (
        "hi", "fix this", "", "delete all *-dashboard.html files",
        "explain each", "read run.py", "open the src folder",
        "create a website", "create weather.html",
        "update landing.html to add a footer",
        "add authentication to the app", "make a payments dashboard",
        "create a next.js app with routes and a navbar",
    )
    for task in corpus:
        assert classify_task(task).route in valid, task
        assert classify_task(task, allow_clarification=False).route in valid, task
        assert assess_request(task).route in valid, task

    for manifest in (
        _manifest(),
        _manifest(unknowns=("palette",)),
        _manifest(files_expected=9),
        _manifest(security_sensitive=True),
        _manifest(files_expected=3, subsystems=("auth", "billing")),
    ):
        assert assess_manifest(manifest).route in valid


def test_every_route_kind_carries_prompt_guidance() -> None:
    # A route with no entry in the guidance table falls through to "Use the
    # route conservatively", which tells the root nothing. Adding a kind
    # without its guidance is the way that happens silently.
    for kind in RouteKind:
        hint = ScopeHint(kind=ScopeKind.UNKNOWN, route=kind, reason="test")
        block = hint.prompt_block()
        assert f"route={kind.value}" in block
        assert "Use the route conservatively" not in block, kind


def test_route_kind_stays_string_compatible() -> None:
    # Existing comparisons, dict keys, log lines, and stored audit values all
    # treat a route as text. StrEnum keeps that true — this pins it so a later
    # switch to a plain Enum cannot pass silently.
    assert RouteKind.ASK_SCOPE == "ask_scope"
    assert f"{RouteKind.SINGLE_CODER}" == "single_coder"
    assert {"ask_scope": 1}[RouteKind.ASK_SCOPE] == 1
