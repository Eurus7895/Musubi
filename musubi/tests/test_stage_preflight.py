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


class SchemaAwareRouter(LMRouter):
    model = "test"
    name = "test"

    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        self.calls += 1
        payload = json.loads(messages[-1]["content"])
        schemas = payload.get("predicate_schemas", {})
        valid = {
            "skill_id": "web-ui", "goal": "write page",
            "exit_when": [{
                "type": "file_created_or_modified",
                "root": "musubi", "path": "index.html",
            }],
        }
        value = (
            {"skill_id": "web-ui", "goal": "write page",
             "exit_when": ["file_created_or_modified"]}
            if (self.fail_first and self.calls == 1)
            or "file_created_or_modified" not in schemas
            else valid
        )
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


def _text_meta() -> SkillMeta:
    return SkillMeta(
        "planning", "Planning", "x", version="1", content_hash="sha256:b",
        completion_contract=CompletionContract(("summary",), ()),
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


def test_preflight_accepts_skill_only_for_stage_without_checks(tmp_path: Path) -> None:
    recipe = StageRecipe("plan", "planner", None, (), (), (), 1)

    result = run_stage_preflight(
        Router([{"skill_id": "planning"}]),
        "planner", "plan it", [_text_meta()], recipe,
        roots=RootRegistry.build(tmp_path),
    )

    assert result.skill.skill_id == "planning"
    assert result.contract.goal == ""
    assert result.contract.exit_when == ()


def test_preflight_supplies_predicate_shapes_on_first_call(tmp_path: Path) -> None:
    router = SchemaAwareRouter()

    result = run_stage_preflight(
        router, "coder", "build", [_meta()], _recipe(),
        roots=RootRegistry.build(tmp_path),
    )

    assert result.contract.exit_when[0]["type"] == "file_created_or_modified"
    assert router.calls == 1


def test_preflight_repeats_predicate_shapes_for_correction(tmp_path: Path) -> None:
    router = SchemaAwareRouter(fail_first=True)

    result = run_stage_preflight(
        router, "coder", "build", [_meta()], _recipe(),
        roots=RootRegistry.build(tmp_path),
    )

    assert result.contract.exit_when[0]["path"] == "index.html"
    assert router.calls == 2
