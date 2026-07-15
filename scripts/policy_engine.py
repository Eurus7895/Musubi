"""Policy engine — maps (pipeline, agent) → allowed tools, plus the
sub-agent spawn allow-lists used by Phase A.

Used by scripts/pre_tool_use.py and musubi/server.py. Kept as
plain dicts so hooks executed from the command line (not just from
Python) can import it cheaply without pulling in the harness core.

Rules:
  - A tool is allowed only if it appears in the agent's ALLOWED list.
    Any tool not in the list is denied — explicit allowlists > denylists.
  - A main agent may spawn a sub-agent role only if the role appears in
    `MAIN_SUBAGENT_ALLOWLIST[main]`. This dict is the *firewall* — the
    maximum set per role. Pipelines narrow further via their
    `pipeline.yaml::generator.agents[].spawns` (and `evaluator.spawns`)
    field. When `pipeline_name` is passed, the effective set is
    `pipeline.yaml's spawns ∩ MAIN_SUBAGENT_ALLOWLIST[main]`. When
    `pipeline_name` is None (agent path, or callers without a
    pipeline context), the firewall is returned directly.
  - The sub-agent's effective tools are
    `SUBAGENT_POLICIES[role] ∩ main's tool allow-list`. Unknown role
    → deny. Unknown main → deny.

Back-compat: agents that have an entry in the per-pipeline table
override the defaults; agents unknown to the engine are denied all
tools (fail-closed).
"""

from __future__ import annotations

import os
from pathlib import Path

PIPELINE_POLICIES: dict[str, dict[str, list[str]]] = {
    "feature-dev": {
        "planner":  ["Read", "View", "Grep", "Glob"],
        "designer": ["Read", "View", "Grep", "Glob"],
        "coder":    ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"],
        "reviewer": ["Read", "View", "Grep", "Glob"],
    },
    # Phase H.1 — /code-review pipeline. Three read-only agents; the
    # synthesizer fans out reviewer-aux per file but never writes itself.
    # No Bash for scoper/finder/synthesizer — diff parsing happens in the
    # TS runner before the agents see the data.
    "code-review": {
        "scoper":      ["Read", "View", "Grep", "Glob"],
        "finder":      ["Read", "View", "Grep", "Glob"],
        "synthesizer": ["Read", "View", "Grep", "Glob"],
    },
}


# ── Sub-agent policies (Phase A) ───────────────────────────────────────────
#
# SUBAGENT_POLICIES — per-role tool allow-list. The role files live under
# `.github/agents/workers/<role>.agent.md`.
# Defining the policy here ahead of the role files is intentional: the
# spawn path must fail closed even if the .agent.md file is missing.
SUBAGENT_POLICIES: dict[str, list[str]] = {
    "explorer":     ["Read", "View", "Grep", "Glob"],
    "investigator": ["Read", "View", "Grep", "Glob", "Bash"],
    "reviewer-aux": ["Read", "View"],
    # Phase B.1 — pipeline roles spawnable as ad-hoc sub-agents by the
    # agent. Tool sets mirror PIPELINE_POLICIES["feature-dev"] so
    # an ad-hoc spawn cannot exceed what the same role gets inside a
    # pipeline. Sync is enforced by validate_policy_table(): a role
    # present in both tables must carry identical tool sets, or boot
    # aborts. Intentional divergence is a design discussion first.
    "planner":      ["Read", "View", "Grep", "Glob"],
    "designer":     ["Read", "View", "Grep", "Glob"],
    "coder":        ["Read", "View", "Grep", "Glob", "Write", "Edit", "Bash"],
    "reviewer":     ["Read", "View", "Grep", "Glob"],
    # Phase C.2 — text-only sub-agent driving 90% reactive compaction.
    # No tools: the brief already carries the older conversation window
    # serialized as text, and the output is plain markdown.
    "summarizer":   [],
    # code-review stage roles, runnable as standalone pipeline stage
    # workers (`agent --pipeline code-review`). Tool sets must equal
    # PIPELINE_POLICIES["code-review"] — the boot-time sync check in
    # validate_policy_table enforces it. They are NOT in
    # MAIN_SUBAGENT_ALLOWLIST["agent"]: pipeline-internal roles, never
    # ad-hoc spawnable by the root (locked decision #4).
    "scoper":       ["Read", "View", "Grep", "Glob"],
    "finder":       ["Read", "View", "Grep", "Glob"],
    "synthesizer":  ["Read", "View", "Grep", "Glob"],
}

