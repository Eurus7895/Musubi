"""The evidence vector states facts about the record, never opinions about it.

musubi-tier: substrate test — pins the containment test against the firewall's,
and pins the distinction the layer it replaces could not make: "I do not know
what this targets" versus "this targets something that is not there".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.evidence import NO_PROGRESS_TURNS, collect


@pytest.fixture()
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "run.py").write_text("x", encoding="utf-8")
    return tmp_path


def test_a_named_existing_path_is_evidence(workspace: Path) -> None:
    vector = collect("please read agent/run.py and explain the loop")

    assert vector.names_workspace_path is True
    assert vector.path_exists is True
    assert vector.named_paths == ("agent/run.py",)
    assert vector.target_is_unknown is False


def test_a_named_missing_path_is_still_a_target(workspace: Path) -> None:
    # The distinction the lexical layer could not draw. "edit agent/gone.py" is
    # not vague — the user said exactly what they mean; the file is simply not
    # there. Routing it to a clarifying question wasted a turn asking something
    # the filesystem had already answered.
    vector = collect("edit agent/gone.py")

    assert vector.names_workspace_path is True
    assert vector.path_exists is False
    assert vector.target_is_unknown is False


def test_a_request_naming_nothing_is_the_unknown_target(workspace: Path) -> None:
    vector = collect("create a website")

    assert vector.names_workspace_path is False
    assert vector.path_exists is False
    assert vector.target_is_unknown is True
    assert "Do not send a coder at a guess" in vector.prompt_block()


def test_a_path_outside_the_root_is_reported_as_unreachable(
    workspace: Path,
) -> None:
    # The traced session ended on "Cannot access: the target path resolves
    # outside the workspace root" AFTER a spawn. The vector says so before one.
    vector = collect("summarize /etc/hosts for me")

    assert vector.names_workspace_path is False
    assert vector.escaped_paths == ("/etc/hosts",)
    block = vector.prompt_block()
    assert "no worker can reach them" in block
    # The refusal names the remedy the console actually offers, so the root
    # stops answering "run this by hand in PowerShell".
    assert "attach that folder" in block


def test_an_attached_folder_is_reachable_and_named_by_its_alias(
    workspace: Path, tmp_path: Path,
) -> None:
    # The whole point of a folder grant: the operator hands over a directory
    # outside the harness root, and the turn must treat it as reachable. Before
    # this, the vector measured containment against the harness root alone, so
    # every attached folder landed in `escaped_paths` and `prompt_block` told
    # the root agent to refuse it — one block above the roots listing that said
    # the opposite.
    external = tmp_path.parent / "bamf-updater"
    external.mkdir(exist_ok=True)
    (external / "temp.log").write_text("y", encoding="utf-8")
    roots = (("musubi", workspace), ("bamf-updater", external))

    vector = collect(f"delete everything in {external}", roots=roots)

    assert vector.escaped_paths == ()
    assert vector.names_workspace_path is True
    assert vector.path_exists is True
    assert vector.named_paths == ("bamf-updater",)
    assert vector.named_root_aliases == ("bamf-updater",)
    assert "outside_granted_roots" not in vector.prompt_block()


def test_a_path_under_an_attached_folder_carries_its_root_argument(
    workspace: Path, tmp_path: Path,
) -> None:
    external = tmp_path.parent / "bamf-updater"
    external.mkdir(exist_ok=True)
    roots = (("musubi", workspace), ("bamf-updater", external))

    vector = collect(f"read {external / 'temp.log'}", roots=roots)

    assert vector.named_paths == ("bamf-updater/temp.log",)
    # The alias is the tool's `root` argument, not part of the filename. A
    # first segment cannot be recognised as an alias by shape alone — this is
    # what `named_root_aliases` exists to say.
    assert vector.named_root_aliases == ("bamf-updater",)
    assert "`root` argument" in vector.prompt_block()


def test_a_relative_token_still_resolves_against_the_harness_root(
    workspace: Path, tmp_path: Path,
) -> None:
    external = tmp_path.parent / "bamf-updater"
    external.mkdir(exist_ok=True)
    roots = (("musubi", workspace), ("bamf-updater", external))

    vector = collect("update agent/run.py", roots=roots)

    assert vector.named_paths == ("agent/run.py",)
    assert vector.named_root_aliases == ()


def test_a_path_in_no_granted_root_is_still_unreachable(
    workspace: Path, tmp_path: Path,
) -> None:
    external = tmp_path.parent / "bamf-updater"
    external.mkdir(exist_ok=True)
    roots = (("musubi", workspace), ("bamf-updater", external))

    vector = collect("summarize /etc/hosts", roots=roots)

    assert vector.escaped_paths == ("/etc/hosts",)
    assert vector.names_workspace_path is False


def test_containment_matches_the_firewall(workspace: Path) -> None:
    # Same verdict as tools/fs.resolve_path, which is the point: a vector that
    # promised a path the firewall then refuses would be worse than no vector.
    from tools.fs import resolve_path

    vector = collect("look at ../../../etc/passwd")

    assert vector.names_workspace_path is False
    with pytest.raises(PermissionError):
        resolve_path("../../../etc/passwd")


def test_prose_is_not_mistaken_for_paths(workspace: Path) -> None:
    # A bare noun has nothing to resolve. If every word were a path candidate,
    # `path_exists` would answer a question nobody asked.
    vector = collect("make the website faster and nicer please")

    assert vector.named_paths == ()
    assert vector.escaped_paths == ()


def test_explorer_findings_answer_the_unknown_target(workspace: Path) -> None:
    # Two ways to stop being lost: name the target, or send someone to find it.
    vector = collect("make it faster", explorer_findings=True)

    assert vector.names_workspace_path is False
    assert vector.target_is_unknown is False


def test_barren_turns_are_stated_once_they_pass_the_threshold(
    workspace: Path,
) -> None:
    quiet = collect("keep going", barren_turns=NO_PROGRESS_TURNS - 1)
    loud = collect("keep going", barren_turns=NO_PROGRESS_TURNS)

    assert "produced no file" not in quiet.prompt_block()
    assert "produced no file" in loud.prompt_block()


def test_the_vector_never_raises(workspace: Path) -> None:
    # It runs before every turn; a malformed request must not cost the turn.
    for request in ("", None, "\x00\x00", "a" * 5000, "//////", "..", "C:\\"):
        collect(request)  # type: ignore[arg-type]


def test_absent_facts_default_to_absent(workspace: Path) -> None:
    vector = collect("anything", barren_turns=-4)

    assert vector.has_conversation is False
    assert vector.explorer_findings is False
    assert vector.barren_turns == 0


def test_a_url_is_not_a_filesystem_path(workspace: Path) -> None:
    # PR #164 review: the path pattern matched from `//` onward, so a URL
    # resolved "outside the workspace" and the prompt told the root no worker
    # could reach it — false when an HTTP or browser MCP server is configured,
    # which is precisely when the request is about a URL.
    vector = collect("summarize https://example.com/docs/page.html for me")

    assert vector.escaped_paths == ()
    assert vector.named_paths == ()
    assert "no worker can reach" not in vector.prompt_block()


def test_a_url_beside_a_real_path_leaves_the_path_alone(workspace: Path) -> None:
    vector = collect("port https://example.com/a.html into agent/run.py")

    assert vector.named_paths == ("agent/run.py",)
    assert vector.escaped_paths == ()


def test_the_prompt_omits_facts_that_change_mid_turn(workspace: Path) -> None:
    # The system prompt is built once and never rewritten. `explorer_findings`
    # flips the moment a read-only worker reports, so freezing it into that
    # block would contradict the outcome the root reads later in the same turn.
    block = collect("create a website").prompt_block()

    assert "explorer_findings" not in block
    assert "as this turn begins" in block
    # It stays in the log line, which is honest about being one moment.
    assert "explorer=" in collect("create a website").log_line()
