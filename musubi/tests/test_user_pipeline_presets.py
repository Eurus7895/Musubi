"""User-defined pipelines from presets (Increment 6).

A preset is a reusable worker/stage building block; a pipeline is an ordered
list of presets. Covers: composer resolving a preset-composed pipeline, the
fail-closed catalog validation, and an end-to-end summon of one, with the
evaluator firewall.

The pipeline under test is AUTHORED BY THE TEST rather than shipped. `dev-lite`
used to fill that role, but a sample recipe in `.github/pipelines/` is
indistinguishable from a supported one — it appeared in the console catalog,
in `--pipeline` help, and in the README beside `feature-dev`. The mechanism is
what ships; a recipe is the user's. Building it here proves the same thing
without shipping a recipe nobody asked for.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

import composer as c
from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter

PIPELINE = "preset-flow"


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture()
def preset_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real `.github` catalog with one preset-composed pipeline added.

    Copied rather than written from scratch so the presets, agents and skills
    are the shipped ones — the test exercises composition over the real
    catalog, which is what `dev-lite` used to demonstrate by existing.
    """
    import shutil

    root = _musubi_dir().parent
    shutil.copytree(root / ".github", tmp_path / ".github")
    recipe = tmp_path / ".github" / "pipelines" / PIPELINE
    recipe.mkdir(parents=True)
    (recipe / "pipeline.yaml").write_text(
        f"name: {PIPELINE}\n"
        "description: plan -> build -> check, composed from presets.\n"
        "version: 0.1.0\n"
        "stages:\n"
        "  - preset: plan\n"
        "  - preset: build\n"
        "  - preset: check\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    c.reset_cache()
    try:
        yield tmp_path
    finally:
        c.reset_cache()


# ── composer resolution + validation ────────────────────────────────────────


def test_a_preset_composed_pipeline_resolves(preset_catalog: Path) -> None:
    assert c.active_stages(PIPELINE) == ["plan", "code", "review"]
    assert c.agent_for_stage(PIPELINE, "plan") == "planner"
    assert c.agent_for_stage(PIPELINE, "review") == "reviewer"
    # Evaluator (last stage) is firewalled to the prior stage's output.
    assert c.evaluator_input_stage(PIPELINE) == "code"


def test_shipped_catalog_validates_clean() -> None:
    c.reset_cache()
    assert c.validate_catalog() == []


def test_validate_catalog_rejects_unknown_preset(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    presets = tmp_path / ".github" / "pipelines" / "presets"
    presets.mkdir(parents=True)
    (presets / "plan.yaml").write_text("id: plan\nagent: planner\nstage: plan\n")
    bad = tmp_path / ".github" / "pipelines" / "bad"
    bad.mkdir(parents=True)
    (bad / "pipeline.yaml").write_text(
        "name: bad\nstages:\n  - preset: plan\n  - preset: ghost\n"
    )
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    c.reset_cache()
    try:
        errors = c.validate_catalog()
        assert any("ghost" in e for e in errors), errors
    finally:
        c.reset_cache()


# ── end-to-end: summon a preset-composed pipeline ────────────────────────────


def _text(s: str) -> LMResponse:
    return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": s}])


def _spawn_pipeline(name: str, brief: str) -> LMResponse:
    return LMResponse(stop_reason="tool_use", content=[{
        "type": "tool_use", "id": "pl-1", "name": "musubi_spawn_pipeline",
        "input": {"pipeline_name": name, "brief": brief},
    }])


def _brief_text(messages: list[dict[str, Any]]) -> str | None:
    for m in messages:
        body = m.get("content")
        if isinstance(body, str) and "## Brief" in body:
            return body.split("## Brief", 1)[1]
    return None


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in messages
    )


class PresetFlowRouter(LMRouter):
    name = "presetflow"
    model = "presetflow-1"

    def __init__(self) -> None:
        self.order: list[str] = []
        self.reviewer_brief = ""

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        if any(
            message.get("role") == "system"
            and "STAGE PREFLIGHT" in str(message.get("content"))
            for message in messages
        ):
            payload = json.loads(messages[1]["content"])
            skill = {
                "planner": "request-triage",
                "coder": "python",
                "reviewer": "code-review",
            }[payload["role"]]
            return _text(json.dumps({
                "skill_id": skill,
                "goal": f"complete {payload['role']} stage",
                "exit_when": [],
            }))
        brief = _brief_text(messages)
        if brief is None:
            if _has_tool_result(messages):
                return _text("done")
            return _spawn_pipeline(PIPELINE, "build a thing")
        if "Evaluate the output of the prior stage" in brief:
            self.reviewer_brief = brief
            self.order.append("reviewer")
            return _text("review: PASS")
        if brief.count("### ") == 0:
            self.order.append("planner")
            return _text("plan: step1")
        self.order.append("coder")
        return _text("code: wrote it")


def test_summon_a_preset_composed_pipeline_end_to_end(
    preset_catalog: Path,
) -> None:
    router = PresetFlowRouter()
    answer = asyncio.run(
        run_agent(f"ship via {PIPELINE}", router, _musubi_dir(), log=io.StringIO())
    )

    assert answer == "done"
    assert router.order == ["planner", "coder", "reviewer"]
    # HI #3: the evaluator sees only the build output, not the request or plan.
    assert "code: wrote it" in router.reviewer_brief
    assert "build a thing" not in router.reviewer_brief
    assert "plan: step1" not in router.reviewer_brief