# MAIN_SUBAGENT_ALLOWLIST — firewall: the maximum set of sub-agent roles
# each main agent may EVER spawn, across all pipelines. Pipelines narrow
# further via their `pipeline.yaml::generator.agents[].spawns` field; a
# pipeline.yaml cannot widen this dict.
# - "agent" (Phase B.1) may spawn the read-only Phase A roles plus
#   the pipeline roles ad-hoc. It must NOT spawn an entire pipeline —
#   that is reserved for user-invoked slash commands. Locked decision #4
#   in docs/roadmap.md. Agent has no pipeline.yaml, so this entry
#   IS the effective list.
# - Phase G.1.6: feature-dev's `coder` and `reviewer` stages opt into
#   read-only sub-agents:
#     coder    → explorer (existing-callers scan), investigator (diagnostics)
#     reviewer → reviewer-aux (per-file checklist)
#   For feature-dev these values are now declared in
#   `.github/pipelines/feature-dev/pipeline.yaml`; the firewall here is
#   sized to match so the intersection is identical.
# - planner / designer stay empty in the firewall AS MAINS (they spawn
#   nothing): even a future pipeline that declares spawns for them gets
#   dropped to []. As a SPAWNEE, designer joined the agent's list when the
#   standalone worker catalog shipped `workers/designer.agent.md` — the
#   old "ask for /feature-dev instead" rationale was embedded-host-only.
MAIN_SUBAGENT_ALLOWLIST: dict[str, list[str]] = {
    "agent": [
        "explorer", "investigator", "reviewer-aux",
        "planner", "designer", "coder", "reviewer",
        "summarizer",
    ],
    "planner":  [],
    "designer": [],
    "coder":    ["explorer", "investigator"],
    "reviewer": ["reviewer-aux"],
    # Phase H.1 — /code-review pipeline roles. scoper + finder don't spawn
    # sub-agents; only synthesizer fans out reviewer-aux per file.
    "scoper":      [],
    "finder":      [],
    "synthesizer": ["reviewer-aux"],
}


# ── Pipeline.yaml-driven spawns cache ────────────────────────────────────
#
# Read `.github/pipelines/<name>/pipeline.yaml` to discover per-pipeline,
# per-agent declared spawns. Cached by (path, mtime). The yaml is the
# *declaration*; MAIN_SUBAGENT_ALLOWLIST is the *firewall*. Effective
# = declaration ∩ firewall.

_PIPELINE_SPAWNS_CACHE: dict[str, tuple[float, dict[str, list[str]]]] = {}


def _pipelines_root() -> Path:
    """Resolve `.github/pipelines/`. MUSUBI_ROOT wins when set."""
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        candidate = Path(env) / ".github" / "pipelines"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent / ".github" / "pipelines"


