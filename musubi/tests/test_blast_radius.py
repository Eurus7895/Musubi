"""The destructive gate measures the call instead of reading the sentence.

musubi-tier: substrate test — pins the counts, the thresholds, the fail-closed
band, and the case the old lexical guard could never see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.blast_radius import (
    DELETE_CONFIRM_THRESHOLD,
    OVERWRITE_CONFIRM_THRESHOLD,
    RunningTotals,
    describe,
    exceeds_threshold,
    measure,
)


@pytest.fixture()
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    (tmp_path / "build").mkdir()
    for i in range(3):
        (tmp_path / "build" / f"out{i}.js").write_text("x", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"page{i}.html").write_text("x", encoding="utf-8")
    return tmp_path


def test_the_case_the_old_guard_let_through(workspace: Path) -> None:
    # `rm -rf build` never matched _DESTRUCTIVE_FILE_RE ("build" was not in its
    # noun list), so it routed to a coder holding musubi_run_command — a tool
    # whose contract states it does no dangerous-command detection. The one
    # path that could wipe a workspace was the one nobody watched.
    radius = measure("musubi_run_command", {"command": "rm -rf build"})

    assert radius.delete_count == 3, "must count the files, not the word"
    assert all(name.endswith(".js") for name in radius.deletes)
    assert exceeds_threshold(radius, RunningTotals())
    message = describe(radius, RunningTotals())
    assert "DELETE 3 file(s)" in message
    assert "out0.js" in message, "the user must see WHICH files"


def test_one_deletion_is_enough_to_stop_for(workspace: Path) -> None:
    assert DELETE_CONFIRM_THRESHOLD == 1
    radius = measure("musubi_run_command", {"command": "rm keep.py"})
    assert radius.delete_count == 1
    assert exceeds_threshold(radius, RunningTotals())


def test_a_delete_that_cannot_be_parsed_fails_closed(workspace: Path) -> None:
    # No static analysis can say what an arbitrary pipeline removes. Assuming
    # "small" is worst exactly here, so an unreadable delete is over every
    # threshold rather than under it.
    for command in (
        "find . -name '*.tmp' | xargs rm",
        "rm -rf $(cat targets.txt)",
        "rm -rf build && rm -rf dist",
    ):
        radius = measure("musubi_run_command", {"command": command})
        assert radius.unanalyzable, command
        assert exceeds_threshold(radius, RunningTotals()), command
        assert "cannot be resolved" in describe(radius, RunningTotals())


def test_a_command_that_deletes_nothing_is_not_this_module_s_business(
    workspace: Path,
) -> None:
    # The gate must not become a general shell police force — that would
    # reintroduce the judgment it exists to replace, one layer lower.
    for command in (
        "npm run build",
        "git status",
        "python -m pytest -q",
        "ls -la build",
        "echo removing nothing",
    ):
        assert measure("musubi_run_command", {"command": command}).is_empty, command


def test_overwrite_counts_only_an_existing_file_and_only_write(
    workspace: Path,
) -> None:
    # Creating a file destroys nothing; an edit or an append changes a file
    # without replacing it. Only a write onto an existing path is an overwrite.
    assert measure("musubi_write_file", {"path": "brand-new.html"}).is_empty
    assert measure("musubi_edit_file", {"path": "keep.py"}).is_empty
    assert measure("musubi_append_file", {"path": "keep.py"}).is_empty

    radius = measure("musubi_write_file", {"path": "keep.py"})
    assert radius.overwrite_count == 1
    assert radius.delete_count == 0
    # One overwrite is routine and must not stop the run.
    assert not exceeds_threshold(radius, RunningTotals())


def test_the_overwrite_ceiling_is_per_run_not_per_call(workspace: Path) -> None:
    # A worker rewriting one file per cycle never trips a per-call check —
    # which is exactly the drift the ceiling exists to catch.
    totals = RunningTotals()
    for i in range(OVERWRITE_CONFIRM_THRESHOLD - 1):
        radius = measure("musubi_write_file", {"path": f"page{i}.html"})
        assert not exceeds_threshold(radius, totals), i
        totals.add(radius)

    last = measure(
        "musubi_write_file",
        {"path": f"page{OVERWRITE_CONFIRM_THRESHOLD - 1}.html"},
    )
    assert exceeds_threshold(last, totals)
    assert f"overwrite number {OVERWRITE_CONFIRM_THRESHOLD}" in describe(last, totals)

    # Rewriting the SAME file again is not new blast radius.
    totals.add(last)
    again = measure("musubi_write_file", {"path": "page0.html"})
    totals.add(again)
    assert totals.overwrite_count == OVERWRITE_CONFIRM_THRESHOLD


def test_a_path_outside_the_workspace_is_never_counted_or_touched(
    workspace: Path, tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "elsewhere.txt"
    outside.write_text("x", encoding="utf-8")
    assert measure("musubi_run_command", {"command": f"rm {outside}"}).delete_count == 0
    assert measure("musubi_write_file", {"path": str(outside)}).is_empty


def test_malformed_input_never_raises(workspace: Path) -> None:
    for tool, args in (
        ("musubi_run_command", {}),
        ("musubi_run_command", {"command": ""}),
        ("musubi_run_command", {"command": 'rm "unterminated'}),
        ("musubi_write_file", {"path": None}),
        ("musubi_write_file", {}),
        ("some_other_tool", {"path": "keep.py"}),
    ):
        measure(tool, args)  # must not raise


def test_a_delete_verb_that_is_not_a_command_passes_through(
    workspace: Path,
) -> None:
    # The gate must not fire on the LETTERS. `grep -r rm .` searches for a
    # string; blocking it would rebuild the sentence-guessing guard one layer
    # down, which is the whole thing this replaces.
    for command in (
        "grep -r rm .",
        "echo rm",
        'git commit -m "remove old files"',
        "cat rm-notes.txt",
    ):
        assert measure("musubi_run_command", {"command": command}).is_empty, command


def test_the_grant_is_an_operator_env_var_not_a_tool_argument() -> None:
    # A worker cannot set its own process env, so the escape hatch is a human's
    # decision by construction — the model cannot argue its way past the gate.
    from agent.run import DESTRUCTIVE_GRANT_ENV, _destructive_grant

    assert DESTRUCTIVE_GRANT_ENV == "MUSUBI_ALLOW_DESTRUCTIVE"
    assert _destructive_grant() is False


def test_gate_refuses_the_batch_and_names_the_files(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.run import Orchestration, _preflight_destructive_batch

    orchestration = Orchestration(parent_session_id="s1")
    calls = [
        {"id": "t1", "name": "musubi_run_command", "input": {"command": "rm -rf build"}},
        {"id": "t2", "name": "musubi_read_file", "input": {"path": "keep.py"}},
    ]
    import io

    refusals = _preflight_destructive_batch(
        calls, orchestration=orchestration, log=io.StringIO(),
    )
    assert set(refusals) == {"t1"}, "only the destructive call is refused"
    assert "out0.js" in refusals["t1"]

    # With the operator's grant the same batch proceeds and is accounted for.
    monkeypatch.setenv("MUSUBI_ALLOW_DESTRUCTIVE", "1")
    assert _preflight_destructive_batch(
        calls, orchestration=Orchestration(parent_session_id="s1"),
        log=io.StringIO(),
    ) == {}


# ── one-time approval, verifiable without reading prose ─────────────────────


def test_the_token_is_bound_to_the_exact_file_set(workspace: Path) -> None:
    from agent.blast_radius import GRANT_PREFIX, covered_by, grant_token

    three = measure("musubi_run_command", {"command": "rm -rf build"})
    token = grant_token(three.keys)
    assert token.startswith(GRANT_PREFIX)
    # Order-independent, so the same files always yield the same token…
    assert grant_token(tuple(reversed(three.keys))) == token
    # …and one extra file yields a different one, so approval cannot widen.
    assert grant_token(three.keys + ("extra.js",)) != token

    approved = frozenset(three.keys)
    assert covered_by(three, approved)
    assert not covered_by(
        measure("musubi_run_command", {"command": "rm keep.py"}), approved,
    )


def test_approval_reads_the_token_not_the_sentiment(workspace: Path) -> None:
    # The harness cannot tell "yes delete them" from "no don't" — judging a
    # sentence is what this whole redesign removes. It matches a literal it
    # minted itself, which is string equality, not interpretation.
    from agent.blast_radius import approved_keys_from, encode_pending, grant_token

    radius = measure("musubi_run_command", {"command": "rm -rf build"})
    token = grant_token(radius.keys)
    pending = encode_pending([(token, radius.keys)])

    assert approved_keys_from(pending, token) == frozenset(radius.keys)
    assert approved_keys_from(pending, f"ok, {token} please") == frozenset(radius.keys)
    for prose in ("ok xoá đi", "yes delete them", "go ahead", "allow-000000", ""):
        assert approved_keys_from(pending, prose) == frozenset(), prose


def test_unreadable_storage_leaves_the_gate_shut(workspace: Path) -> None:
    from agent.blast_radius import approved_keys_from

    for pending in (None, "", "not json", "{}", "[1,2]", '[{"token":1}]'):
        assert approved_keys_from(pending, "allow-abc123") == frozenset(), pending


def test_gate_mints_a_token_then_honours_it_next_run(
    workspace: Path,
) -> None:
    import io

    from agent.blast_radius import approved_keys_from, encode_pending
    from agent.run import Orchestration, _preflight_destructive_batch

    calls = [
        {"id": "t1", "name": "musubi_run_command", "input": {"command": "rm -rf build"}},
    ]
    first = Orchestration(parent_session_id="s1")
    refusals = _preflight_destructive_batch(
        calls, orchestration=first, log=io.StringIO(),
    )
    assert "reply with: allow-" in refusals["t1"]
    assert len(first.pending_destructive) == 1

    # The user echoes the token; the next run allows exactly those paths.
    token, keys = first.pending_destructive[0]
    approved = approved_keys_from(encode_pending([(token, keys)]), f"{token}")
    second = Orchestration(parent_session_id="s1", approved_destructive=approved)
    assert _preflight_destructive_batch(
        calls, orchestration=second, log=io.StringIO(),
    ) == {}

    # A DIFFERENT deletion is not covered by that approval.
    other = [
        {"id": "t2", "name": "musubi_run_command", "input": {"command": "rm keep.py"}},
    ]
    assert _preflight_destructive_batch(
        other, orchestration=second, log=io.StringIO(),
    ) != {}


def test_a_dropped_token_is_restored_by_the_harness(workspace: Path) -> None:
    """Consent must not depend on the model relaying the refusal faithfully."""
    from agent.run import _ensure_grant_visible

    pending = [("allow-a3f9c1", ("build/a.js",)), ("allow-b7e204", ("out/b.js",))]

    # The model paraphrased and dropped both tokens: the harness puts them back.
    restored = _ensure_grant_visible("I stopped before deleting anything.", pending)
    assert "reply with: allow-a3f9c1" in restored
    assert "reply with: allow-b7e204" in restored

    # A token the model DID relay is not printed twice.
    kept = _ensure_grant_visible("… reply with: allow-a3f9c1", [pending[0]])
    assert kept.count("allow-a3f9c1") == 1

    # Two refusals over the same radius print one line, not two.
    once = _ensure_grant_visible("blocked", [pending[0], pending[0]])
    assert once.count("allow-a3f9c1") == 1
