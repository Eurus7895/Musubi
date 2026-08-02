"""Validation and canonical freezing for model-authored stage goals.

musubi-tier: substrate
expires-when: never - frozen claims and provenance remain auditable
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from composer import StageRecipe
from skills.skill_loader import SkillMeta
from workspace.grants import RootRegistry

_PATH_CHECKS = frozenset({
    "file_exists", "file_created_or_modified", "dom_count",
    "dom_distinct_text", "dom_text_set",
})
_SELECTOR_RE = re.compile(
    r"^(?:[a-zA-Z][\w-]*)?(?:#[\w-]+|\.[\w-]+|"
    r"\[[\w:-]+(?:=(?:'[^']*'|\"[^\"]*\"|[\w-]+))?\])+$|"
    r"^[a-zA-Z][\w-]*$"
)


@dataclass(frozen=True)
class FrozenStageContract:
    skill_id: str
    goal: str
    exit_when: tuple[Mapping[str, Any], ...]
    required_output_fields: tuple[str, ...]
    canonical_json: str
    contract_hash: str


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def validate_and_freeze_contract(
    raw: Mapping[str, Any],
    recipe: StageRecipe,
    skill_meta: SkillMeta,
    roots: RootRegistry,
    *,
    frozen_contract_hash: str | None = None,
) -> FrozenStageContract:
    """Validate a model declaration against recipe and skill ceilings."""
    if not isinstance(raw, Mapping):
        raise ValueError("stage contract must be an object")
    if frozen_contract_hash is not None:
        echoed = str(raw.get("contract_hash") or "")
        if echoed != frozen_contract_hash:
            raise ValueError("retry contract hash does not match the frozen contract hash")

    skill_id = str(raw.get("skill_id") or "").strip()
    if skill_id != skill_meta.skill_id:
        raise ValueError("skill_id does not match the validated catalog selection")
    goal = str(raw.get("goal") or "").strip()
    predicates = raw.get("exit_when", [])
    if not isinstance(predicates, list):
        raise ValueError("exit_when must be a list")
    if predicates and not goal:
        raise ValueError("goal is required when exit_when is declared")
    if recipe.max_iterations > 1 and not predicates:
        raise ValueError("retryable stages require at least one exit_when predicate")

    allowed = set(recipe.allowed_checks)
    commands = set(recipe.allowed_commands)
    normalized: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    for index, raw_check in enumerate(predicates):
        if not isinstance(raw_check, Mapping):
            raise ValueError(f"exit_when[{index}] must be an object")
        check = dict(raw_check)
        check_type = str(check.get("type") or "").strip()
        if check_type not in allowed:
            raise ValueError(f"check type {check_type!r} is not allowed by the recipe")
        seen_types.add(check_type)
        if check_type in _PATH_CHECKS:
            root = str(check.get("root") or "").strip()
            path = str(check.get("path") or "").strip()
            roots.resolve(root, path)
        if check_type.startswith("dom_"):
            selector = str(check.get("selector") or "").strip()
            if not selector or ":" in selector or not _SELECTOR_RE.fullmatch(selector):
                raise ValueError(f"unsupported static DOM selector {selector!r}")
            equals = check.get("equals")
            if check_type in {"dom_count", "dom_distinct_text"} and (
                isinstance(equals, bool) or not isinstance(equals, int) or equals < 0
            ):
                raise ValueError(f"{check_type}.equals must be a non-negative integer")
            if check_type == "dom_text_set" and not isinstance(equals, list):
                raise ValueError("dom_text_set.equals must be a list")
        if check_type == "named_command":
            command_id = str(check.get("command_id") or "").strip()
            if command_id not in commands:
                raise ValueError(f"command {command_id!r} is not allowed by the recipe")
        normalized.append(check)

    missing = set(skill_meta.completion_contract.required_check_types) - seen_types
    if missing:
        raise ValueError(
            f"contract omits required skill check types: {sorted(missing)}"
        )
    frozen_value = {
        "skill_id": skill_id,
        "skill_version": skill_meta.version,
        "skill_hash": skill_meta.content_hash,
        "goal": goal,
        "exit_when": normalized,
        "required_output_fields": list(
            skill_meta.completion_contract.required_output_fields
        ),
    }
    canonical = _canonical(frozen_value)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return FrozenStageContract(
        skill_id=skill_id,
        goal=goal,
        exit_when=tuple(normalized),
        required_output_fields=skill_meta.completion_contract.required_output_fields,
        canonical_json=canonical,
        contract_hash=digest,
    )
