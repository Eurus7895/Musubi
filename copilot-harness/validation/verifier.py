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

from session import state

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

# ── Phase G.2: schema versioning ──────────────────────────────────────────
#
# OUTPUT_SCHEMAS describes the shape every agent must emit. v2 (G.2)
# adds a `category` field on reviewer issues so the new
# correction.escalate_on_categories rules can match against a
# structured value instead of regex-matching the description.
#
# Old `stage_outputs` rows tagged `schema_version='v1'` are migrated
# on read by `validation/schema_migrations.py` — pre-G.2 reviewer
# rows get `category='other'` filled in for each issue.

CURRENT_SCHEMA_VERSION = "v2"

# Allowed values for `issues[].category` in v2. Reviewer's prompt
# instructs it to pick one of these. `other` is the safety valve when
# a finding doesn't fit cleanly.
REVIEWER_CATEGORY_ENUM: frozenset[str] = frozenset({
    "security",
    "data-loss",
    "performance",
    "style",
    "correctness",
    "breaking-change",
    "other",
})

OUTPUT_SCHEMAS_V1: dict[str, dict[str, Any]] = {
    "planner": {
        "required": ["summary", "tasks"],
        "types": {
            "summary": str, "tasks": list,
            "required_skills": list, "open_questions": list, "confidence": str,
        },
    },
    "designer": {
        "required": ["summary", "tasks_addressed", "modules"],
        "types": {
            "summary": str, "tasks_addressed": list, "modules": list,
            "data_schemas": list, "dependencies": list,
            "integration_notes": str, "confidence": str,
        },
    },
    "coder": {
        "required": ["summary", "files_modified"],
        "types": {
            "summary": str, "files_modified": list, "file_contents": dict,
            "implementation_notes": str, "confidence": str,
        },
    },
    "reviewer": {
        "required": ["status", "attempt", "issues"],
        "types": {
            "status": str, "attempt": int, "issues": list,
            # Documented nullable in reviewer.agent.md: the reviewer emits
            # `"escalate_reason": null` when status is pass/fail.
            "escalate_reason": (str, type(None)),
        },
        "status_values": {"pass", "fail", "escalate", "wrong_plan"},
    },
}

OUTPUT_SCHEMAS_V2: dict[str, dict[str, Any]] = {
    # Other agents identical to v1; copies kept explicit so a future
    # divergence is a visible diff rather than a forgotten alias.
    "planner":  dict(OUTPUT_SCHEMAS_V1["planner"]),
    "designer": dict(OUTPUT_SCHEMAS_V1["designer"]),
    "coder":    dict(OUTPUT_SCHEMAS_V1["coder"]),
    "reviewer": {
        "required": ["status", "attempt", "issues"],
        "types": {
            "status": str, "attempt": int, "issues": list,
            "escalate_reason": (str, type(None)),
        },
        "status_values": {"pass", "fail", "escalate", "wrong_plan"},
        # G.2: every issue now requires a `category` field; value must
        # be one of REVIEWER_CATEGORY_ENUM.
        "issue_category_required": True,
        "issue_category_enum": REVIEWER_CATEGORY_ENUM,
    },
}

_SCHEMAS_BY_VERSION: dict[str, dict[str, dict[str, Any]]] = {
    "v1": OUTPUT_SCHEMAS_V1,
    "v2": OUTPUT_SCHEMAS_V2,
}

# Back-compat alias — existing callers (`_check_schema`,
# `_check_reviewer_issues`) read `OUTPUT_SCHEMAS[agent]`. They now
# transparently get the current-version schema.
OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = _SCHEMAS_BY_VERSION[CURRENT_SCHEMA_VERSION]


def schemas_for_version(version: str) -> dict[str, dict[str, Any]]:
    """Return the agent → schema map for a specific schema version.

    Used by tests and by future migrate-then-validate flows that need
    to validate against the source-version schema before running the
    upgrade migration."""
    if version not in _SCHEMAS_BY_VERSION:
        raise ValueError(
            f"Unknown schema version {version!r}. "
            f"Known: {sorted(_SCHEMAS_BY_VERSION)}"
        )
    return _SCHEMAS_BY_VERSION[version]


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
            if isinstance(expected_type, tuple):
                expected_name = " | ".join(t.__name__ for t in expected_type)
            else:
                expected_name = expected_type.__name__
            errors.append(f"Field '{key}' must be {expected_name}, got {actual}")

    if "status_values" in schema and "status" in output:
        if output["status"] not in schema["status_values"]:
            errors.append(
                f"Field 'status' must be one of {sorted(schema['status_values'])}, "
                f"got '{output['status']}'"
            )

    return errors


