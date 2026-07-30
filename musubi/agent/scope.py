"""What the request text can still be trusted to say: almost nothing.

musubi-tier: substrate
expires-when: never - one regex asking "does this sentence read as a deletion?"
  and answering with a WARNING is not a routing decision, and does not become
  unnecessary as models improve. The layer that WAS ephemeral is gone; see the
  history note below.

What used to live here
----------------------
Nineteen regexes and an `assess_request` cascade that decided, from one
sentence and nothing else, whether a request was broad, sensitive, an edit, a
question, or a real work order — and then handed the model a route to follow.
Text was the only input and nothing checked the verdict, so a wrong one was
silent. "fix the typo in the security section of the README" read as critical;
"wire up Okta" read as routine.

The repository already stated the position this layer contradicted. From the
`request-triage` skill pushed to the planner on every run: *"The harness makes
no judgment about how large or how risky a change is. It cannot."*

What replaced it, in order
--------------------------
1. `agent/evidence.py` — six FACTS about the record (does the request name a
   path inside the workspace, does it exist, has anyone looked yet), each one
   a `stat()` call or a DB row rather than an opinion.
2. `agent/triage.py` — the root declares the turn's shape itself, in a line
   that is recorded so a wrong call is attributable afterwards.
3. `GoalState.evidence_gap` — a mutation worker may not be summoned while
   nothing establishes what the turn targets. This is the enforceable core of
   what the deleted `ask_scope` halt was groping at, except it checks the
   record instead of the vocabulary.
4. `agent/manifest.py` — the planner declares blast radius and the substrate
   does arithmetic on the declaration; `GoalState.overrun_stop` then checks it
   against what workers actually touched.
5. `agent/blast_radius.py` — the destructive hard stop, at the tool boundary,
   where the files about to be removed can be counted and named.

Every one of those reads a measurement or a declaration. None of them reads
English and forms an opinion, which is the line this whole track drew.

What is left here
-----------------
One regex, and it refuses nothing. It notices that a sentence *reads* like a
deletion and attaches a warning, so the model raises the question with the user
BEFORE spending a worker on a plan whose last step the gate would refuse. Being
wrong costs a sentence in a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from agent.manifest import ChangeAssessment
from agent.routes import RouteKind


class ScopeKind(StrEnum):
    """Sizes the PLANNER's manifest establishes — never the sentence.

    `assess_manifest` still classifies a change once a planner has declared its
    blast radius, and these are the names it uses. The lexical branches that
    used to guess them before any model call (`ADVISORY`, `INSPECT`,
    `SIMPLE_EDIT`, …) are gone; `UNKNOWN` is what every turn starts as now.
    """

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
    #: Post-plan bands, set by `apply_planner_manifest`. Always None at turn
    #: start now — nothing assesses a request before a planner reads code.
    assessment: ChangeAssessment | None = None

    def prompt_block(self) -> str:
        """What the harness knows before any model call: the warning, if any.

        This block used to carry a route and a paragraph of guidance telling
        the root which worker to spawn. Both are gone. What remains is a
        statement of ignorance — deliberately, because the alternative was a
        confident guess that the root could not tell apart from a fact.
        """
        lines = ["[agent-routing-hint]"]
        lines += [f"warning={w}" for w in self.warnings]
        lines.append(
            "note=no route was guessed from your request text. Nothing here "
            "has read a file. Decide the shape of this turn from the evidence "
            "block below and your own reading of what was asked."
        )
        lines.append("[/agent-routing-hint]")
        return "\n".join(lines) + "\n"

    def log_line(self) -> str:
        warned = ",".join(self.warnings) if self.warnings else "none"
        return (
            f"scope={self.kind.value} route={self.route} "
            f"warnings={'yes' if self.warnings else 'no'} "
            f"reason=\"{self.reason}\""
            + ("" if warned == "none" else "")
        )


#: What the text SUGGESTS about deletion — never what it establishes. The
#: sentence cannot say which files a run will remove; only the tool call can,
#: and `agent/blast_radius.py` counts them there. This note exists so the model
#: raises the question with the user BEFORE spending a worker on a plan whose
#: last step the gate will refuse.
_DESTRUCTIVE_FILE_RE = re.compile(
    r"(?i)\b(delete|remove|rm|erase)\b.*\b("
    r"file|files|folder|folders|directory|directories|dashboard|dashboards|"
    r"workspace|\*|[\w.-]+\.(?:html|htm|py|js|jsx|ts|tsx|css|md|json|csv|txt)"
    r")\b"
)

DESTRUCTIVE_WARNING = (
    "This request reads as removing files. The harness measures every tool "
    "call and REFUSES any that deletes a file, or that overwrites more than a "
    "handful in one run, until the user has explicitly approved it. So do not "
    "plan around a silent deletion: name the exact paths you intend to remove, "
    "show the user that list, and get a clear go-ahead first."
)

_NO_ROUTE_REASON = "the root triages this turn from the evidence vector"


def classify_task(task: str) -> ScopeHint:
    """One question, answered as a warning. No route, no size, no halt.

    The destructive check used to HALT the turn here, answering with manual
    operator steps. It was measurably the wrong shape: it refused "delete all
    *-dashboard.html files" — the user saying plainly what they wanted — while
    `run rm -rf build` did not match its noun list and routed to a coder
    holding `musubi_run_command`, whose contract states it does no dangerous
    command detection. The guard blocked the honest request and missed every
    other path to the same outcome.

    A regex over a sentence is not entitled to refuse; it IS entitled to warn.
    The refusal lives in `agent/blast_radius.py`, at the tool call, where the
    files about to be removed can be counted and named.
    """
    text = " ".join((task or "").strip().split())
    warnings = (
        (DESTRUCTIVE_WARNING,) if _DESTRUCTIVE_FILE_RE.search(text) else ()
    )
    return ScopeHint(
        kind=ScopeKind.UNKNOWN,
        route=RouteKind.ROOT_DECIDES,
        reason=_NO_ROUTE_REASON,
        warnings=warnings,
    )
