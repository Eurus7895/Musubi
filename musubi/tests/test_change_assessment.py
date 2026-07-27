"""Bounded planner change-manifest parsing and reassessment."""

from __future__ import annotations

from agent.change_assessment import Band, assess_manifest, parse_change_manifest

ELEVEN_FILE_MANIFEST = (
    '<change_manifest>{"files_expected":11,"subsystems":'
    '["config","routes","components","styles"],"public_contract":false,'
    '"data_migration":false,"security_sensitive":false,'
    '"external_side_effects":false,"destructive":false,"unknowns":[],'
    '"validation_commands":2}</change_manifest>'
)


def test_manifest_many_files_or_subsystems_is_large() -> None:
    manifest = parse_change_manifest(ELEVEN_FILE_MANIFEST)
    assert manifest is not None
    assert manifest.files_expected == 11
    assert manifest.subsystems == ("components", "config", "routes", "styles")
    result = assess_manifest(manifest)
    assert result.impact is Band.HIGH
    assert result.route == "plan_design_workflow"


def test_manifest_unknowns_require_clarification() -> None:
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":3,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":["deployment target"],'
        '"validation_commands":1}</change_manifest>'
    )
    assert manifest is not None
    assert assess_manifest(manifest).route == "ask_scope"


def test_missing_or_oversized_manifest_fails_closed() -> None:
    assert parse_change_manifest("status: done") is None
    assert parse_change_manifest(
        "<change_manifest>" + "x" * 4097 + "</change_manifest>"
    ) is None


