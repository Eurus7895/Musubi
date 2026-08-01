"""Contract tests for the shipped feature-dev recipe."""

from __future__ import annotations

from pathlib import Path

import yaml

import composer

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PIPELINE_YAML = _REPO_ROOT / ".github" / "pipelines" / "feature-dev" / "pipeline.yaml"


def _load() -> dict:
    with _PIPELINE_YAML.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def test_feature_dev_uses_flat_stages_without_runtime_skill_or_correction() -> None:
    cfg = _load()
    assert [entry["stage"] for entry in cfg["stages"]] == [
        "plan", "design", "code", "review",
    ]
    assert "generator" not in cfg
    assert "evaluator" not in cfg
    assert "correction" not in cfg
    assert all("skill" not in entry for entry in cfg["stages"])


def test_feature_dev_code_stage_declares_bounded_acceptance_ceiling() -> None:
    contract = composer.load_pipeline_contract("feature-dev")
    code = next(stage for stage in contract.stages if stage.stage == "code")
    assert code.agent == "coder"
    assert code.max_iterations == 3
    assert code.spawns == ("explorer", "investigator")
    assert code.allowed_checks == (
        "file_exists", "file_created_or_modified", "dom_count",
        "dom_distinct_text", "dom_text_set", "lint_clean",
    )


def test_feature_dev_non_mutating_stages_run_once() -> None:
    contract = composer.load_pipeline_contract("feature-dev")
    assert {stage.stage: stage.max_iterations for stage in contract.stages} == {
        "plan": 1, "design": 1, "code": 3, "review": 1,
    }


def test_feature_dev_baseline_checks_remain_declarative() -> None:
    assert _load().get("baseline_checks") == [{
        "type": "file_read",
        "path": "src/",
        "error": "Cannot read src/ - ensure workspace root contains source.",
    }]
