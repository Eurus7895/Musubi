from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.stage_preflight import run_stage_preflight
from agent.vendors.base import LMResponse, LMRouter
from composer import StageRecipe
from skills.skill_loader import CompletionContract, SkillMeta
from workspace.grants import RootRegistry


class Router(LMRouter):
    model = "test"
    name = "test"

    def __init__(self, values: list[dict]) -> None:
        self.values = values
        self.calls = 0

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        value = self.values[self.calls]
        self.calls += 1
        return LMResponse("end_turn", [{"type": "text", "text": json.dumps(value)}])


def _recipe() -> StageRecipe:
    return StageRecipe(
        "build", "coder", None, (),
        ("file_created_or_modified",), (), 3,
    )


def _meta() -> SkillMeta:
    return SkillMeta(
        "web-ui", "Web UI", "x", version="1", content_hash="sha256:a",
        completion_contract=CompletionContract((), ("file_created_or_modified",)),
    )


def _valid() -> dict:
    return {
        "skill_id": "web-ui", "goal": "write page",
        "exit_when": [{
            "type": "file_created_or_modified", "root": "musubi", "path": "index.html",
        }],
    }


def test_preflight_accepts_model_choice_and_contract(tmp_path: Path) -> None:
    result = run_stage_preflight(
        Router([_valid()]), "coder", "build it", [_meta()], _recipe(),
        roots=RootRegistry.build(tmp_path),
    )
    assert result.skill.skill_id == "web-ui"
    assert result.contract.contract_hash.startswith("sha256:")


def test_preflight_allows_one_correction_then_fails(tmp_path: Path) -> None:
    router = Router([{"skill_id": "missing"}, _valid()])
    assert run_stage_preflight(
        router, "coder", "build", [_meta()], _recipe(),
        roots=RootRegistry.build(tmp_path),
    ).skill.skill_id == "web-ui"
    assert router.calls == 2

    with pytest.raises(RuntimeError, match="invalid after correction"):
        run_stage_preflight(
            Router([{}, {}]), "coder", "build", [_meta()], _recipe(),
            roots=RootRegistry.build(tmp_path),
        )
