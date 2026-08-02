"""Deterministic enforcement of a planner-declared change manifest.

musubi-tier: substrate
expires-when: never - arithmetic over an LLM-declared blast radius is
  governance, not a compensation for a weak model. A stronger planner makes
  the DECLARATION better; it does not remove the need to check it.

This file judges nothing about English. Its input is the nine-field JSON a
planner emits after reading the code, and its output follows from counting:
files against a ceiling, subsystems against a ceiling, critical flags against
a deny rule. That is why it survives the deletion of `agent/scope.py`, which
answers the same question by pattern-matching the user's sentence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from agent.routes import RouteKind


class Band(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChangeAssessment:
    ambiguity: Band
    impact: Band
    risk: Band
    route: str
    evidence: tuple[str, ...]
    clarifying_question: str | None = None


# ── bounded planner change manifest ──────────────────────────────────────────

#: Hard byte bound on the manifest JSON — a planner cannot smuggle a plan-sized
#: payload through the reclassification channel; an oversized block parses as
#: missing and the caller fails closed to one clarification.
MAX_MANIFEST_BYTES = 4096
#: Compatibility name for callers that imported the original constant. The
#: bound is deliberately measured in UTF-8 bytes, not Python characters.
MAX_MANIFEST_CHARS = MAX_MANIFEST_BYTES
_MANIFEST_OPEN = "<change_manifest>"
_MANIFEST_CLOSE = "</change_manifest>"

#: Manifest impact thresholds: above the file ceiling — or above the subsystem
#: ceiling once the change spans more than one file — a "medium" plan is
#: actually a large change and must not escape through a direct coder.
MAX_SIMPLE_FILES = 1
MAX_MEDIUM_FILES = 5
MAX_MEDIUM_SUBSYSTEMS = 1

_CRITICAL_FLAGS = (
    "public_contract",
    "data_migration",
    "security_sensitive",
    "external_side_effects",
    "destructive",
)
_MANIFEST_REQUIRED_FIELDS = {
    "files_expected",
    "subsystems",
}
_MANIFEST_DEFAULTS: dict[str, Any] = {
    "public_contract": False,
    "data_migration": False,
    "security_sensitive": False,
    "external_side_effects": False,
    "destructive": False,
    "blocking_decisions": [],
    "validation_commands": 0,
}
_MANIFEST_FIELDS = _MANIFEST_REQUIRED_FIELDS | set(_MANIFEST_DEFAULTS)

# Root, not the harness, chooses the ordered worker chain. This is the closed
# vocabulary it may choose from; ``planner`` is deliberately absent because
# Root owns planning and a chain begins only after a committed plan.
ROOT_PLAN_WORKER_ROLES: tuple[str, ...] = (
    "designer",
    "coder",
    "reviewer",
    "investigator",
    "explorer",
    "reviewer-aux",
)
ROOT_PLAN_WORKER_ROLE: TypeAlias = Literal[
    "designer",
    "coder",
    "reviewer",
    "investigator",
    "explorer",
    "reviewer-aux",
]
ROOT_PLAN_CHANGE_SIZES: tuple[str, ...] = ("small", "medium", "large")
ROOT_PLAN_CHANGE_SIZE: TypeAlias = Literal["small", "medium", "large"]


class ChangeManifestInput(BaseModel):
    """Closed, model-visible version of Root's bounded declaration.

    FastMCP derives the tool schema from this model, while
    :func:`parse_change_manifest_object` remains the defensive runtime parser
    for providers that bypass, loosen, or mis-serialize that schema.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    files_expected: int = Field(ge=0)
    subsystems: list[str]
    public_contract: bool = _MANIFEST_DEFAULTS["public_contract"]
    data_migration: bool = _MANIFEST_DEFAULTS["data_migration"]
    security_sensitive: bool = _MANIFEST_DEFAULTS["security_sensitive"]
    external_side_effects: bool = _MANIFEST_DEFAULTS["external_side_effects"]
    destructive: bool = _MANIFEST_DEFAULTS["destructive"]
    blocking_decisions: list[str] = Field(
        default_factory=lambda: list(_MANIFEST_DEFAULTS["blocking_decisions"]),
    )
    validation_commands: int = Field(
        default=_MANIFEST_DEFAULTS["validation_commands"],
        ge=0,
    )


def manifest_schema() -> dict[str, Any]:
    """Return the exact model-visible contract used in correction responses."""

    return ChangeManifestInput.model_json_schema(mode="validation")



@dataclass(frozen=True)
class ChangeManifest:
    files_expected: int
    subsystems: tuple[str, ...]
    public_contract: bool
    data_migration: bool
    security_sensitive: bool
    external_side_effects: bool
    destructive: bool
    blocking_decisions: tuple[str, ...]
    validation_commands: int


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _require_count(raw: dict[str, Any], key: str) -> int:
    value = raw[key]
    if type(value) is not int or value < 0:
        raise TypeError(f"manifest count {key!r} must be a non-negative integer")
    return value


