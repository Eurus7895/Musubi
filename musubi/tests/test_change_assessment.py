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