# ── Reviewer issues nested validation ────────────────────────────────────────

_ISSUE_REQUIRED = {"severity", "description", "fix_instruction"}
_VALID_SEVERITIES = {"critical", "high", "medium", "low"}
# Only these severities are allowed to drive a status=fail retry. Anything
# else is advisory — see normalize_reviewer_status().
_FAIL_TRIGGERING_SEVERITIES = {"critical", "high"}


def normalize_reviewer_status(output: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Enforce the severity rubric on reviewer output.

    Rule (documented in reviewer.agent.md + code-review/SKILL.md):
      - critical | high  → force fail / escalate; coder must retry
      - medium  | low    → advisory; recorded but does not fail the review

    If the reviewer submits status="fail" with no critical/high issue, the
    harness coerces it to "pass" and records the coercion in a synthetic
    `status_coerced_from` field. The issues list is preserved so medium/low
    findings still reach the user.

    Returns (possibly-mutated output, coerced_bool). Does NOT mutate `escalate` or
    `wrong_plan` — both are deliberate reviewer decisions that can stand on
    severity-independent grounds.
    """
    if not isinstance(output, dict):
        return output, False
    if output.get("status") != "fail":
        return output, False

    issues = output.get("issues") or []
    if not isinstance(issues, list):
        return output, False

    severities = {
        (i.get("severity") or "").lower()
        for i in issues
        if isinstance(i, dict)
    }
    if severities & _FAIL_TRIGGERING_SEVERITIES:
        return output, False  # genuine fail, leave alone

    # No critical/high present — rubric says this is a pass.
    coerced = dict(output)
    coerced["status"] = "pass"
    coerced["status_coerced_from"] = "fail"
    coerced["status_coercion_reason"] = (
        "Severity rubric: status=fail requires at least one critical or "
        "high issue. Only medium/low issues were present; coerced to pass."
    )
    return coerced, True


def _check_reviewer_issues(output: dict[str, Any]) -> list[str]:
    """Validate each item in the reviewer's issues array.

    v1 (pre-G.2): severity + description + fix_instruction required.
    v2 (G.2):     above plus `category` ∈ REVIEWER_CATEGORY_ENUM.

    Validation always runs against the CURRENT_SCHEMA_VERSION's
    reviewer schema. Older v1-stored rows are migrated to v2 BEFORE
    they reach the validator on read — see
    `validation/schema_migrations._migrate_reviewer_v1_to_v2`.
    """
    issues = output.get("issues", [])
    if not isinstance(issues, list):
        return []
    reviewer_schema = OUTPUT_SCHEMAS.get("reviewer", {})
    require_category = reviewer_schema.get("issue_category_required", False)
    category_enum = reviewer_schema.get("issue_category_enum") or frozenset()
    errors: list[str] = []
    for i, item in enumerate(issues):
        if not isinstance(item, dict):
            errors.append(f"issues[{i}] must be an object, got {type(item).__name__}")
            continue
        for field in _ISSUE_REQUIRED:
            if field not in item:
                errors.append(f"issues[{i}] missing required field: '{field}'")
        sev = item.get("severity")
        if sev is not None and sev not in _VALID_SEVERITIES:
            errors.append(
                f"issues[{i}].severity must be one of {sorted(_VALID_SEVERITIES)}, got '{sev}'"
            )
        if require_category:
            cat = item.get("category")
            if cat is None:
                errors.append(f"issues[{i}] missing required field: 'category'")
            elif cat not in category_enum:
                errors.append(
                    f"issues[{i}].category must be one of {sorted(category_enum)}, got {cat!r}"
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


def _check_coder_file_contents(code_output: dict[str, Any]) -> list[str]:
    """file_contents must cover every path in files_modified with non-empty strings.

    This is the main guard against the model returning stub/empty implementations.
    A missing or empty file_contents means no code gets written to disk.
    """
    modified = code_output.get("files_modified", [])
    if not isinstance(modified, list) or not modified:
        return []

    contents = code_output.get("file_contents")

    if contents is None:
        return [
            "file_contents is required when files_modified is non-empty. "
            "Include the complete content of every modified file."
        ]

    if not isinstance(contents, dict):
        return ["file_contents must be an object mapping file path → string content"]

    errors: list[str] = []
    for fpath in modified:
        if not isinstance(fpath, str):
            continue
        if fpath not in contents:
            errors.append(f"file_contents missing entry for '{fpath}' listed in files_modified")
        elif not isinstance(contents[fpath], str) or not contents[fpath].strip():
            errors.append(f"file_contents['{fpath}'] is empty — must contain complete file content")
    return errors


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

    # Reviewer nested issues validation (runs regardless of session context).
    if agent_name.lower() == "reviewer" and isinstance(output, dict):
        errors.extend(_check_reviewer_issues(output))

    # Coder: file_contents coverage check runs regardless of session context —
    # it only examines the coder output itself, not cross-stage data.
    if agent_name.lower() == "coder" and isinstance(output, dict):
        errors.extend(_check_coder_file_contents(output))

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


# ── Sub-agent result verification (Phase A.2) ────────────────────────────────
#
# Sub-agents return a `summary` string and an optional `structured` payload.
# The harness applies two checks here, both deterministic:
#   1. Token cap on the summary — runaway summaries blow the parent's
#      context window, which is exactly what sub-agents exist to avoid.
#      Over-cap text is truncated with a single explicit marker so the
#      parent agent can detect the truncation.
#   2. Optional JSON-shape check on `structured` — the parent specifies
#      an `output_schema` at spawn time when it expects a particular
#      shape (e.g. orchestrator asking explorer for `{matches: list}`).
#      Malformed output is rejected, not silently accepted.

# Char-per-token approximation. Tiktoken would be more precise but pulls
# a heavy dep; 4 chars/token is the standard rule-of-thumb the GPT-4
# token estimator emits within 20% across English prose.
_CHARS_PER_TOKEN: int = 4
_TRUNCATION_MARKER: str = "\n\n[truncated by harness — exceeded max_tokens cap]"

DEFAULT_SUBAGENT_MAX_TOKENS: int = 2000


@dataclass
class SubagentVerifyResult:
    """Outcome of `verify_subagent_summary`.

    `summary` is the (possibly truncated) text safe to hand back to the
    parent. `truncated` records whether the cap fired so the parent /
    extension can render a chat marker. `errors` lists schema problems
    on the structured payload — non-empty errors mean `valid` is False
    and the runner should mark the sub-session 'failed'.
    """
    valid: bool
    summary: str
    truncated: bool
    errors: list[str] = field(default_factory=list)


def _truncate_to_token_budget(
    text: str, max_tokens: int
) -> tuple[str, bool]:
    """Return (possibly-truncated text, did_truncate)."""
    if max_tokens <= 0:
        return _TRUNCATION_MARKER.lstrip(), True
    char_cap = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= char_cap:
        return text, False
    # Reserve space for the marker so the final string is <= char_cap +
    # marker. A character-cap counted in "tokens" is approximate anyway;
    # the marker keeps the output legible.
    reserved = char_cap - len(_TRUNCATION_MARKER)
    if reserved <= 0:
        return _TRUNCATION_MARKER.lstrip(), True
    return text[:reserved] + _TRUNCATION_MARKER, True


# Schema type names accepted in JSON-encoded schemas (the extension cannot
# encode Python `type` objects, so it sends names). Mirrors typing in
# OUTPUT_SCHEMAS but reachable via JSON.
_TYPE_NAME_MAP: dict[str, type | tuple[type, ...]] = {
    "str":    str,
    "string": str,
    "int":    int,
    "float":  (int, float),
    "number": (int, float),
    "bool":   bool,
    "list":   list,
    "array":  list,
    "dict":   dict,
    "object": dict,
    "null":   type(None),
}


def _coerce_type(t: Any) -> type | tuple[type, ...] | None:
    """Accept either a Python `type` (in-process callers) or a string
    (JSON-encoded schemas from the extension)."""
    if isinstance(t, type):
        return t
    if isinstance(t, tuple) and all(isinstance(x, type) for x in t):
        return t
    if isinstance(t, str):
        return _TYPE_NAME_MAP.get(t.lower())
    if isinstance(t, list):
        coerced: list[type] = []
        for x in t:
            mapped = _TYPE_NAME_MAP.get(x.lower()) if isinstance(x, str) else x
            if isinstance(mapped, type):
                coerced.append(mapped)
            elif isinstance(mapped, tuple):
                coerced.extend(mapped)
        return tuple(coerced) if coerced else None
    return None


def _type_label(t: Any) -> str:
    if isinstance(t, type):
        return t.__name__
    if isinstance(t, tuple):
        return " | ".join(getattr(x, "__name__", str(x)) for x in t)
    return str(t)


def _check_structured_against_schema(
    structured: Any, schema: dict[str, Any]
) -> list[str]:
    """Lightweight structural match. Same shape as `OUTPUT_SCHEMAS` above.

    Schema keys:
      - required: list[str]
      - types:    dict[str, type | tuple | str]   string names supported so
                  the extension can JSON-encode the schema (e.g. "int").
      - enum:     dict[str, set | list]   (optional — value-set check)
    """
    if not isinstance(structured, dict):
        return [
            f"structured must be a JSON object, got {type(structured).__name__}"
        ]
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in structured:
            errors.append(f"structured missing required field: {key!r}")
    for key, raw_type in schema.get("types", {}).items():
        coerced = _coerce_type(raw_type)
        if coerced is None:
            # An unparseable type entry is a schema-author error, not a
            # sub-agent error — surface it but don't block the run.
            errors.append(
                f"schema.types.{key} unrecognised type spec: {raw_type!r}"
            )
            continue
        if key in structured and not isinstance(structured[key], coerced):
            errors.append(
                f"structured.{key} must be {_type_label(coerced)}, "
                f"got {type(structured[key]).__name__}"
            )
    for key, allowed in schema.get("enum", {}).items():
        allowed_seq = list(allowed) if not isinstance(allowed, list) else allowed
        if key in structured and structured[key] not in allowed_seq:
            errors.append(
                f"structured.{key} must be one of {sorted(allowed_seq)}, "
                f"got {structured[key]!r}"
            )
    return errors


def verify_subagent_summary(
    summary: str | None,
    structured: Any | None = None,
    *,
    max_tokens: int = DEFAULT_SUBAGENT_MAX_TOKENS,
    schema: dict[str, Any] | None = None,
) -> SubagentVerifyResult:
    """Sanitise + validate a sub-agent's terminal result.

    1. `summary` is coerced to a string; over-cap text is truncated with
       an explicit marker (`_TRUNCATION_MARKER`) so the caller can see
       the cut.
    2. `summary` is also scanned for instruction-injection attempts and
       leaked secrets. Either is a hard-fail — the runner must not
       forward such content to the parent.
    3. `structured`, when present, must be a JSON object matching `schema`
       (when given). `schema` shape mirrors `OUTPUT_SCHEMAS` —
       `{required, types, enum}` — so callers don't need an extra dep.

    Used by `harness_complete_subagent` so the runner cannot bypass the
    cap by handing the harness an oversize string.
    """
    raw = "" if summary is None else str(summary)
    safe_summary, truncated = _truncate_to_token_budget(raw, max_tokens)

    errors: list[str] = []

    # Reuse the secrets scanner from validate(): a sub-agent dumping a
    # private key into its summary is the same threat as a coder doing it.
    for hit in _scan_secrets(safe_summary):
        errors.append(f"sub-agent summary contains potential secret: {hit}")

    # Instruction-injection in the summary would propagate up to the
    # parent's prompt. Re-use the scanner from context_builder.
    from validation.context_builder import scan_injection
    if scan_injection(safe_summary):
        errors.append(
            "sub-agent summary contains instruction-injection patterns"
        )

    if structured is not None and schema is not None:
        errors.extend(_check_structured_against_schema(structured, schema))

    return SubagentVerifyResult(
        valid=not errors,
        summary=safe_summary,
        truncated=truncated,
        errors=errors,
    )
