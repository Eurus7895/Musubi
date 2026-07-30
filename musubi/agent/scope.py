"""Lexical request classification — the pre-model routing layer.

musubi-tier: ephemeral
expires-when: the root triages its own turn from the evidence vector, leaving
  this file one deterministic question — is the request destructive? — whose
  answer is a WARNING to the model, not a refusal. See
  docs/superpowers/plans/2026-07-29-llm-owned-scope-with-evidence-gate.md
cost-lever: deletes 18 of 19 regexes, assess_request, the pre-run ask_scope
  halt, BROAD_PRODUCT_QUESTION, and the pending_clarification storage column

What lives here, and why it is temporary
----------------------------------------
Every function below answers a question about ENGLISH: is this request broad,
is it an edit, is it sensitive, does the user want advice or a file. Text is
the only input and nothing checks the answer, so a wrong verdict is silent —
which is exactly how "fix the typo in the security section of the README" came
to read as critical while "wire up Okta" read as routine.

The repository already states the position this file contradicts. From the
`request-triage` skill pushed to the planner on every run: *"The harness makes
no judgment about how large or how risky a change is. It cannot."*

What replaces it: the root triages its own turn (it is already a model call),
the planner declares blast radius in a change manifest, and the substrate
ENFORCES that declaration deterministically in `agent/manifest.py`. What stays
here at the end is one branch — destructive or not — emitted as a warning the
model acts on, with the hard stop measured at the tool boundary where the
files being touched can actually be counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from agent.manifest import Band, ChangeAssessment
from agent.routes import RouteKind


class ScopeKind(StrEnum):
    ADVISORY = "advisory"
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
    #: Non-binding notes for the model. A warning never changes the route: it
    #: says something the text suggests but cannot establish, and the model is
    #: free to act on it or not. Anything that must be ENFORCED belongs at the
    #: tool boundary, where the affected files can be counted.
    warnings: tuple[str, ...] = field(default_factory=tuple)
    #: Ambiguity/impact/risk bands for a mutation request (None on the casual,
    #: destructive, vague, and read-only branches, which return before the
    #: assessment runs). Carries the one deterministic clarifying question the
    #: driver returns without a model call when route == RouteKind.ASK_SCOPE.
    assessment: ChangeAssessment | None = None

    def prompt_block(self) -> str:
        requires = ",".join(self.requires) if self.requires else "none"
        route_guidance = {
            RouteKind.ADVISORY: (
                "Advisory route: the user asked to be ADVISED, not for a "
                "change. Answer directly from your own reasoning in ONE turn. "
                "Do NOT spawn a worker: the request names no file, so no "
                "read-only worker can add evidence, and a planner would "
                "return a change manifest the user never asked for."
            ),
            RouteKind.SINGLE_EXPLORER: (
                "Read-only route: the user wants to inspect, not change. Spawn "
                "exactly ONE explorer worker (read-only Read/Grep/Glob) with a "
                "compact brief to reach the target path or files and summarize "
                "what is there. Do NOT spawn a planner or coder and do NOT "
                "attempt any edit. If the path is outside the workspace root or "
                "does not exist, report that plainly and stop — do not retry."
            ),
            RouteKind.SINGLE_CODER: (
                "Simple route: start with one coder worker using a compact, "
                "implementation-ready brief. Recommend a skill for the coder "
                "(musubi_recommend_skills) and pass the best skill_id as "
                "pushed_skill_id on the spawn. This is an initial routing "
                "recommendation, not a lifetime worker cap."
            ),
            RouteKind.PLANNER_THEN_CODER_CHECK: (
                "Medium route: spawn planner first for scope, acceptance "
                "criteria, and a change manifest; then spawn coder with that "
                "plan. Do not ask coder to both plan and implement.\n"
                "If the plan depends on facts about this workspace that "
                "nobody has established yet, summon an EXPLORER for them "
                "first and pass its findings into the planner's brief. "
                "Surveying the workspace is the explorer's job — a planner "
                "sent to find its own facts spends its whole turn budget "
                "reading and returns no manifest at all."
            ),
            RouteKind.PLAN_DESIGN_WORKFLOW: (
                "Large route: require explicit plan/design/implementation/"
                "review structure before mutation."
            ),
            RouteKind.ASK_SCOPE: (
                "Unknown route: ask one clarifying question before spawning."
            ),
            RouteKind.DIRECT_ANSWER: (
                "Casual route: answer directly in one turn without tools or workers."
            ),
        }.get(self.route, "Use the route conservatively.")
        return (
            "[agent-routing-scope]\n"
            f"scope={self.kind.value}\n"
            f"route={self.route}\n"
            f"requires={requires}\n"
            f"reason={self.reason}\n"
            + "".join(f"warning={w}\n" for w in self.warnings)
            + f"guidance={route_guidance}\n"
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
# Consultative intent: the user wants to be ADVISED, not to have something
# changed or read — "explain each", "which is better", "choose the best for
# me". These carry no deliverable and name no target, so every mutation branch
# below reads them as a change on insufficient evidence and sends a planner to
# produce a change manifest nobody asked for.
_ADVISORY_RE = re.compile(
    r"(?i)(\bexplain\b|\bcompare\b|\bversus\b|\bvs\.?\b|\bpros and cons\b|"
    r"\btrade[- ]?offs?\b|\brecommend\b|\bsuggest\b|\badvise\b|\bchoose\b|"
    r"\bpick\b|\bshould i\b|\bwhich (?:one|is|are|should|would)\b|"
    r"\bwhat(?:'?s| is) (?:the )?(?:best|better|difference)\b|\bbest for\b)"
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
            Band.HIGH, Band.UNKNOWN, Band.UNKNOWN, RouteKind.ASK_SCOPE,
            ("broad-product-without-deliverable-constraints",),
            BROAD_PRODUCT_QUESTION,
        )
    if _STATIC_FILE_RE.search(text) and not _FRAMEWORK_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, RouteKind.SINGLE_CODER,
            ("bounded-static-artifact",),
        )
    if _BOUNDED_ARTIFACT_RE.search(text) and not _FRAMEWORK_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.LOW, Band.LOW, RouteKind.SINGLE_CODER,
            ("bounded-named-artifact",),
        )
    if _FRAMEWORK_RE.search(text) and _MULTIPART_RE.search(text):
        return ChangeAssessment(
            Band.LOW, Band.MEDIUM, Band.LOW, RouteKind.PLANNER_THEN_CODER_CHECK,
            ("framework-multifile-change",),
        )
    return ChangeAssessment(
        Band.MEDIUM, Band.MEDIUM, Band.UNKNOWN,
        RouteKind.PLANNER_THEN_CODER_CHECK, ("insufficient-deterministic-evidence",),
    )


def _mutation_intent(text: str) -> bool:
    without_targets = _DIR_TARGET_RE.sub(" ", _PATH_TOKEN_RE.sub(" ", text))
    return _MUTATION_RE.search(without_targets) is not None


#: Word ceiling for a message to read as conversational rather than as a work
#: order. "Okta" and "skill?" are one word; "these are complicated" is three.
_FOLLOW_UP_MAX_WORDS = 6


def _is_bare_follow_up(text: str) -> bool:
    """True when `text` is a short message carrying no actionable signal.

    Deliberately narrow: a message under the word ceiling with no mutation
    verb, no inspection verb, no diagnostic signal, and no path target gives a
    worker nothing to act on. "Okta" and "skill?" are a choice and a question
    inside a conversation; "add auth to the app" and "fix the login bug" carry
    a mutation verb and are excluded here, keeping their own classification.
    """
    words = text.split()
    if not words or len(words) > _FOLLOW_UP_MAX_WORDS:
        return False
    return not (
        _mutation_intent(text)
        or _INSPECT_RE.search(text)
        or _DIAGNOSTIC_RE.search(text)
        or _PATH_RE.search(text)
        or _PATHISH_RE.search(text)
    )


_SIMPLE_EDIT_RE = re.compile(
    r"(?i)\b(update|change|modify|replace|rename|fix|tweak|adjust|set|add)\b"
)
_ARTIFACT_RE = re.compile(
    r"(?i)\b(create|make|generate|write|build)\b.*\b("
    r"artifact|file|page|dashboard|report|summary|csv|markdown|json|html|chart|doc"
    r")\b"
)
# Areas where a mistake is INVISIBLE: the page still renders, the tests still
# pass, and the damage surfaces later (anyone can log in as anyone; the wrong
# amount moved; the column is gone). This list does NOT judge how big a change
# is — text cannot know blast radius, and the old `>= 2 keywords = large` rule
# called two typos in auth.py and payment.py a large feature while calling
# "rewrite the entire user system" a medium one. Its ONLY job is to deny the
# lone-coder shortcut so a read-only planner looks before anything mutates.
# Blast radius is decided downstream from the planner's manifest.
#
# Vocabulary is deliberately generous, because a false positive costs exactly
# one read-only planner run. The old list missed the plural "payments" and was
# blind to SSO, Okta, passwords, tokens, and sessions entirely.
_NO_SHORTCUT_RE = re.compile(
    r"(?i)\b("
    r"auth|authn|authz|authentication|authorization|login|logout|sign[- ]?in|"
    r"sign[- ]?up|sso|oauth|oidc|okta|saml|jwt|token|session|cookie|"
    r"password|passwd|credential|secret|api[- ]key|permission|role|rbac|acl|"
    r"access control|security|encrypt|hash|"
    r"payment|billing|invoice|charge|refund|subscription|price|checkout|"
    r"database|schema|migration|persistence|sql|"
    r"public api|api contract|api endpoint|breaking api"
    r")s?\b"
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


def _clarification_already_spent(
    assessment: ChangeAssessment | None,
) -> ScopeHint:
    """The route to take when the one clarification has already been asked.

    Never `ask_scope`: the user has answered the deterministic question once,
    and a second identical question is not a governance step — it is a loop
    that spends a turn and delivers nothing. The request goes to a planner
    instead, which reads the workspace and asks its own *specific* questions
    inside a plan rather than re-emitting a canned sentence.

    The assessment rides along with its route downgraded and its clarifying
    question dropped, so nothing downstream (`GoalState` bands,
    `_deterministic_scope_answer`) can resurrect the halt. The bands are left
    exactly as assessed: the request may still be ambiguous, and the planner
    should see that.
    """
    downgraded = (
        None
        if assessment is None
        else replace(
            assessment,
            route=RouteKind.PLANNER_THEN_CODER_CHECK,
            evidence=assessment.evidence + ("clarification-answered",),
            clarifying_question=None,
        )
    )
    return ScopeHint(
        kind=ScopeKind.MEDIUM_CHANGE,
        route=RouteKind.PLANNER_THEN_CODER_CHECK,
        reason="clarification already asked and answered; plan on what is known",
        requires=("plan", "implementation", "verification"),
        assessment=downgraded,
    )


def _classify_route(
    task: str,
    *,
    has_history: bool = False,
    allow_clarification: bool = True,
) -> ScopeHint:
    """Classify ONE user message.

    `has_history` says only that this `chat_id` already has prior turns — not
    what they were about. It is used in exactly one direction: to route a bare
    conversational follow-up to the cheap advisory answer. Nothing may use it
    to escalate, so a stale or wrong flag can never open a mutation path.

    `allow_clarification=False` says the caller has ALREADY spent this
    conversation's one deterministic clarification and is passing the merged
    request (original + the user's answer). Every `ask_scope` return below
    becomes a planner route instead. Like `has_history`, it moves in exactly
    one direction — it can only remove a halt, never add one — so a wrong flag
    costs one planner run and can never block the conversation.
    """
    text = " ".join((task or "").strip().split())
    if _CASUAL_RE.match(text):
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route=RouteKind.DIRECT_ANSWER,
            reason="casual chat does not need tools",
        )
    if not text or _VAGUE_RE.match(text):
        # An empty message is the one case that still halts after a spent
        # clarification: there is no merged text to plan from, so the question
        # is the only move left. Every other vague request carries the prior
        # turn's content and goes to a planner.
        if text and not allow_clarification:
            return _clarification_already_spent(None)
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route=RouteKind.ASK_SCOPE,
            reason="request lacks a concrete target",
            requires=("clarification",),
        )

    # Consultative turn: advise, don't change. Runs BEFORE the mutation
    # branches (including the critical-risk gate) because "which auth provider
    # should I choose?" is a question about auth, not a change to auth — the
    # risk gate would otherwise force a plan/design/review workflow onto a
    # request that mutates nothing. Three exclusions keep it narrow: a mutation
    # verb ("compare these and fix the drift"), a diagnostic signal, or ANY
    # concrete path/filesystem target. The last one matters most — "explain
    # run.py" is a codebase question that needs a worker to actually read the
    # file, so it must not be answered from the root's own memory. What
    # survives is an abstract question the root is the cheapest answerer for.
    if (
        _ADVISORY_RE.search(text)
        and not _mutation_intent(text)
        and not _DIAGNOSTIC_RE.search(text)
        and not (_PATH_RE.search(text) or _PATHISH_RE.search(text))
    ):
        return ScopeHint(
            kind=ScopeKind.ADVISORY,
            route=RouteKind.ADVISORY,
            reason="consultative question with no deliverable or path target",
        )

    # Conversational follow-up. `classify_task` sees ONE message, so a bare
    # noun ("Okta") or a one-word question ("skill?") carries no signal at all
    # and falls to the mutation catch-all below — in the traced conversation
    # that bought a 96s / 27k-token planner round trip to answer a question
    # that named no file. With prior turns on record, the cheapest correct
    # reading is that the user is still talking, so it gets the same advisory
    # answer. Inheritance moves only TOWARD the cheaper route: anything
    # carrying a mutation verb, a path, or an inspect verb is excluded by
    # `_is_bare_follow_up` and keeps its own classification.
    if has_history and _is_bare_follow_up(text):
        return ScopeHint(
            kind=ScopeKind.ADVISORY,
            route=RouteKind.ADVISORY,
            reason="bare follow-up in an ongoing conversation",
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
            route=RouteKind.SINGLE_EXPLORER,
            reason="read-only inspection of a path or files",
        )

    # Deterministic ambiguity band for the mutation branches below. NOTE what
    # this no longer does: it does not guess blast radius. "Large" is decided
    # in exactly one place — `assess_manifest`, from the planner's declared
    # `files_expected` / `subsystems` / critical flags, after it has read the
    # code. Text cannot know blast radius, and the removed lexical rules proved
    # it: two keywords made "fix typo in auth.py and payment.py" a large
    # feature, while "rewrite the entire user system" scored zero.
    assessment = assess_request(text)
    if assessment.route == RouteKind.ASK_SCOPE:
        if not allow_clarification:
            return _clarification_already_spent(assessment)
        return ScopeHint(
            kind=ScopeKind.UNKNOWN,
            route=RouteKind.ASK_SCOPE,
            reason="broad product request without deliverable constraints",
            requires=("clarification",),
            assessment=assessment,
        )

    # Sensitive-area guard. This is NOT a size judgment — its only effect is to
    # withhold the single_coder shortcut so a read-only planner reads the code
    # and files a manifest before anything mutates. A false positive costs one
    # planner run; a false negative lets a lone coder change auth unreviewed.
    no_shortcut = _NO_SHORTCUT_RE.search(text) is not None

    has_path = _PATH_RE.search(text) is not None
    if has_path and _SIMPLE_EDIT_RE.search(text) and not no_shortcut:
        return ScopeHint(
            kind=ScopeKind.SIMPLE_EDIT,
            route=RouteKind.SINGLE_CODER,
            reason="known file and low-risk edit",
            assessment=assessment,
        )

    if _ARTIFACT_RE.search(text) and not no_shortcut:
        return ScopeHint(
            kind=ScopeKind.SIMPLE_ARTIFACT,
            route=RouteKind.SINGLE_CODER,
            reason="concrete low-risk artifact request",
            assessment=assessment,
        )

    if no_shortcut:
        return ScopeHint(
            kind=ScopeKind.MEDIUM_CHANGE,
            route=RouteKind.PLANNER_THEN_CODER_CHECK,
            reason="touches a sensitive area; a planner reads before mutation",
            requires=("plan", "implementation", "verification"),
            assessment=assessment,
        )

    return ScopeHint(
        kind=ScopeKind.MEDIUM_CHANGE,
        route=RouteKind.PLANNER_THEN_CODER_CHECK,
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


# `_mentions_large_workflow` was REMOVED with the rest of the lexical size
# guessing. It matched phrases like "whole app" and "multiple services" — and
# so scored zero on "rewrite the entire user system" and "migrate all 40
# services to the new runtime". Size is decided from the planner's manifest.


#: What the text SUGGESTS about deletion — never what it establishes. The
#: sentence cannot say which files a run will remove; only the tool call can,
#: and `agent/blast_radius.py` counts them there. This note exists so the model
#: raises the question with the user BEFORE spending a worker on a plan whose
#: last step the gate will refuse.
DESTRUCTIVE_WARNING = (
    "This request reads as removing files. The harness measures every tool "
    "call and REFUSES any that deletes a file, or that overwrites more than a "
    "handful in one run, until the user has explicitly approved it. So do not "
    "plan around a silent deletion: name the exact paths you intend to remove, "
    "show the user that list, and get a clear go-ahead first."
)


def classify_task(
    task: str,
    *,
    has_history: bool = False,
    allow_clarification: bool = True,
) -> ScopeHint:
    """Classify one message, attaching non-binding warnings.

    The destructive check used to HALT the turn here, answering with manual
    operator steps. It was measurably the wrong shape: it refused "delete all
    *-dashboard.html files" — the user saying plainly what they wanted — while
    `run rm -rf build` did not match its noun list and routed to a coder
    holding `musubi_run_command`, whose contract states it does no dangerous
    command detection. The guard blocked the honest request and missed every
    other path to the same outcome.

    A regex over a sentence is not entitled to refuse; it IS entitled to warn.
    The refusal now lives in `agent/blast_radius.py`, at the tool call, where
    the files about to be removed can be counted and named.
    """
    hint = _classify_route(
        task, has_history=has_history, allow_clarification=allow_clarification,
    )
    text = " ".join((task or "").strip().split())
    if _DESTRUCTIVE_FILE_RE.search(text):
        return replace(hint, warnings=hint.warnings + (DESTRUCTIVE_WARNING,))
    return hint
