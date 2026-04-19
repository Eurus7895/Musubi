"""Output validation — schema checks, secrets scan, cross-stage contracts.

Zero LLM calls. All checks are regex / structural Python.

Public API:
    validate(output, agent_name, session_id?, db_path?) → ValidationResult
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import state

# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True)

    @classmethod
    def failed(cls, errors: list[str]) -> "ValidationResult":
        return cls(valid=False, errors=errors)


# ── Output schemas ────────────────────────────────────────────────────────────
# Lightweight: required top-level keys + expected Python types.
# Avoids jsonschema dependency — stays zero-dep beyond stdlib + mcp.

OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "planner": {
        "required": ["summary", "tasks"],
        "types": {"summary": str, "tasks": list},
    },
    "designer": {
        "required": ["summary", "tasks_addressed", "modules"],
        "types": {"summary": str, "tasks_addressed": list, "modules": list},
    },
    "coder": {
        "required": ["summary", "files_modified"],
        "types": {"summary": str, "files_modified": list},
    },
    "reviewer": {
        "required": ["status", "attempt", "issues"],
        "types": {"status": str, "attempt": int, "issues": list},
        "status_values": {"pass", "fail", "escalate", "wrong_plan"},
    },
}


# ── Secrets patterns ──────────────────────────────────────────────────────────

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key",  re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII)),
    ("private key",     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token",    re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("GitHub PAT",      re.compile(r"github_pat_[A-Za-z0-9_]{82}")),
    ("generic API key", re.compile(r"(?i)api[_-]?key\s*[:=]\s*[A-Za-z0-9]{20,}")),
    ("bearer token",    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
]


def _scan_secrets(text: str) -> list[str]:
    """Return human-readable labels for each secret pattern matched in text."""
    return [label for label, pattern in _SECRET_PATTERNS if pattern.search(text)]


# ── Schema check ──────────────────────────────────────────────────────────────


def _check_schema(output: Any, agent_name: str) -> list[str]:
    """Return schema error messages; empty list if output is valid."""
    schema = OUTPUT_SCHEMAS.get(agent_name.lower())
    if schema is None:
        return []

    if not isinstance(output, dict):
        return [f"Output must be a JSON object, got {type(output).__name__}"]

    errors: list[str] = []

    for key in schema["required"]:
        if key not in output:
            errors.append(f"Missing required field: '{key}'")

    for key, expected_type in schema.get("types", {}).items():
        if key in output and not isinstance(output[key], expected_type):
            actual = type(output[key]).__name__
            errors.append(f"Field '{key}' must be {expected_type.__name__}, got {actual}")

    if "status_values" in schema and "status" in output:
        if output["status"] not in schema["status_values"]:
            errors.append(
                f"Field 'status' must be one of {sorted(schema['status_values'])}, "
                f"got '{output['status']}'"
            )

    return errors


# ── Cross-stage contract validation ───────────────────────────────────────────


def _check_design_references_plan_tasks(
    design_output: dict[str, Any], plan_output: dict[str, Any] | None
) -> list[str]:
    """Every plan task ID must appear in design's tasks_addressed list."""
    if plan_output is None:
        return []
    task_ids = {
        t["id"]
        for t in plan_output.get("tasks", [])
        if isinstance(t, dict) and "id" in t
    }
    if not task_ids:
        return []
    addressed = set(design_output.get("tasks_addressed", []))
    missing = sorted(task_ids - addressed)
    if missing:
        return [f"Design does not reference plan task IDs: {missing}"]
    return []


def _check_code_only_modifies_declared_files(
    code_output: dict[str, Any], design_output: dict[str, Any] | None
) -> list[str]:
    """Code must only modify files that were declared in design."""
    if design_output is None:
        return []
    declared = {
        m["file"]
        for m in design_output.get("modules", [])
        if isinstance(m, dict) and "file" in m
    }
    if not declared:
        return []
    modified = code_output.get("files_modified", [])
    if not isinstance(modified, list):
        return []
    undeclared = sorted(f for f in modified if f not in declared)
    if undeclared:
        return [f"Code modifies files not declared in design: {undeclared}"]
    return []


# ── Public API ────────────────────────────────────────────────────────────────


def validate(
    output: Any,
    agent_name: str,
    session_id: str | None = None,
    db_path: Path | None = None,
) -> ValidationResult:
    """Validate agent output. Returns ValidationResult with all errors found.

    Checks (in order):
      1. Schema — required fields + types (+ status enum for reviewer)
      2. Secrets scan — regex patterns for API keys, tokens, private keys
      3. Cross-stage contracts — design refs plan tasks; code uses declared files
    """
    errors: list[str] = []

    errors.extend(_check_schema(output, agent_name))

    text = json.dumps(output) if isinstance(output, (dict, list)) else str(output)
    for hit in _scan_secrets(text):
        errors.append(f"Potential secret detected: {hit}")

    # Cross-stage checks only run when session context is available and no
    # earlier errors would make the output unusable anyway.
    if session_id and not errors and isinstance(output, dict):
        agent = agent_name.lower()
        if agent == "designer":
            plan = state.read_stage(session_id, "plan", db_path)
            errors.extend(_check_design_references_plan_tasks(output, plan))
        elif agent == "coder":
            design = state.read_stage(session_id, "design", db_path)
            errors.extend(_check_code_only_modifies_declared_files(output, design))

    return ValidationResult.failed(errors) if errors else ValidationResult.ok()