def _require_strings(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw[key]
    if not isinstance(value, list):
        raise TypeError(f"manifest field {key!r} must be an array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TypeError(
                f"manifest field {key!r} must contain non-blank strings"
            )
        normalized.append(item.strip())
    return tuple(sorted(set(normalized)))




def _require_bool(raw: dict[str, Any], key: str) -> bool:
    """Return a manifest flag only when it is a real JSON boolean.

    The planner contract declares each critical flag as a bool. A wrong-typed
    value (e.g. the string ``"true"``) is NOT silently coerced — that would
    let a truthy-looking `security_sensitive: "true"` read as ``False`` and
    slip a critical change past the large-workflow gate. Raise so the caller
    fails closed to one clarification instead.
    """
    value = raw[key]
    if type(value) is not bool:
        raise TypeError(f"manifest flag {key!r} must be a boolean")
    return value


def parse_change_manifest_object(raw: Any) -> ChangeManifest | None:
    """Validate a compact manifest object and apply safe field defaults.

    Root owns planning, so forcing it to spell five ``false`` values, an empty
    decision list, and a zero command count adds formatting failure without
    adding evidence. The two radius fields remain required. Unknown fields
    still fail closed so this bounded governance channel cannot silently grow.
    """
    if not isinstance(raw, dict):
        return None
    if not _MANIFEST_REQUIRED_FIELDS.issubset(raw):
        return None
    if not set(raw).issubset(_MANIFEST_FIELDS):
        return None
    normalized = dict(_MANIFEST_DEFAULTS)
    normalized.update(raw)
    try:
        return ChangeManifest(
            files_expected=_require_count(normalized, "files_expected"),
            subsystems=_require_strings(normalized, "subsystems"),
            public_contract=_require_bool(normalized, "public_contract"),
            data_migration=_require_bool(normalized, "data_migration"),
            security_sensitive=_require_bool(normalized, "security_sensitive"),
            external_side_effects=_require_bool(
                normalized, "external_side_effects",
            ),
            destructive=_require_bool(normalized, "destructive"),
            blocking_decisions=_require_strings(
                normalized, "blocking_decisions",
            ),
            validation_commands=_require_count(
                normalized, "validation_commands",
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_change_manifest(text: str) -> ChangeManifest | None:
    """Extract the single bounded `<change_manifest>` JSON block, or None.

    Fail-closed on every malformation: no block, MORE THAN ONE block (an
    ambiguous small-then-large planner emission must not resolve to the first,
    smaller one), JSON over `MAX_MANIFEST_CHARS`, missing keys, non-boolean
    critical flags, other wrong types, or negative counts all return None —
    the caller treats that as "the planner could not commit to a blast radius"
    and asks for scope instead of guessing.
    """
    source = text or ""
    if source.count(_MANIFEST_OPEN) != 1 or source.count(_MANIFEST_CLOSE) != 1:
        return None
    start = source.find(_MANIFEST_OPEN) + len(_MANIFEST_OPEN)
    end = source.find(_MANIFEST_CLOSE)
    if end < start:
        return None
    block = source[start:end].strip()
    try:
        if len(block.encode("utf-8")) > MAX_MANIFEST_BYTES:
            return None
        raw = json.loads(
            block,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        result = parse_change_manifest_object(raw)
        if result is None:
            return None
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeEncodeError,
        RecursionError,
        json.JSONDecodeError,
    ):
        return None
    return result


def assess_manifest(manifest: ChangeManifest) -> ChangeAssessment:
    """Re-band a planned change from the planner's own manifest.

    Precedence:
      1. Any `blocking_decisions` → ask_scope. The planner has already used
         model reasoning to choose every reversible default and reserves this
         field for decisions that are expensive, irreversible, or unsafe to
         guess. The harness validates that declaration; it does not reinterpret
         the user's text or use file count as a proxy for whether a decision is
         defaultable.
      2. Any critical flag, more than `MAX_MEDIUM_FILES` files, or more than
         `MAX_MEDIUM_SUBSYSTEMS` subsystem spread across more than
         `MAX_SIMPLE_FILES` file → plan_design_workflow: a large blast radius
         cannot escape through a direct coder. The subsystem count ALONE
         cannot escalate a single-file change: one file is not a large blast
         radius however many subsystems the planner names inside it (a single
         HTML page is routinely "markup + styling + content"). Critical flags
         are unaffected, so a one-file security or migration change stays
         large.
      3. At most one file and one subsystem → single_coder.
      4. Otherwise → planner_then_coder_check.
    """
    flags = tuple(
        flag for flag in _CRITICAL_FLAGS if getattr(manifest, flag)
    )
    if manifest.blocking_decisions:
        listed = ", ".join(manifest.blocking_decisions)
        return ChangeAssessment(
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, RouteKind.ASK_SCOPE,
            tuple(
                f"blocking-decision:{item}"
                for item in manifest.blocking_decisions
            ),
            f"The plan leaves open: {listed}. "
            "Please decide before implementation starts.",
        )
    if (
        flags
        or manifest.files_expected > MAX_MEDIUM_FILES
        or (
            manifest.files_expected > MAX_SIMPLE_FILES
            and len(manifest.subsystems) > MAX_MEDIUM_SUBSYSTEMS
        )
    ):
        evidence = tuple(f"critical:{flag}" for flag in flags) + (
            f"files_expected:{manifest.files_expected}",
            f"subsystems:{len(manifest.subsystems)}",
        )
        return ChangeAssessment(
            Band.LOW,
            Band.HIGH,
            Band.HIGH if flags else Band.MEDIUM,
            RouteKind.PLAN_DESIGN_WORKFLOW,
            evidence,
        )
    if (
        manifest.files_expected <= MAX_SIMPLE_FILES
        and len(manifest.subsystems) <= 1
    ):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, RouteKind.SINGLE_CODER,
            (f"files_expected:{manifest.files_expected}",),
        )
    return ChangeAssessment(
        Band.LOW, Band.MEDIUM, Band.LOW, RouteKind.PLANNER_THEN_CODER_CHECK,
        (
            f"files_expected:{manifest.files_expected}",
            f"subsystems:{len(manifest.subsystems)}",
        ),
    )
