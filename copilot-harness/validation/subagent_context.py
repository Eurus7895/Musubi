"""Sub-agent context firewall (Phase A.2).

harness-tier: substrate
expires-when: never — Frozen sub-agent context (HI #3 firewall).


Sub-agents exist to do focused lookup work and report a tight summary back
to their parent. They must NOT see parent session state, memory, sibling
sub-agents, or anything beyond:

    1. The `brief` the parent passed at spawn time.
    2. The role's own SKILL.md (procedure for the role).

This module is the only sanctioned producer of a sub-agent's pre-prompt
payload. The extension-side runner (Phase A.3) calls
`build_subagent_context(brief, role)` and uses the result verbatim;
nothing else should construct sub-agent context.

Contrast with `validation/context_builder.py`:
  - That module reads session state for *main* agents (planner, coder,
    reviewer, …) under per-agent firewall rules.
  - This module never reads session state. Its signature deliberately
    excludes session_id and db_path so the firewall is impossible to
    bypass at the type level.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skills import skill_loader

# scripts/policy_engine.py is on sys.path via server.py's _add_scripts_to_path.
# In tests this module may be imported standalone; resolve scripts/ as a
# fallback so SUBAGENT_POLICIES is reachable.
def _ensure_scripts_on_path() -> None:
    candidate = Path(__file__).parent.parent.parent / "scripts"
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


_ensure_scripts_on_path()
from policy_engine import SUBAGENT_POLICIES  # noqa: E402


# Each sub-agent role can declare a single SKILL.md that captures the
# procedure for its role. Phase A.2 ships the table; Phase A.3 lands the
# actual SKILL.md files. Roles without a registered skill receive
# `role_skill: None` — never an arbitrary fallback skill.
SUBAGENT_ROLE_SKILLS: dict[str, str | None] = {
    "explorer":     "explorer",       # .github/skills/explorer/SKILL.md (Phase A.3)
    "investigator": "investigator",   # .github/skills/investigator/SKILL.md (Phase A.3)
    "reviewer-aux": "reviewer-aux",   # .github/skills/reviewer-aux/SKILL.md (Phase A.3)
    # Phase B.1 — pipeline roles spawnable ad-hoc by the orchestrator. They
    # carry their procedure in `.github/agents/<role>.agent.md`, not a
    # role-procedure SKILL.md. The Phase B.2 runner injects the agent body
    # at request-build time; the harness pushes None here so no stray
    # role skill is loaded.
    "planner":      None,
    "coder":        None,
    "reviewer":     None,
    # Phase C.2 — summarizer drives the 90% reactive-compaction branch.
    # The procedure lives in `.github/skills/summarizer/SKILL.md`.
    "summarizer":   "summarizer",
}


@dataclass(frozen=True)
class SubagentContext:
    """The complete pre-prompt payload for a sub-agent run.

    Frozen so the runner can't mutate it after build time. Two fields only:
    everything else (session state, memory, sibling subs) is unreachable
    by construction.
    """
    brief: str
    role: str
    role_skill: str | None
    allowed_tools: tuple[str, ...]


def build_subagent_context(
    brief: str,
    role: str,
    *,
    skills_dir: Path | None = None,
) -> SubagentContext:
    """Build the immutable pre-prompt payload for a sub-agent.

    Reads exactly two pieces of data, both of which are independent of the
    parent's session state:

      - `brief`: the user-/parent-supplied task string passed at spawn.
      - `role_skill`: the SKILL.md text for the role's procedure, loaded
        from `.github/skills/<skill_id>/SKILL.md` via `skill_loader`.
        None when no skill is registered for the role yet.

    `allowed_tools` is the role's hard-cap tool list — the actual run-time
    tool set is `role ∩ main` (Phase A.1, computed in policy_engine).
    Returning the role cap here is informational only and does NOT widen
    the run-time set.

    Raises:
      ValueError if `brief` is empty or `role` is unknown. We fail closed
      rather than serve a sub-agent with no instructions or an unknown
      role; the caller (`harness_spawn_subagent`) has already validated
      the policy table, so an unknown role here is a programmer error.
    """
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("brief must be a non-empty string")

    role_key = role.strip()
    if role_key not in SUBAGENT_POLICIES:
        raise ValueError(
            f"Unknown sub-agent role {role!r}. "
            f"Valid roles: {sorted(SUBAGENT_POLICIES.keys())}"
        )

    skill_id = SUBAGENT_ROLE_SKILLS.get(role_key)
    role_skill: str | None = None
    if skill_id is not None:
        # Loaded only when the SKILL.md actually exists on disk; Phase A.3
        # adds the files. Until then `role_skill` stays None — the runner
        # treats that as "no procedure pushed; rely on the role
        # description in the spawn brief".
        role_skill = skill_loader.get_skill(skill_id, skills_dir=skills_dir)

    return SubagentContext(
        brief=brief.strip(),
        role=role_key,
        role_skill=role_skill,
        allowed_tools=tuple(SUBAGENT_POLICIES[role_key]),
    )


def context_keys() -> set[str]:
    """The closed set of fields a sub-agent ever receives.

    Used by tests to assert the firewall hasn't accidentally widened —
    add a field here only after a deliberate design discussion.
    """
    return {"brief", "role", "role_skill", "allowed_tools"}


def assert_no_session_leakage(payload: Any) -> None:
    """Defensive check: raise AssertionError if a payload looks like main
    session state.

    Sub-agents must never receive a dict that contains plan / design /
    code / review / request / memory / fail_patterns. The runner can call
    this on whatever it is about to inject so a future refactor that
    accidentally widens the firewall surface fails loudly.
    """
    if not isinstance(payload, dict):
        return
    forbidden = {
        "plan", "design", "code", "review",
        "request", "memory", "fail_patterns", "fix_instructions",
        "session_id", "agent_versions",
    }
    leaked = forbidden & set(payload.keys())
    if leaked:
        raise AssertionError(
            f"Sub-agent firewall breach: payload contains main-session "
            f"keys {sorted(leaked)}. build_subagent_context returns only "
            f"{sorted(context_keys())}."
        )
