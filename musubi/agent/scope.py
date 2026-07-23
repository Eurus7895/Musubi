"""Deterministic scope hints for the standalone root agent.

musubi-tier: substrate
expires-when: never - risk/ambiguity/blast-radius hints are durable routing
  context even as model quality improves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from agent.change_assessment import ChangeAssessment, assess_request


class ScopeKind(StrEnum):
    INSPECT = "inspect"
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
    #: Ambiguity/impact/risk bands for a mutation request (None on the casual,
    #: destructive, vague, and read-only branches, which return before the
    #: assessment runs). Carries the one deterministic clarifying question the
    #: driver returns without a model call when route == "ask_scope".
    assessment: ChangeAssessment | None = None

    def prompt_block(self) -> str:
        requires = ",".join(self.requires) if self.requires else "none"
        route_guidance = {
            "single_explorer": (
                "Read-only route: the user wants to inspect, not change. Spawn "
                "exactly ONE explorer worker (read-only Read/Grep/Glob) with a "
                "compact brief to reach the target path or files and summarize "
                "what is there. Do NOT spawn a planner or coder and do NOT "
                "attempt any edit. If the path is outside the workspace root or "
                "does not exist, report that plainly and stop — do not retry."
            ),
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
# Read-only intent: the user wants to reach/look at something, not change it.
_INSPECT_RE = re.compile(
    r"(?i)(\breach(?:\s+(?:to|into|out\s+to))?\b|\bopen\b|\bshow\b|\bview\b|"
    r"\bread\b|\blist\b|\bbrowse\b|\bexplore\b|\binspect\b|\bexamine\b|"
    r"\blook(?:\s+(?:at|into|in))?\b|\bfind\b|\blocate\b|\bcat\b|\bdisplay\b|"
    r"\bdescribe\b|\btell me about\b|\bwhat(?:'?s| is) in\b|\bwhere(?:'?s| is)\b)"
)
# Any verb that would change state — its presence disqualifies the read-only
# route so an explicit edit/create/run request is never sent to an explorer.
# Filesystem-move verbs (move/copy/mv/cp) are mutations too: "find and move
# src/foo to src/bar" is a change, not an inspection.
_MUTATION_RE = re.compile(
    r"(?i)\b(create|make|generate|write|build|add|update|change|modify|replace|"
    r"rename|fix|tweak|adjust|set|delete|remove|erase|refactor|implement|"
    r"install|run|execute|deploy|commit|push|edit|migrate|rewrite|append|"
    r"move|copy|mv|cp)\b"
)
# Diagnostic intent ("find why X is failing") needs an investigator with
# Bash/test access, not a read-only explorer — route it away from inspection so
# the root can reproduce the failure. Kept tight (strong failure/why signals
# only) so a plain file read like "read the error log" is not swept up.
_DIAGNOSTIC_RE = re.compile(
    r"(?i)\b(why|failing|fails|failed|not working|does(?:n'?t| not) work)\b"
)
# A directory named after a mutation verb ("build directory", "run folder") is
# a *target*, not an action. Stripped before the mutation check so a read-only
# "open build directory" is not disqualified by the embedded verb.
_DIR_TARGET_RE = re.compile(
    r"(?i)\b[\w.\-]+\s+(?:folders?|directory|directories|dir)\b"
)
# A concrete path/dir/file target, so bare intent ("open a PR") does not route
# to inspection. Matches a drive-letter path, a slashed path segment, or an
# explicit filesystem noun.
_PATHISH_RE = re.compile(
    r"(?i)(\b[a-z]:[\\/]|[\\/][\w.\-]+[\\/]|\b[\w.\-]+[\\/][\w.\-]+|"
    r"\b(folder|directory|directories|dir|path|file|files|repo|repository|"
    r"workspace|project|codebase|module|package)\b)"
)
# Path-like tokens (drive paths, slashed paths, and filenames with an
# extension) — WITHOUT the space-tolerant matching of `_PATH_RE`, which would
# greedily swallow a whole clause. Stripped before the mutation check so a
# filename such as `run.py` or `src/update-config` never reads as the mutation
# verb it embeds, while a real verb ("...replace TODO in run.py") survives.
_PATH_TOKEN_RE = re.compile(
    r"(?i)[a-z]:[\\/][\w.\-\\/]*|[\w.\-]*[\\/][\w.\-/\\]*|"
    r"\b[\w.\-]+\.(?:py|js|jsx|ts|tsx|rs|go|java|html|htm|css|md|json|ya?ml|toml|csv|txt)\b"
)


def _mutation_intent(text: str) -> bool:
    without_targets = _DIR_TARGET_RE.sub(" ", _PATH_TOKEN_RE.sub(" ", text))
    return _MUTATION_RE.search(without_targets) is not None


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

    # Read-only inspection ("reach to / open / show / read / list <path>")
    # routes to a single explorer BEFORE the risk/medium heuristics: reading a
    # sensitive area is still just reading, so it must not be scoped as a
    # planner→coder change. Gated on a concrete path/dir target and the absence
    # of any mutation verb, so explicit edits/creates are never intercepted; a
    # diagnostic ("find why X is failing") is excluded so it can keep the
    # investigator's Bash/test access instead of a read-only explorer.
    if (
        _INSPECT_RE.search(text)
        and not _mutation_intent(text)
        and not _DIAGNOSTIC_RE.search(text)
        and (_PATH_RE.search(text) or _PATHISH_RE.search(text))
    ):
        return ScopeHint(
            kind=ScopeKind.INSPECT,
            route="single_explorer",
            reason="read-only inspection of a path or files",
        )

    # Deterministic ambiguity/impact/risk bands for the mutation branches
    # below. Only the high-ambiguity verdict changes the route here — a broad
    # product request without deliverable constraints stops at one
    # clarification instead of guessing a lexical scope; every other verdict
    # rides along on the hint so the goal-state controller can reclassify
    # after a planner manifest lands.
    assessment = assess_request(text)
    if assessment.route == "ask_scope":
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route="ask_scope",
            reason="broad product request without deliverable constraints",
            requires=("clarification",),
            assessment=assessment,
        )
    if assessment.route == "plan_design_workflow":
        # The deterministic critical-risk gate fired (auth/payment/database/
        # migration/…). Honor it directly — the legacy `_LARGE_RISK_RE`
        # threshold needs TWO tokens, so a single critical term ("add
        # authentication") would otherwise silently downgrade to a medium
        # planner→coder change and skip the plan/design/review structure.
        return ScopeHint(
            kind=ScopeKind.LARGE_FEATURE,
            route="plan_design_workflow",
            reason="critical-risk change requires plan/design/review",
            requires=("plan", "design", "implementation", "review"),
            assessment=assessment,
        )

    risk_hits = sorted(set(match.group(1).lower() for match in _LARGE_RISK_RE.finditer(text)))
    if len(risk_hits) >= 2 or _mentions_large_workflow(low):
        return ScopeHint(
            kind=ScopeKind.LARGE_FEATURE,
            route="plan_design_workflow",
            reason="high-risk or multi-surface change",
            requires=("plan", "design", "implementation", "review"),
            assessment=assessment,
        )

    has_path = _PATH_RE.search(text) is not None
    if has_path and _SIMPLE_EDIT_RE.search(text) and not risk_hits:
        return ScopeHint(
            kind=ScopeKind.SIMPLE_EDIT,
            route="single_coder",
            reason="known file and low-risk edit",
            assessment=assessment,
        )

    if _ARTIFACT_RE.search(text) and not risk_hits:
        return ScopeHint(
            kind=ScopeKind.SIMPLE_ARTIFACT,
            route="single_coder",
            reason="concrete low-risk artifact request",
            assessment=assessment,
        )

    if risk_hits:
        return ScopeHint(
            kind=ScopeKind.MEDIUM_CHANGE,
            route="planner_then_coder_check",
            reason="concrete change with some risk signals",
            requires=("plan", "implementation", "verification"),
            assessment=assessment,
        )

    return ScopeHint(
        kind=ScopeKind.MEDIUM_CHANGE,
        route="planner_then_coder_check",
        reason="concrete change but scope is not obviously tiny",
        requires=("plan", "implementation", "verification"),
        assessment=assessment,
    )


def is_simple_scope(hint: ScopeHint | None) -> bool:
    return hint is not None and hint.kind in {
        ScopeKind.INSPECT,
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
