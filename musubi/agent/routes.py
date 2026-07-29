"""The closed vocabulary of routing decisions.

musubi-tier: substrate
expires-when: never - whoever decides the route (a regex today, the root and
  the planner tomorrow), the set of answers it may give is a contract every
  consumer branches on.

Why an enum and not plain strings: a route drove 43 bare string literals
across four modules, and a typo in any of them fails SILENTLY — `route ==
"single_codr"` is simply False, so the request takes another path with no
error anywhere. `StrEnum` members compare and hash exactly like their values,
so existing comparisons against raw strings keep working while every
construction site becomes a name the interpreter can check.
"""

from __future__ import annotations

from enum import StrEnum


class RouteKind(StrEnum):
    #: Answer from the root's own reasoning, one turn, no workers.
    ADVISORY = "advisory"
    #: One read-only explorer worker reaches a path and reports.
    SINGLE_EXPLORER = "single_explorer"
    #: One coder worker implements directly.
    SINGLE_CODER = "single_coder"
    #: Planner first for a change manifest, coder only after it lands.
    PLANNER_THEN_CODER_CHECK = "planner_then_coder_check"
    #: Design and independent review owed before the change is done. Reachable
    #: ONLY from `assess_manifest` — no sentence establishes a blast radius.
    PLAN_DESIGN_WORKFLOW = "plan_design_workflow"
    #: Halt and put one clarifying question to the user.
    ASK_SCOPE = "ask_scope"
    #: Conversational reply, no tools.
    DIRECT_ANSWER = "direct_answer"
    #: Refuse and hand back manual operator steps.
    MANUAL_DESTRUCTIVE = "manual_destructive"