def _load_pipeline_spawns(pipeline_name: str) -> dict[str, list[str]]:
    """Per-agent spawns declared in `pipeline_name`'s pipeline.yaml.

    Returns {} when the file is missing, malformed, or omits all
    `spawns:` fields. Cached by mtime.
    """
    safe = (pipeline_name or "").strip()
    if not safe or "/" in safe or ".." in safe:
        return {}
    path = _pipelines_root() / safe / "pipeline.yaml"
    if not path.is_file():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _PIPELINE_SPAWNS_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        import yaml  # type: ignore[import-untyped]
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    stages = data.get("stages")
    if isinstance(stages, list) and stages:
        # Composer owns flat-stage/preset resolution. Import lazily so this
        # standalone hook remains usable in both repo and packaged layouts.
        try:
            import composer as pipeline_composer
        except ImportError:
            from musubi import composer as pipeline_composer
        ambiguous_agents: set[str] = set()
        for entry in pipeline_composer.pipeline_stage_entries(pipeline_name):
            agent = entry["agent"]
            spawns = entry["spawns"]
            if isinstance(agent, str) and isinstance(spawns, list):
                if agent in ambiguous_agents:
                    continue
                if agent in out:
                    # Validation rejects duplicate resolved agents because
                    # policy is role-keyed, not stage-keyed. If boot
                    # validation is bypassed, grant neither stage anything.
                    out[agent] = []
                    ambiguous_agents.add(agent)
                else:
                    out[agent] = [s for s in spawns if isinstance(s, str)]
        _PIPELINE_SPAWNS_CACHE[str(path)] = (mtime, out)
        return out

    gen = data.get("generator") or {}
    if isinstance(gen, dict):
        for entry in (gen.get("agents") or []):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").lower()
            spawns = entry.get("spawns")
            if name and isinstance(spawns, list):
                out[name] = [s for s in spawns if isinstance(s, str)]
    ev = data.get("evaluator") or {}
    if isinstance(ev, dict):
        ev_spawns = ev.get("spawns")
        if isinstance(ev_spawns, list):
            # Evaluator role name comes from `evaluator.name` (defaulting to
            # "reviewer" for back-compat with feature-dev's yaml, which
            # doesn't declare it). PR 2b — H.1 needs this so code-review's
            # `synthesizer` evaluator gets its spawns keyed correctly.
            ev_name = (ev.get("name") or "reviewer").lower()
            out[ev_name] = [s for s in ev_spawns if isinstance(s, str)]
    _PIPELINE_SPAWNS_CACHE[str(path)] = (mtime, out)
    return out


def _reset_pipeline_spawns_cache() -> None:
    """Test hook — drop the cache so a mid-test rewrite is picked up."""
    _PIPELINE_SPAWNS_CACHE.clear()


# ── Agent-frontmatter spawn allow-lists ──────────────────────────────────
#
# `.github/agents/<role>.agent.md` may declare `spawn_allowlist:` in its YAML
# frontmatter — the workers that role may summon. It is AUTHORITATIVE WHEN
# PRESENT; the MAIN_SUBAGENT_ALLOWLIST constant above is the fail-closed
# fallback for the installed-wheel case (no `.github/` adjacent) and for roles
# whose file omits the field. This moves the spawn firewall toward data the user
# can edit, without losing the safe default. Cached by mtime, fail-soft.

_AGENT_SPAWNS_CACHE: dict[str, tuple[float, list[str] | None]] = {}


def _agents_root() -> Path:
    """Resolve `.github/agents/`. MUSUBI_ROOT wins when set."""
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        candidate = Path(env) / ".github" / "agents"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent / ".github" / "agents"


#: Purpose subdirectories scanned for a role's `.agent.md`, in precedence
#: order. Mirrors `musubi/agent/prompt_resolver.py`; the flat file stays the
#: last candidate for the legacy copies the feature-frozen extension reads.
_AGENT_MD_PURPOSE_DIRS = ("root", "workers", "meta")


def _agent_md_candidates(role: str) -> list[Path]:
    """Candidate `.agent.md` paths for `role` across the purpose-dir catalog."""
    base = _agents_root()
    filename = f"{role}.agent.md"
    out = [base / d / filename for d in _AGENT_MD_PURPOSE_DIRS]
    stages = base / "pipeline-stages"
    if stages.is_dir():
        out.extend(sorted(stages.glob(f"*/{filename}")))
    out.append(base / filename)
    return out


def _parse_spawn_allowlist_file(path: Path) -> list[str] | None:
    """`spawn_allowlist` from one `.agent.md` file's frontmatter.

    None when the file is missing/malformed or omits the field.
    Cached by mtime.
    """
    if not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _AGENT_SPAWNS_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    out: list[str] | None = None
    try:
        text = path.read_text(encoding="utf-8").lstrip()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                import yaml  # type: ignore[import-untyped]
                fm = yaml.safe_load(text[3:end]) or {}
                if isinstance(fm, dict) and isinstance(fm.get("spawn_allowlist"), list):
                    out = [s for s in fm["spawn_allowlist"] if isinstance(s, str)]
    except Exception:
        out = None
    _AGENT_SPAWNS_CACHE[str(path)] = (mtime, out)
    return out


