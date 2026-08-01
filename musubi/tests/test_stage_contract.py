from __future__ import annotations

from pathlib import Path

import pytest

from composer import StageRecipe
from skills.skill_loader import CompletionContract, SkillMeta
from validation.stage_contract import validate_and_freeze_contract
from workspace.grants import RootRegistry


def _recipe() -> StageRecipe:
    return StageRecipe(
        stage="build", agent="coder", preset=None, spawns=(),
        allowed_checks=("file_created_or_modified", "dom_count", "named_command"),
        allowed_commands=("tests",), max_iterations=3,
    )


def _skill() -> SkillMeta:
    return SkillMeta(
        "web-ui", "Web UI", "x", version="1.0.0", content_hash="sha256:abc",
        completion_contract=CompletionContract(
            ("summary",), ("file_created_or_modified",),
        ),
    )


def test_contract_hash_is_canonical_and_merges_skill_requirements(tmp_path: Path) -> None:
    roots = RootRegistry.build(tmp_path)
    raw = {
        "skill_id": "web-ui", "goal": "Build five rows",
        "exit_when": [
            {"path": "index.html", "root": "musubi", "type": "file_created_or_modified"},
            {"type": "dom_count", "root": "musubi", "path": "index.html", "selector": ".row", "equals": 5},
        ],
    }
    first = validate_and_freeze_contract(raw, _recipe(), _skill(), roots)
    second = validate_and_freeze_contract(dict(reversed(list(raw.items()))), _recipe(), _skill(), roots)
    assert first.contract_hash == second.contract_hash
    assert first.required_output_fields == ("summary",)


def test_contract_rejects_disallowed_check_and_path_escape(tmp_path: Path) -> None:
    roots = RootRegistry.build(tmp_path)
    with pytest.raises(ValueError, match="not allowed"):
        validate_and_freeze_contract({
            "skill_id": "web-ui", "goal": "x",
            "exit_when": [{"type": "file_exists", "root": "musubi", "path": "x"}],
        }, _recipe(), _skill(), roots)
    with pytest.raises((ValueError, PermissionError), match="escape"):
        validate_and_freeze_contract({
            "skill_id": "web-ui", "goal": "x",
            "exit_when": [{"type": "file_created_or_modified", "root": "musubi", "path": "../x"}],
        }, _recipe(), _skill(), roots)


def test_retry_must_echo_frozen_hash(tmp_path: Path) -> None:
    roots = RootRegistry.build(tmp_path)
    with pytest.raises(ValueError, match="contract hash"):
        validate_and_freeze_contract(
            {"skill_id": "web-ui", "contract_hash": "sha256:wrong"},
            _recipe(), _skill(), roots, frozen_contract_hash="sha256:right",
        )
