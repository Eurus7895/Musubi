"""User-defined pipelines from presets (Increment 6).

A preset is a reusable worker/stage building block; a pipeline is an ordered
list of presets. Covers: composer resolving a preset-composed pipeline, the
fail-closed catalog validation, and an end-to-end summon of the shipped
`dev-lite` user pipeline (plan → build → check) with the evaluator firewall.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import composer as c
from agent.run import run_agent
from agent.vendors.base import LMResponse, LMRouter


def _musubi_dir() -> Path:
    return Path(__file__).resolve().parent.parent


# ── composer resolution + validation ────────────────────────────────────────


def test_dev_lite_resolves_from_presets() -> None:
    c.reset_cache()
    assert c.active_stages("dev-lite") == ["plan", "code", "review"]
    assert c.agent_for_stage("dev-lite", "plan") == "planner"
    assert c.agent_for_stage("dev-lite", "review") == "reviewer"
    # Evaluator (last stage) is firewalled to the prior stage's output.
    assert c.evaluator_input_stage("dev-lite") == "code"


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


# ── end-to-end: summon the user-defined dev-lite pipeline ────────────────────


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


class DevLiteRouter(LMRouter):
    name = "devlite"
    model = "devlite-1"

    def __init__(self) -> None:
        self.order: list[str] = []
        self.reviewer_brief = ""

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        brief = _brief_text(messages)
        if brief is None:
            if _has_tool_result(messages):
                return _text("done")
            return _spawn_pipeline("dev-lite", "build a thing")
        if "Evaluate the output of the prior stage" in brief:
            self.reviewer_brief = brief
            self.order.append("reviewer")
            return _text("review: PASS")
        if brief.count("### ") == 0:
            self.order.append("planner")
            return _text("plan: step1")
        self.order.append("coder")
        return _text("code: wrote it")


def test_summon_user_defined_dev_lite_pipeline_end_to_end() -> None:
    c.reset_cache()
    router = DevLiteRouter()
    answer = asyncio.run(run_agent("ship via dev-lite", router, _musubi_dir(), log=io.StringIO()))

    assert answer == "done"
    assert router.order == ["planner", "coder", "reviewer"]
    # HI #3: the evaluator sees only the build output, not the request or plan.
    assert "code: wrote it" in router.reviewer_brief
    assert "build a thing" not in router.reviewer_brief
    assert "plan: step1" not in router.reviewer_brief