def _frontmatter_spawn_allowlist(role: str) -> list[str] | None:
    """`spawn_allowlist` from the role's `.agent.md` frontmatter.

    Scans the purpose-dir catalog (`root/`, `workers/`, `meta/`,
    `pipeline-stages/*/`, then the flat legacy file); the first file that
    declares the field wins. None when no candidate declares it — the
    caller then falls back to the MAIN_SUBAGENT_ALLOWLIST constant.
    """
    safe = (role or "").strip().lower()
    if not safe or "/" in safe or ".." in safe:
        return None
    for path in _agent_md_candidates(safe):
        out = _parse_spawn_allowlist_file(path)
        if out is not None:
            return out
    return None


def _reset_agent_spawns_cache() -> None:
    """Test hook — drop the cache so a mid-test rewrite is picked up."""
    _AGENT_SPAWNS_CACHE.clear()


def main_subagent_allowlist(agent: str) -> list[str]:
    """Roles `agent` may EVER spawn — the firewall ceiling, before any
    pipeline narrowing. Frontmatter `spawn_allowlist:` wins when present; the
    MAIN_SUBAGENT_ALLOWLIST constant is the fail-closed fallback.
    """
    fm = _frontmatter_spawn_allowlist(agent)
    if fm is not None:
        return list(fm)
    return list(MAIN_SUBAGENT_ALLOWLIST.get(agent.lower(), []))


def _effective_spawn_roles(main_agent: str, pipeline_name: str | None) -> list[str]:
    """Resolve `main_agent`'s spawn list under `pipeline_name`.

    - main_agent == 'agent' OR pipeline_name is None →
      firewall entry verbatim (back-compat / agent path).
    - else → pipeline.yaml's `spawns:` for that agent ∩ firewall.
      If the pipeline declares no `spawns:` for the agent → [].

    The firewall is `main_subagent_allowlist` (frontmatter-authoritative,
    constant fallback), so the literal 'agent' string is no longer special —
    it is just the role whose declared `spawn_allowlist` happens to be broad.
    """
    agent = main_agent.lower()
    firewall = main_subagent_allowlist(agent)
    if agent == "agent" or pipeline_name is None:
        return list(firewall)
    declared = _load_pipeline_spawns(pipeline_name).get(agent)
    if declared is None:
        return []
    firewall_set = set(firewall)
    return [r for r in declared if r in firewall_set]


def check_tool_allowed(pipeline: str, agent: str, tool: str) -> bool:
    """Return True if the tool call is permitted, False otherwise.

    Unknown pipeline or agent → deny (fail-closed).
    """
    pipeline_rules = PIPELINE_POLICIES.get(pipeline)
    if pipeline_rules is None:
        return False
    allowed = pipeline_rules.get(agent.lower())
    if allowed is None:
        return False
    return tool in allowed


def deny_reason(pipeline: str, agent: str, tool: str) -> str:
    """Return a human-readable reason for a deny decision."""
    if pipeline not in PIPELINE_POLICIES:
        return f"Unknown pipeline: {pipeline!r}"
    if agent.lower() not in PIPELINE_POLICIES[pipeline]:
        return f"Agent {agent!r} has no policy entry in pipeline {pipeline!r}"
    allowed = PIPELINE_POLICIES[pipeline][agent.lower()]
    return (
        f"Tool {tool!r} is not permitted for agent {agent!r} in pipeline "
        f"{pipeline!r}. Allowed: {allowed}"
    )


# ── Sub-agent helpers ─────────────────────────────────────────────────────

def list_subagent_roles(
    main_agent: str, pipeline_name: str | None = None,
) -> list[str]:
    """Roles that `main_agent` is allowed to spawn under `pipeline_name`.

    `pipeline_name=None` returns the firewall verbatim — the back-compat
    path for the agent and for callers without a pipeline context.
    When `pipeline_name` is supplied, the result is the intersection of
    the pipeline.yaml-declared `spawns:` and the firewall (fail-closed:
    pipelines that omit the field get []).
    """
    return _effective_spawn_roles(main_agent, pipeline_name)


def check_subagent_allowed(
    main_agent: str, role: str, pipeline_name: str | None = None,
) -> bool:
    """True iff `main_agent` may spawn `role` under `pipeline_name`."""
    return role in _effective_spawn_roles(main_agent, pipeline_name)


def get_subagent_tools(role: str) -> list[str]:
    """Tools the role itself is allowed to use. [] if role unknown."""
    return list(SUBAGENT_POLICIES.get(role, []))


