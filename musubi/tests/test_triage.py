"""The root's declared turn shape is recorded, never inferred.

musubi-tier: substrate test — pins the parse, the closed vocabulary, and the
one rule that makes the record worth keeping: an absent declaration stays
absent.
"""

from __future__ import annotations

from agent.triage import (
    MAX_TRIAGE_REASON,
    TRIAGE_SHAPES,
    encode_triage,
    parse_triage,
    prompt_block,
)


def test_a_declaration_is_read_back_whole() -> None:
    declared = parse_triage(
        "[triage] work: dashboard.html exists and the change is one file\n"
        "I'll spawn a coder."
    )

    assert declared == (
        "work", "dashboard.html exists and the change is one file",
    )


def test_the_first_declaration_wins() -> None:
    # A root that restates its triage mid-turn has changed its mind. The turn
    # was planned around the first one, and letting a later line overwrite it
    # would quietly rewrite the record it exists to preserve.
    declared = parse_triage(
        "[triage] inspect: just reading\n...\n[triage] work: actually writing"
    )

    assert declared is not None and declared[0] == "inspect"


def test_a_shape_outside_the_vocabulary_is_dropped() -> None:
    # The column is counted, not read one row at a time, so it holds the closed
    # set or nothing. A near-miss stored verbatim would need normalising later.
    assert parse_triage("[triage] refactor: tidy things up") is None
    assert parse_triage("[triage] WORK: caps are fine") == ("work", "caps are fine")


def test_a_malformed_or_absent_declaration_is_absent() -> None:
    for text in (
        "",
        "I think this is work.",
        "[triage] work",           # no reason: the reviewable half is missing
        "[triage]: no shape",
        "triage: work — no brackets",
    ):
        assert parse_triage(text) is None, text


def test_a_runaway_reason_is_bounded() -> None:
    declared = parse_triage("[triage] work: " + "x" * 900)

    assert declared is not None
    assert len(declared[1]) <= MAX_TRIAGE_REASON


def test_the_encoding_is_one_readable_column_value() -> None:
    assert encode_triage(("work", "one file")) == "work: one file"
    # Absent stays absent — a placeholder would be indistinguishable from a
    # real declaration when the column is counted.
    assert encode_triage(None) is None


def test_the_prompt_teaches_exactly_the_vocabulary_it_parses() -> None:
    block = prompt_block()

    for shape in TRIAGE_SHAPES:
        assert shape in block, shape
    # It must not read as a gate, or the model waits for an answer that is
    # never coming.
    assert "Nothing waits on this line" in block


def test_the_prompt_and_the_parser_agree() -> None:
    # The block shows one example form; whatever it shows must parse, or every
    # compliant root produces an unparseable line.
    assert parse_triage("[triage] work: because the file exists") is not None
    assert parse_triage("[triage] conversation: just saying hi") is not None