def test_single_file_single_subsystem_manifest_is_simple() -> None:
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["pages"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert manifest is not None
    result = assess_manifest(manifest)
    assert result.route == "single_coder"
    assert result.impact is Band.LOW


def test_unknowns_on_a_one_file_change_are_deferred_not_blocking() -> None:
    # Halting to ask about a palette on a one-file page discards the plan the
    # planner just spent its whole budget producing. The coder defaults them
    # instead; a wrong default costs one turn to redirect.
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["markup"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,'
        '"unknowns":["color palette","typography scale"],'
        '"validation_commands":1}</change_manifest>'
    )
    assert manifest is not None
    result = assess_manifest(manifest)

    assert result.route == "single_coder"
    assert result.clarifying_question is None
    assert result.deferred_unknowns == ("color palette", "typography scale")


def test_unknowns_still_block_a_critical_or_multi_file_change() -> None:
    # The relaxation is scoped to small, cheap-to-redo changes. A critical
    # flag or a multi-file blast radius keeps the fail-closed halt.
    critical = parse_change_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["auth"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":true,"external_side_effects":false,'
        '"destructive":false,"unknowns":["token lifetime"],'
        '"validation_commands":1}</change_manifest>'
    )
    multi_file = parse_change_manifest(
        '<change_manifest>{"files_expected":4,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":["deployment target"],'
        '"validation_commands":1}</change_manifest>'
    )
    assert critical is not None and multi_file is not None

    for manifest in (critical, multi_file):
        result = assess_manifest(manifest)
        assert result.route == "ask_scope"
        assert result.clarifying_question is not None
        assert result.deferred_unknowns == ()


def test_single_file_many_subsystems_is_not_large() -> None:
    # One file is not a large blast radius however many subsystems the planner
    # names inside it — a single HTML page is routinely "markup + styling +
    # content". Escalating it to plan_design_workflow strands the request: the
    # orchestrator may not launch a pipeline, so no coder ever writes the file.
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":1,'
        '"subsystems":["markup","styling","content"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert manifest is not None
    result = assess_manifest(manifest)

    assert result.route == "planner_then_coder_check"
    assert result.impact is not Band.HIGH


def test_single_file_many_subsystems_stays_large_when_flagged() -> None:
    # The relaxation is scoped to the subsystem count alone: a critical flag
    # still dominates, so a one-file security change cannot slip through.
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":1,'
        '"subsystems":["markup","styling","content"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":true,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert manifest is not None
    result = assess_manifest(manifest)

    assert result.route == "plan_design_workflow"
    assert result.risk is Band.HIGH


def test_multi_file_many_subsystems_is_still_large() -> None:
    # Regression guard: the subsystem ceiling still escalates as soon as the
    # change spans more than one file.
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":3,'
        '"subsystems":["routes","components","styles"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert manifest is not None

    assert assess_manifest(manifest).route == "plan_design_workflow"


def test_critical_boolean_manifest_is_large_even_when_small() -> None:
    manifest = parse_change_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["auth"],'
        '"public_contract":false,"data_migration":true,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    assert manifest is not None
    result = assess_manifest(manifest)
    assert result.route == "plan_design_workflow"
    assert result.risk is Band.HIGH


def test_negative_counts_fail_closed() -> None:
    assert parse_change_manifest(
        '<change_manifest>{"files_expected":-1,"subsystems":[],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":0}'
        '</change_manifest>'
    ) is None


def test_non_boolean_critical_flag_fails_closed() -> None:
    # A truthy-looking string flag must NOT coerce to False and slip a critical
    # change past the gate — it fails closed to a rejected manifest.
    assert parse_change_manifest(
        '<change_manifest>{"files_expected":1,"subsystems":["auth"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":"true","external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    ) is None


def test_duplicate_manifest_blocks_fail_closed() -> None:
    # Two blocks (small then corrected-large) are ambiguous: reject rather than
    # silently resolving to the first, smaller manifest.
    small = (
        '<change_manifest>{"files_expected":1,"subsystems":["routes"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":1}'
        '</change_manifest>'
    )
    large = (
        '<change_manifest>{"files_expected":9,"subsystems":["a","b","c"],'
        '"public_contract":false,"data_migration":false,'
        '"security_sensitive":false,"external_side_effects":false,'
        '"destructive":false,"unknowns":[],"validation_commands":2}'
        '</change_manifest>'
    )
    assert parse_change_manifest(small + "\n" + large) is None

def _manifest(payload: str) -> str:
    return f"<change_manifest>{payload}</change_manifest>"


VALID_MANIFEST = (
    '{"files_expected":1,"subsystems":[" routes ","routes"],'
    '"public_contract":false,"data_migration":false,'
    '"security_sensitive":false,"external_side_effects":false,'
    '"destructive":false,"unknowns":[" deployment "],'
    '"validation_commands":1}'
)



def test_manifest_requires_exact_schema_and_exact_json_types() -> None:
    manifest = parse_change_manifest(_manifest(VALID_MANIFEST))
    assert manifest is not None
    assert manifest.subsystems == ("routes",)
    assert manifest.unknowns == ("deployment",)

    malformed = (
        VALID_MANIFEST.replace('"validation_commands":1', '"extra":0,"validation_commands":1'),
        VALID_MANIFEST.replace('"validation_commands":1', ''),
        VALID_MANIFEST.replace('"files_expected":1', '"files_expected":true'),
        VALID_MANIFEST.replace('"validation_commands":1', '"validation_commands":1.0'),
        VALID_MANIFEST.replace('[" routes ","routes"]', '"routes"'),
        VALID_MANIFEST.replace('[" deployment "]', '["   "]'),
        VALID_MANIFEST.replace('"public_contract":false', '"public_contract":0'),
    )
    for payload in malformed:
        assert parse_change_manifest(_manifest(payload)) is None, payload


def test_manifest_rejects_duplicate_json_keys_and_non_finite_numbers() -> None:
    duplicate_key = VALID_MANIFEST.replace(
        '"files_expected":1,', '"files_expected":1,"files_expected":2,',
    )
    non_finite = VALID_MANIFEST.replace('"files_expected":1', '"files_expected":NaN')

    assert parse_change_manifest(_manifest(duplicate_key)) is None
    assert parse_change_manifest(_manifest(non_finite)) is None


def test_manifest_requires_one_well_formed_literal_tag_pair() -> None:
    assert parse_change_manifest(
        f"<change_manifest>{_manifest(VALID_MANIFEST)}</change_manifest>"
    ) is None
    assert parse_change_manifest(
        f"<change_manifest>{VALID_MANIFEST}</change_manifest></change_manifest>"
    ) is None


def test_manifest_byte_limit_is_utf8_not_character_count() -> None:
    # 1,365 three-byte characters plus JSON syntax exceeds the 4,096-byte
    # boundary while remaining below it in Python character count.
    oversized_utf8 = VALID_MANIFEST.replace(
        '" deployment "', f'"{chr(0x20AC) * 1365}"',
    )
    assert len(oversized_utf8) < 4096
    assert len(oversized_utf8.encode("utf-8")) > 4096
    assert parse_change_manifest(_manifest(oversized_utf8)) is None
