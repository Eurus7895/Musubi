"""Sub-agent context firewall (Phase A.2).

musubi-tier: substrate
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
    #: The catalog id of the skill in `role_skill` — the model's explicit
    #: per-spawn selection. Carried
    #: separately because `role_skill` is SKILL.md prose: nothing downstream
    #: could name what was pushed, so a role-default push left no trace in the
    #: audit ledger or the console, and every session read "no skill used"
    #: however many were pushed (HI #2 pushes; HI #8 says no spawn is silent).
    role_skill_id: str | None = None


def build_subagent_context(
    brief: str,
    role: str,
    *,
    pushed_skill_id: str | None = None,
    skills_dir: Path | None = None,
) -> SubagentContext:
    """Build the immutable pre-prompt payload for a sub-agent.

    Reads exactly two pieces of data, both of which are independent of the
    parent's session state:

      - `brief`: the user-/parent-supplied task string passed at spawn.
      - `role_skill`: the SKILL.md text pushed into the worker prompt,
        loaded from `.github/skills/<skill_id>/SKILL.md` via `skill_loader`.
        The model must select `pushed_skill_id` explicitly. The harness loads
        exactly that skill and never supplies a role default or fallback.

    `pushed_skill_id` was already validated against the worker
    role's allowlist by `musubi_spawn_subagent` (fail-closed at spawn), so
    it is trusted here — the firewall stays at the spawn boundary and this
    builder only loads public catalog content (no parent state, HI #3).

    `allowed_tools` is the role's hard-cap tool list — the actual run-time
    tool set is `role ∩ main` (Phase A.1, computed in policy_engine).
    Returning the role cap here is informational only and does NOT widen
    the run-time set.

    Raises:
      ValueError if `brief` is empty or `role` is unknown. We fail closed
      rather than serve a sub-agent with no instructions or an unknown
      role; the caller (`musubi_spawn_subagent`) has already validated
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

    chosen = (pushed_skill_id or "").strip()
    if not chosen:
        raise ValueError("pushed_skill_id must name the model-selected skill")
    role_skill = skill_loader.get_skill(chosen, skills_dir=skills_dir)
    if role_skill is None:
        raise ValueError(f"selected skill {chosen!r} not found in the catalog")

    return SubagentContext(
        brief=brief.strip(),
        role=role_key,
        role_skill=role_skill,
        allowed_tools=tuple(SUBAGENT_POLICIES[role_key]),
        role_skill_id=chosen,
    )


def context_keys() -> set[str]:
    """The closed set of fields a sub-agent ever receives.

    Used by tests to assert the firewall hasn't accidentally widened —
    add a field here only after a deliberate design discussion.
    """
    return {"brief", "role", "role_skill", "role_skill_id", "allowed_tools"}


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
