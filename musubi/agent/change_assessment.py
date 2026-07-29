"""Deterministic request and change-manifest assessment.

musubi-tier: substrate
expires-when: never - ambiguity, blast radius, and risk gates remain useful
  independently of model quality.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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
    #: Open questions the planner raised that the next worker may settle with
    #: a sensible default instead of halting the conversation. Non-empty only
    #: when the change is small enough that a wrong default costs one turn to
    #: redirect — never on a critical or multi-file change.
    deferred_unknowns: tuple[str, ...] = ()


_BROAD_PRODUCT_RE = re.compile(
    r"(?i)\b(create|make|build|generate|implement)\b.*\b"
    r"(website|site|web app|application|app|platform|system)\b"
)
_STATIC_FILE_RE = re.compile(
    r"(?i)\b(static|single[- ]file)\b.*\b(html|website|page)\b|"
    r"\b[\w.-]+\.html\b"
)
_BOUNDED_ARTIFACT_RE = re.compile(
    r"(?i)\b(create|make|generate|write|build)\b.*\b"
    r"(file|page|dashboard|report|summary|csv|markdown|json|html|chart|doc)\b"
)
_FRAMEWORK_RE = re.compile(r"(?i)\b(next(?:\.js)?|react|vue|svelte|angular)\b")
_MULTIPART_RE = re.compile(
    r"(?i)\b(routes?|pages?|shared|navbar|footer|typescript|build check)\b"
)
# NOTE: the lexical critical-risk gate was REMOVED. It matched a word, not a
# change: it refused "fix the typo in the security section of the README" with
# zero model calls, while "wire up Okta", "add SSO", and "store user passwords"
# sailed past it. Risk is now declared by the planner in the change manifest
# (`security_sensitive`), read from the code rather than guessed from the
# sentence, and enforced deterministically by `assess_manifest`. The
# sensitive-area vocabulary that remains lives in `agent/scope.py` and has one
# narrow job: withhold the lone-coder shortcut so a planner reads first.


#: The one question a broad product request is stopped for. It asks ONLY what
#: the gate below can actually act on — the page shape, tested by
#: `_STATIC_FILE_RE` and `_FRAMEWORK_RE`. The earlier wording led with "What
#: should the website do?", which nothing here tests: a user who answered it
#: ("a weather checking website") re-matched `_BROAD_PRODUCT_RE` with no escape
#: hatch touched, so an earnest answer could not move the route. A question the
#: asker cannot act on is not a governance step. The content ask stays, demoted
#: to a second sentence and explicitly optional: it enriches the planner's brief
#: without deciding anything, and the turn proceeds either way.
BROAD_PRODUCT_QUESTION = (
    "Should this be a single static HTML page, or a framework app "
    "(React, Next.js, Vue, Svelte, or Angular)? Add what the page should "
    "show in the same reply if you know it — I will build from your answer "
    "either way."
)


def assess_request(task: str) -> ChangeAssessment:
    """Bands + route for one raw user request. Pure text analysis, zero LLM.

    This function NEVER returns `plan_design_workflow`: nothing readable from
    one sentence establishes blast radius, so "large" is decided in exactly one
    place — `assess_manifest`, from what the planner declares after reading the
    code. Precedence here: a broad product request without deliverable
    constraints stops for ONE clarification; bounded static/named artifacts
    route to a single coder; a framework scaffold with multiple parts is a
    planned medium change; anything left is a medium change on insufficient
    evidence.
    """
    text = " ".join((task or "").split())
    if _BROAD_PRODUCT_RE.search(text) and not (
        _STATIC_FILE_RE.search(text) or _FRAMEWORK_RE.search(text)
    ):
        return ChangeAssessment(
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, "ask_scope",
            ("broad-product-without-deliverable-constraints",),
            BROAD_PRODUCT_QUESTION,
        )
    if _STATIC_FILE_RE.search(text) and not _FRAMEWORK_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, "single_coder",
            ("bounded-static-artifact",),
        )
    if _BOUNDED_ARTIFACT_RE.search(text) and not _FRAMEWORK_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, "single_coder",
            ("bounded-named-artifact",),
        )
    if _FRAMEWORK_RE.search(text) and _MULTIPART_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.MEDIUM, Band.LOW, "planner_then_coder_check",
            ("framework-multifile-change",),
        )
    return ChangeAssessment(
        Band.MEDIUM, Band.MEDIUM, Band.UNKNOWN,
        "planner_then_coder_check", ("insufficient-deterministic-evidence",),
    )


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
_MANIFEST_FIELDS = {
    "files_expected",
    "subsystems",
    "public_contract",
    "data_migration",
    "security_sensitive",
    "external_side_effects",
    "destructive",
    "unknowns",
    "validation_commands",
}



@dataclass(frozen=True)
class ChangeManifest:
    files_expected: int
    subsystems: tuple[str, ...]
    public_contract: bool
    data_migration: bool
    security_sensitive: bool
    external_side_effects: bool
    destructive: bool
    unknowns: tuple[str, ...]
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
        if not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS:
            return None
        result = ChangeManifest(
            files_expected=_require_count(raw, "files_expected"),
            subsystems=_require_strings(raw, "subsystems"),
            public_contract=_require_bool(raw, "public_contract"),
            data_migration=_require_bool(raw, "data_migration"),
            security_sensitive=_require_bool(raw, "security_sensitive"),
            external_side_effects=_require_bool(raw, "external_side_effects"),
            destructive=_require_bool(raw, "destructive"),
            unknowns=_require_strings(raw, "unknowns"),
            validation_commands=_require_count(raw, "validation_commands"),
        )
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
      1. Any `unknowns` → ask_scope: an open decision must go back to the
         user, never be guessed by the next worker. EXCEPT on a change small
         enough to be cheap to redo — no critical flag and at most
         `MAX_SIMPLE_FILES` file — where the unknowns are handed to the next
         worker as `deferred_unknowns` to settle with sensible defaults. A
         palette or a heading on a one-file page costs one turn to redirect;
         halting the conversation to ask about every one of them costs the
         planner's whole plan, which this function would otherwise discard.
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
    # A blocking unknown is one the next worker cannot safely default. On a
    # one-file change with no critical flag there is none: a wrong palette or
    # heading costs a single turn to redirect, whereas halting discards the
    # plan the planner just spent its whole budget producing.
    deferrable = not flags and manifest.files_expected <= MAX_SIMPLE_FILES
    if manifest.unknowns and not deferrable:
        listed = ", ".join(manifest.unknowns)
        return ChangeAssessment(
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, "ask_scope",
            tuple(f"unknown:{item}" for item in manifest.unknowns),
            f"The plan leaves open: {listed}. "
            "Please decide before implementation starts.",
        )
    deferred = manifest.unknowns
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
            "plan_design_workflow",
            evidence,
        )
    if (
        manifest.files_expected <= MAX_SIMPLE_FILES
        and len(manifest.subsystems) <= 1
    ):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, "single_coder",
            (f"files_expected:{manifest.files_expected}",),
            deferred_unknowns=deferred,
        )
    return ChangeAssessment(
        Band.LOW, Band.MEDIUM, Band.LOW, "planner_then_coder_check",
        (
            f"files_expected:{manifest.files_expected}",
            f"subsystems:{len(manifest.subsystems)}",
        ),
        deferred_unknowns=deferred,
    )
