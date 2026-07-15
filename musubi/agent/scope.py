"""Deterministic scope hints for the standalone root agent.

musubi-tier: substrate
expires-when: never - risk/ambiguity/blast-radius hints are durable routing
  context even as model quality improves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class ScopeKind(StrEnum):
    SIMPLE_EDIT = "simple_edit"
    SIMPLE_ARTIFACT = "simple_artifact"
    MEDIUM_CHANGE = "medium_change"
    LARGE_FEATURE = "large_feature"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ScopeHint:
    kind: ScopeKind
    route: str
    reason: str
    requires: tuple[str, ...] = field(default_factory=tuple)

    def prompt_block(self) -> str:
        requires = ",".join(self.requires) if self.requires else "none"
        route_guidance = {
            "single_coder": (
                "Simple route: start with one coder worker using a compact, "
                "implementation-ready brief. Recommend a skill for the coder "
                "(musubi_recommend_skills) and pass the best skill_id as "
                "pushed_skill_id on the spawn. This is an initial routing "
                "recommendation, not a lifetime worker cap."
            ),
            "planner_then_coder_check": (
                "Medium route: spawn planner first for scope and acceptance "
                "criteria, then spawn coder with that plan. Do not ask coder "
                "to both plan and implement."
            ),
            "plan_design_workflow": (
                "Large route: require explicit plan/design/implementation/"
                "review structure before mutation."
            ),
            "ask_scope": (
                "Unknown route: ask one clarifying question before spawning."
            ),
            "direct_answer": (
                "Casual route: answer directly in one turn without tools or workers."
            ),
            "manual_destructive": (
                "Destructive route: do not call tools or workers. Warn and give "
                "manual operator steps instead."
            ),
        }.get(self.route, "Use the route conservatively.")
        return (
            "[agent-routing-scope]\n"
            f"scope={self.kind.value}\n"
            f"route={self.route}\n"
            f"requires={requires}\n"
            f"reason={self.reason}\n"
            f"guidance={route_guidance}\n"
            "[/agent-routing-scope]\n\n"
            "Use this deterministic hint before choosing tools. The root "
            "agent still makes the final role and routing decision. Scope is "
            "an initial routing recommendation; generic orchestration budgets "
            "bound workers independently. Ask for scope when route=ask_scope."
        )

    def log_line(self) -> str:
        requires = ",".join(self.requires) if self.requires else "none"
        return (
            f"scope={self.kind.value} route={self.route} "
            f"requires={requires} reason=\"{self.reason}\""
        )


_PATH_RE = re.compile(
    r"(?i)\b[\w .\-/\\]+\.(?:py|js|jsx|ts|tsx|rs|go|java|html|htm|css|md|json|ya?ml|toml|csv|txt)\b"
)
_SIMPLE_EDIT_RE = re.compile(
    r"(?i)\b(update|change|modify|replace|rename|fix|tweak|adjust|set|add)\b"
)
_ARTIFACT_RE = re.compile(
    r"(?i)\b(create|make|generate|write|build)\b.*\b("
    r"artifact|file|page|dashboard|report|summary|csv|markdown|json|html|chart|doc"
    r")\b"
)
_LARGE_RISK_RE = re.compile(
    r"(?i)\b("
    r"auth|authentication|authorization|billing|payment|database|schema|migration|"
    r"persistence|public api|api endpoint|architecture|multi[- ]tenant|security|"
    r"permissions|oauth|login|rbac"
    r")\b"
)
_VAGUE_RE = re.compile(
    r"(?i)^\s*(fix this|refactor it|add tests|write tests|create tests|help|do it|"
    r"improve this|make it better)\s*$"
)
_CASUAL_RE = re.compile(
    r"(?i)^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay)\s*[!.?]*\s*$"
)
_DESTRUCTIVE_FILE_RE = re.compile(
    r"(?i)\b(delete|remove|rm|erase)\b.*\b("
    r"file|files|folder|folders|directory|directories|dashboard|dashboards|"
    r"workspace|\*|[\w.-]+\.(?:html|htm|py|js|jsx|ts|tsx|css|md|json|csv|txt)"
    r")\b"
)


def classify_task(task: str) -> ScopeHint:
    text = " ".join((task or "").strip().split())
    low = text.lower()
    if _CASUAL_RE.match(text):
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route="direct_answer",
            reason="casual chat does not need tools",
        )
    if _DESTRUCTIVE_FILE_RE.search(text):
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route="manual_destructive",
            reason="destructive file operation needs explicit operator control",
            requires=("manual_confirmation",),
        )
    if not text or _VAGUE_RE.match(text):
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route="ask_scope",
            reason="request lacks a concrete target",
            requires=("clarification",),
        )

    risk_hits = sorted(set(match.group(1).lower() for match in _LARGE_RISK_RE.finditer(text)))
    if len(risk_hits) >= 2 or _mentions_large_workflow(low):
        return ScopeHint(
            kind=ScopeKind.LARGE_FEATURE,
            route="plan_design_workflow",
            reason="high-risk or multi-surface change",
            requires=("plan", "design", "implementation", "review"),
        )

    has_path = _PATH_RE.search(text) is not None
    if has_path and _SIMPLE_EDIT_RE.search(text) and not risk_hits:
        return ScopeHint(
            kind=ScopeKind.SIMPLE_EDIT,
            route="single_coder",
            reason="known file and low-risk edit",
        )

    if _ARTIFACT_RE.search(text) and not risk_hits:
        return ScopeHint(
            kind=ScopeKind.SIMPLE_ARTIFACT,
            route="single_coder",
            reason="concrete low-risk artifact request",
        )

    if risk_hits:
        return ScopeHint(
            kind=ScopeKind.MEDIUM_CHANGE,
            route="planner_then_coder_check",
            reason="concrete change with some risk signals",
            requires=("plan", "implementation", "verification"),
        )

    return ScopeHint(
        kind=ScopeKind.MEDIUM_CHANGE,
        route="planner_then_coder_check",
        reason="concrete change but scope is not obviously tiny",
        requires=("plan", "implementation", "verification"),
    )


def is_simple_scope(hint: ScopeHint | None) -> bool:
    return hint is not None and hint.kind in {
        ScopeKind.SIMPLE_EDIT,
        ScopeKind.SIMPLE_ARTIFACT,
    }


def _mentions_large_workflow(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "full feature",
            "new feature",
            "end to end",
            "from scratch",
            "whole app",
            "entire app",
            "multiple services",
        )
    )
