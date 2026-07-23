"""Deterministic request and change-manifest assessment.

musubi-tier: substrate
expires-when: never - ambiguity, blast radius, and risk gates remain useful
  independently of model quality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


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
_CRITICAL_RISK_RE = re.compile(
    r"(?i)\b(auth|authentication|authorization|payment|billing|database|"
    r"migration|oauth|rbac|security)\b"
)


def assess_request(task: str) -> ChangeAssessment:
    """Bands + route for one raw user request. Pure text analysis, zero LLM.

    Precedence: critical risk terms dominate (a payment/auth/database change
    is never "simple" no matter how short the sentence); then a broad product
    request without deliverable constraints stops for ONE clarification;
    bounded static/named artifacts route to a single coder; a framework
    scaffold with multiple parts is a planned medium change; anything left is
    a medium change on insufficient evidence — never silently large.
    """
    text = " ".join((task or "").split())
    risks = tuple(sorted(set(
        match.group(1).lower() for match in _CRITICAL_RISK_RE.finditer(text)
    )))
    if risks:
        return ChangeAssessment(
            Band.LOW, Band.HIGH, Band.HIGH, "plan_design_workflow",
            tuple(f"critical-risk:{item}" for item in risks),
        )
    if _BROAD_PRODUCT_RE.search(text) and not (
        _STATIC_FILE_RE.search(text) or _FRAMEWORK_RE.search(text)
    ):
        return ChangeAssessment(
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, "ask_scope",
            ("broad-product-without-deliverable-constraints",),
            "What should the website do, and should it be a static page or use a specific framework?",
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