def effective_subagent_tools(
    main_agent: str,
    main_tools: list[str],
    role: str,
    requested_tools: list[str] | None = None,
) -> list[str]:
    """Compute the sub-agent's effective tool set.

    rule: SUBAGENT_POLICIES[role] ∩ main_tools ∩ (requested_tools or all).

    The intersection guarantees a sub-agent can never exceed its parent's
    permissions or the role's hard cap. `requested_tools=None` means the
    caller did not narrow further.
    """
    role_tools = SUBAGENT_POLICIES.get(role)
    if role_tools is None:
        return []
    main_set = set(main_tools)
    requested_set = set(requested_tools) if requested_tools is not None else None
    out: list[str] = []
    for t in role_tools:
        if t not in main_set:
            continue
        if requested_set is not None and t not in requested_set:
            continue
        out.append(t)
    return out


def subagent_deny_reason(
    main_agent: str, role: str, pipeline_name: str | None = None,
) -> str:
    """Human-readable reason a spawn was denied."""
    if role not in SUBAGENT_POLICIES:
        return (
            f"Unknown sub-agent role {role!r}. "
            f"Valid roles: {sorted(SUBAGENT_POLICIES.keys())}"
        )
    if (
        main_agent.lower() not in MAIN_SUBAGENT_ALLOWLIST
        and _frontmatter_spawn_allowlist(main_agent) is None
    ):
        return (
            f"Main agent {main_agent!r} has no spawn allow-list "
            f"(fail-closed)."
        )
    effective = _effective_spawn_roles(main_agent, pipeline_name)
    if pipeline_name and main_agent.lower() != "agent":
        return (
            f"Main agent {main_agent!r} may not spawn role {role!r} under "
            f"pipeline {pipeline_name!r}. "
            f"Declared spawns ∩ firewall = {sorted(effective)}."
        )
    return (
        f"Main agent {main_agent!r} may not spawn role {role!r}. "
        f"Allowed roles: {sorted(effective)}"
    )


# ── Phase G.2: startup-time policy validation ────────────────────────────
#
# `validate_policy_table` runs at harness boot (called from `init_db` →
# `validate_policies_or_raise`). Catches misconfiguration loud and
# early instead of at the first runtime tool call hours into a session.

_KNOWN_AGENT_NAMES: frozenset[str] = frozenset({
    "planner", "designer", "coder", "reviewer", "skill-builder",
    "agent", "summarizer",
    "explorer", "investigator", "reviewer-aux",
    "pipeline-builder",
    # Phase H.1 — /code-review pipeline roles.
    "scoper", "finder", "synthesizer",
})

# Tool names every policy entry must reference. Sourced from the union
# of pipeline + sub-agent tools currently shipping. Kept frozen so a
# typo in a future PIPELINE_POLICIES edit raises at boot.
_KNOWN_TOOL_NAMES: frozenset[str] = frozenset({
    "Read", "View", "Grep", "Glob", "List",
    "Write", "Edit",
    "Bash",
    "Errors",
})


