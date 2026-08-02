"""Bounded model preflight for explicit skill and acceptance selection.

musubi-tier: ephemeral
expires-when: the worker runtime requires model skill selection before work
  tools without a separate model call or a harness-selected default
cost-lever: removes one model call per stage attempt
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from composer import StageRecipe
from skills.skill_loader import SkillMeta
from validation.stage_contract import (
    FrozenStageContract,
    validate_and_freeze_contract,
)
from workspace.grants import RootRegistry

_PREDICATE_SCHEMAS: dict[str, dict[str, Any]] = {
    "file_exists": {
        "required": {"type": "file_exists", "root": "string", "path": "string"},
    },
    "file_created_or_modified": {
        "required": {
            "type": "file_created_or_modified", "root": "string", "path": "string",
        },
    },
    "dom_count": {
        "required": {
            "type": "dom_count", "root": "string", "path": "string",
            "selector": "supported static CSS selector", "equals": "non-negative integer",
        },
    },
    "dom_distinct_text": {
        "required": {
            "type": "dom_distinct_text", "root": "string", "path": "string",
            "selector": "supported static CSS selector", "equals": "non-negative integer",
        },
    },
    "dom_text_set": {
        "required": {
            "type": "dom_text_set", "root": "string", "path": "string",
            "selector": "supported static CSS selector", "equals": "array of strings",
        },
    },
    "lint_clean": {
        "required": {"type": "lint_clean", "paths": "changed"},
        "optional": {"allow_skipped": "boolean"},
    },
    "named_command": {
        "required": {"type": "named_command", "command_id": "string"},
    },
}


@dataclass(frozen=True)
class StagePreflight:
    skill: SkillMeta
    contract: FrozenStageContract
    calls: int


def _response_text(response: Any) -> str:
    return "".join(
        str(block.get("text") or "")
        for block in getattr(response, "content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def run_stage_preflight(
    vendor: Any,
    role: str,
    brief: str,
    catalog: Sequence[SkillMeta],
    recipe: StageRecipe,
    *,
    roots: RootRegistry,
    frozen_contract: FrozenStageContract | None = None,
    failure_evidence: str | None = None,
    budget: Any = None,
    log: Any = None,
    stats: Any = None,
    audit_db_path: Any = None,
    session_id: str | None = None,
    stage: str | None = None,
    attempt: int = 1,
) -> StagePreflight:
    """Ask the model, validate deterministically, and allow one correction."""
    by_id = {skill.skill_id: skill for skill in catalog}
    projection = [
        {
            "skill_id": skill.skill_id,
            "title": skill.title,
            "description": skill.description,
            "version": skill.version,
            "content_hash": skill.content_hash,
            "completion_contract": {
                "required_output_fields": list(
                    skill.completion_contract.required_output_fields
                ),
                "required_check_types": list(
                    skill.completion_contract.required_check_types
                ),
            },
        }
        for skill in catalog
    ]
    predicate_schemas = {
        check_type: _PREDICATE_SCHEMAS[check_type]
        for check_type in recipe.allowed_checks
    }
    if frozen_contract is not None:
        response_contract = {
            "required": ["skill_id", "contract_hash"],
            "omit": ["goal", "exit_when"],
        }
        stage_instruction = (
            "Echo the frozen contract_hash and do not provide goal or exit_when. "
        )
    elif recipe.allowed_checks:
        response_contract = {
            "required": ["skill_id", "goal", "exit_when"],
            "exit_when": "array of predicate objects matching predicate_schemas",
        }
        stage_instruction = (
            "Provide goal and exit_when as an array of predicate objects matching "
            "predicate_schemas. "
        )
    else:
        response_contract = {
            "required": ["skill_id"],
            "omit": ["goal", "exit_when"],
        }
        stage_instruction = (
            "This stage has no acceptance predicates; return skill_id only and "
            "omit goal and exit_when. "
        )
    system = (
        "STAGE PREFLIGHT. Return one JSON object only. You choose exactly one "
        "skill_id from the catalog. The harness does not choose for you. "
        + stage_instruction
        + "Use only the provided root aliases, check types, and command IDs. "
        "You have no tools in this call."
    )
    payload = {
        "role": role, "brief": brief, "catalog": projection,
        "allowed_checks": list(recipe.allowed_checks),
        "allowed_commands": list(recipe.allowed_commands),
        "available_roots": [grant.alias for grant in roots.grants],
        "predicate_schemas": predicate_schemas,
        "response_contract": response_contract,
        "max_iterations": recipe.max_iterations,
        "frozen_contract_hash": (
            frozen_contract.contract_hash if frozen_contract else None
        ),
        "failure_evidence": (failure_evidence or "")[:8192],
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    last_error = "unknown preflight error"
    for call_index in range(2):
        from agent.run import (
            _charge_budget_postflight,
            _check_budget_preflight,
            _cycle_token_usage,
            _estimate_input_tokens,
            _safe_record_agent_cycle,
        )

        input_estimate = _estimate_input_tokens(messages, [])
        _check_budget_preflight(budget, input_estimate, log)
        started = time.time()
        response = vendor.call(messages, [], max_tokens=2048)
        ended = time.time()
        usage = _cycle_token_usage(response, input_estimate)
        lm_ms = max(0, int((ended - started) * 1000))
        if stats is not None:
            stats.record_cycle(
                lm_ms=lm_ms, tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
            )
        _safe_record_agent_cycle(
            db_path=audit_db_path, session_id=session_id,
            worker_id="stage-preflight", stage=stage,
            cycle_idx=(attempt - 1) * 2 + call_index,
            started_at=started, ended_at=ended, lm_ms=lm_ms,
            usage=usage, tool_names=[], text_chars=len(_response_text(response)),
            cycle_status="stage_preflight", log=log,
        )
        _charge_budget_postflight(
            budget, usage.tokens_in + usage.tokens_out, log,
        )
        text = _response_text(response)
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("preflight response must be a JSON object")
            skill_id = str(raw.get("skill_id") or "").strip()
            skill = by_id.get(skill_id)
            if skill is None:
                raise ValueError(f"skill {skill_id!r} is not in the permitted catalog")
            if frozen_contract is not None:
                if raw.get("contract_hash") != frozen_contract.contract_hash:
                    raise ValueError("retry must echo the frozen contract hash")
                required_checks = set(skill.completion_contract.required_check_types)
                frozen_checks = {str(item.get("type")) for item in frozen_contract.exit_when}
                if not required_checks <= frozen_checks:
                    raise ValueError("replacement skill requirements exceed frozen contract")
                return StagePreflight(skill, frozen_contract, call_index + 1)
            contract = validate_and_freeze_contract(raw, recipe, skill, roots)
            return StagePreflight(skill, contract, call_index + 1)
        except (json.JSONDecodeError, ValueError, PermissionError) as exc:
            last_error = str(exc)
            messages.extend([
                {"role": "assistant", "content": text},
                {"role": "user", "content": json.dumps({
                    "error": last_error,
                    "instruction": "Return one corrected JSON object only.",
                    "allowed_checks": list(recipe.allowed_checks),
                    "allowed_commands": list(recipe.allowed_commands),
                    "available_roots": [grant.alias for grant in roots.grants],
                    "predicate_schemas": predicate_schemas,
                    "response_contract": response_contract,
                })},
            ])
    raise RuntimeError(f"stage preflight invalid after correction: {last_error}")
