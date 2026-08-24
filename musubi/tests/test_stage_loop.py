from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from agent.pipeline_runner import run_pipeline
from agent.vendors.base import LMResponse, LMRouter
from skills import skill_loader  # import before MUSUBI_ROOT is redirected
from storage import db


class RetryRouter(LMRouter):
    name = "retry"
    model = "retry"

    def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
        payload = json.loads(messages[1]["content"])
        if payload.get("frozen_contract_hash"):
            value = {
                "skill_id": "web-ui",
                "contract_hash": payload["frozen_contract_hash"],
            }
        else:
            value = {
                "skill_id": "web-ui", "goal": "render five distinct cities",
                "exit_when": [
                    {"type": "file_created_or_modified", "root": "musubi", "path": "marker.txt"},
                    {"type": "dom_count", "root": "musubi", "path": "index.html", "selector": "[data-testid='weather-row']", "equals": 5},
                    {"type": "dom_distinct_text", "root": "musubi", "path": "index.html", "selector": "[data-testid='city-name']", "equals": 5},
                ],
            }
        return LMResponse("end_turn", [{"type": "text", "text": json.dumps(value)}])


def test_pipeline_retries_same_frozen_contract_then_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("MUSUBI_ROOT", str(tmp_path))
    state_path = tmp_path / "state.db"
    audit_path = tmp_path / "audit.db"
    db.init_db(state_path)
    db.insert_session("pipe-retry", "five-city weather table", "2026-08-01T00:00:00+00:00", state_path)
    db.insert_stage("pipe-retry", "code", 1, state_path)
    (tmp_path / "index.html").write_text(
        "<table><tr data-testid='weather-row'><td data-testid='city-name'>Hanoi</td></tr></table>",
        encoding="utf-8",
    )
    spawns: list[dict[str, Any]] = []
    finalizations: list[dict[str, Any]] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned", "pipeline_session_id": "pipe-retry",
                "pipeline_name": "feature-dev",
                "plan": [{"stage": "code", "role": "coder"}],
            })
        if name == "musubi_spawn_pipeline_stage":
            spawns.append(args)
            return json.dumps({
                "status": "spawned", "handle_id": f"h-{len(spawns)}",
                "role": "coder", "allowed_tools": [],
                "max_turns": args["max_turns"],
            })
        if name == "musubi_get_subagent_context":
            return json.dumps({
                "status": "ok", "role_skill": "python skill", "allowed_tools": [],
            })
        if name == "musubi_complete_subagent":
            return json.dumps({"status": "ok"})
        if name == "musubi_finalize_pipeline_run":
            finalizations.append(args)
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    calls = 0

    async def fake_run_unit(*args: Any, **kwargs: Any) -> tuple[str, int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            (tmp_path / "marker.txt").write_text("created", encoding="utf-8")
        else:
            rows = "".join(
                f"<tr data-testid='weather-row'><td data-testid='city-name'>{city}</td></tr>"
                for city in ("Hanoi", "Hue", "Danang", "Saigon", "Can Tho")
            )
            (tmp_path / "index.html").write_text(f"<table>{rows}</table>", encoding="utf-8")
        return json.dumps({
            "summary": f"attempt {calls}", "artifacts": ["index.html"],
        }), 1

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    monkeypatch.setattr("agent.run.run_unit", fake_run_unit)
    result = asyncio.run(run_pipeline(
        None,
        {"parent_session_id": "outer", "parent_agent_name": "agent",
         "pipeline_name": "feature-dev", "brief": "make result"},
        RetryRouter(), [], io.StringIO(), strict=True,
        compression_db_path=state_path, audit_db_path=audit_path,
    ))

    assert '"summary": "attempt 2"' in result
    assert len(spawns) == 2
    assert {spawn["pushed_skill_id"] for spawn in spawns} == {"web-ui"}
    assert finalizations[-1]["final_status"] == "success"
    events = db.get_stage_attempt_events(
        db.StageAttemptIdentity("pipe-retry", "code", 2), db_path=state_path,
    )
    assert events[-1]["event"] == "gate_verdict"
    assert events[-1]["detail"]["status"] == "pass"
    gate = json.loads(db.get_stage_row("pipe-retry", "code", 2, state_path)["gate_result_json"])
    assert gate["status"] == "pass"
    assert [check["evidence"].get("actual") for check in gate["checks"][1:]] == [5, 5]


def test_restore_frozen_contract_uses_first_persisted_hash() -> None:
    from agent.pipeline_runner import _restore_frozen_contracts

    canonical = json.dumps({
        "skill_id": "web-ui", "skill_version": "1", "skill_hash": "sha256:s",
        "goal": "keep the goal", "exit_when": [],
    }, sort_keys=True, separators=(",", ":"))
    contract_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    restored = _restore_frozen_contracts([
        {"stage": "code", "attempt": 1, "contract_json": canonical,
         "contract_hash": contract_hash},
        {"stage": "code", "attempt": 2, "contract_json": canonical,
         "contract_hash": contract_hash},
    ])

    assert restored["code"].goal == "keep the goal"
    assert restored["code"].contract_hash == contract_hash


def test_strict_recipe_failure_finalizes_spawned_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizations: list[dict[str, Any]] = []

    async def fake_call(session: Any, name: str, args: dict[str, Any]) -> str:
        if name == "musubi_spawn_pipeline":
            return json.dumps({
                "status": "spawned", "pipeline_session_id": "bad-recipe",
                "pipeline_name": "feature-dev",
                "plan": [{"stage": "code", "role": "coder"}],
            })
        if name == "musubi_finalize_pipeline_run":
            finalizations.append(args)
            return json.dumps({"status": "ok"})
        raise AssertionError(name)

    monkeypatch.setattr("agent.run._call_tool_text", fake_call)
    monkeypatch.setattr(
        "composer.load_pipeline_contract",
        lambda name: (_ for _ in ()).throw(
            __import__("composer").PipelineRecipeError("invalid ceiling")
        ),
    )

    with pytest.raises(RuntimeError, match="strict recipe loading failed"):
        asyncio.run(run_pipeline(
            None,
            {"parent_session_id": "root", "parent_agent_name": "agent",
             "pipeline_name": "feature-dev", "brief": "build"},
            RetryRouter(), [], io.StringIO(), strict=True,
        ))

    assert finalizations[-1]["final_status"] == "aborted"