def validate_policy_table() -> list[str]:
    """Walk PIPELINE_POLICIES, SUBAGENT_POLICIES, and
    MAIN_SUBAGENT_ALLOWLIST. Return human-readable error strings for
    every misconfiguration. Empty list ⇒ clean boot.
    """
    errors: list[str] = []

    # PIPELINE_POLICIES checks: agent names + tool names.
    for pipeline, agents in PIPELINE_POLICIES.items():
        if not isinstance(pipeline, str) or not pipeline:
            errors.append(f"PIPELINE_POLICIES has non-string key {pipeline!r}")
            continue
        if not isinstance(agents, dict):
            errors.append(
                f"PIPELINE_POLICIES[{pipeline!r}] must be a dict, "
                f"got {type(agents).__name__}"
            )
            continue
        for agent, tools in agents.items():
            if agent not in _KNOWN_AGENT_NAMES:
                errors.append(
                    f"PIPELINE_POLICIES[{pipeline!r}] references unknown "
                    f"agent {agent!r}. Known: {sorted(_KNOWN_AGENT_NAMES)}"
                )
            if not isinstance(tools, list):
                errors.append(
                    f"PIPELINE_POLICIES[{pipeline!r}][{agent!r}] must be "
                    f"a list, got {type(tools).__name__}"
                )
                continue
            for tool in tools:
                if tool not in _KNOWN_TOOL_NAMES:
                    errors.append(
                        f"PIPELINE_POLICIES[{pipeline!r}][{agent!r}] "
                        f"references unknown tool {tool!r}. "
                        f"Known: {sorted(_KNOWN_TOOL_NAMES)}"
                    )

    # SUBAGENT_POLICIES checks: tool names per role.
    for role, tools in SUBAGENT_POLICIES.items():
        if role not in _KNOWN_AGENT_NAMES:
            errors.append(
                f"SUBAGENT_POLICIES references unknown role {role!r}"
            )
        if not isinstance(tools, list):
            errors.append(
                f"SUBAGENT_POLICIES[{role!r}] must be a list, "
                f"got {type(tools).__name__}"
            )
            continue
        for tool in tools:
            if tool not in _KNOWN_TOOL_NAMES:
                errors.append(
                    f"SUBAGENT_POLICIES[{role!r}] references unknown "
                    f"tool {tool!r}"
                )

    # PIPELINE_POLICIES ↔ SUBAGENT_POLICIES sync: a role that exists in
    # both tables must carry the same tool set, so an ad-hoc spawn can
    # never exceed — or silently lag — what the same role gets inside a
    # pipeline. Divergence is legal only after the tables (and this
    # check) are changed together in an explicit design discussion.
    for pipeline, agents in PIPELINE_POLICIES.items():
        if not isinstance(agents, dict):
            continue  # shape error already reported above
        for agent, tools in agents.items():
            if agent not in SUBAGENT_POLICIES or not isinstance(tools, list):
                continue
            role_tools = SUBAGENT_POLICIES[agent]
            if not isinstance(role_tools, list):
                continue  # shape error already reported above
            if set(role_tools) != set(tools):
                errors.append(
                    f"SUBAGENT_POLICIES[{agent!r}] is out of sync with "
                    f"PIPELINE_POLICIES[{pipeline!r}][{agent!r}]: "
                    f"ad-hoc {sorted(role_tools)} vs pipeline "
                    f"{sorted(tools)}. A role in both tables must carry "
                    "identical tool sets."
                )

    # MAIN_SUBAGENT_ALLOWLIST checks: roles must be in SUBAGENT_POLICIES.
    for main, allowed_roles in MAIN_SUBAGENT_ALLOWLIST.items():
        if not isinstance(allowed_roles, list):
            errors.append(
                f"MAIN_SUBAGENT_ALLOWLIST[{main!r}] must be a list"
            )
            continue
        for role in allowed_roles:
            if role not in SUBAGENT_POLICIES:
                errors.append(
                    f"MAIN_SUBAGENT_ALLOWLIST[{main!r}] references role "
                    f"{role!r} not declared in SUBAGENT_POLICIES"
                )

    # Frontmatter spawn_allowlist checks (authoritative-when-present). Only runs
    # when `.github/agents/` is on disk — installed wheels skip this and the
    # constant governs. Every `.agent.md` anywhere in the purpose-dir catalog
    # is validated: each declared spawn role must be a known sub-agent role.
    agents_dir = _agents_root()
    if agents_dir.is_dir():
        for md in sorted(agents_dir.rglob("*.agent.md")):
            allow = _parse_spawn_allowlist_file(md)
            if allow is None:
                continue
            rel = md.relative_to(agents_dir)
            for r in allow:
                if r not in SUBAGENT_POLICIES:
                    errors.append(
                        f"{rel} spawn_allowlist references role {r!r} "
                        f"not declared in SUBAGENT_POLICIES"
                    )

    return errors


def validate_policies_or_raise() -> None:
    """Boot-time gate. Calls `validate_policy_table`; raises
    `RuntimeError` listing every issue if any are found. Called from
    `storage/db.init_db` so a bad policy table aborts harness startup
    instead of producing silent denials at first runtime tool call.
    """
    errors = validate_policy_table()
    if errors:
        bullets = "\n  - ".join(errors)
        raise RuntimeError(
            "Policy table validation failed (Phase G.2). "
            f"Fix `.github/...` or scripts/policy_engine.py:\n  - {bullets}"
        )
