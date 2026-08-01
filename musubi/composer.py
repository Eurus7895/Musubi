"""Composer — pipeline.yaml as the source of declarative composition truth.

musubi-tier: substrate
expires-when: never — Prompt assembly from .agent.md + skills; pure catalog reader.


Loads `.github/pipelines/<name>/pipeline.yaml` and resolves per-(stage, agent)
injection lists from it. Replaces the old `_STAGE_SKILL_MAP` static dict that
duplicated what each pipeline.yaml's `agents[].skill` and `evaluator.skill`
fields already declare.

This module owns *declarative* composition. Firewalls (which skills an agent
may ever load, which sub-agent roles a main agent may ever spawn) live in
`validation/context_builder.AGENT_SKILL_ALLOWLIST` and
`scripts/policy_engine.MAIN_SUBAGENT_ALLOWLIST` — they are the maximum sets;
pipeline.yaml narrows further.

The loader caches per (pipeline_name, mtime). It returns sane defaults when
pipeline.yaml is missing, malformed, or omits a field — same fail-soft posture
as `musubi_get_correction_rules`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

# PyYAML is loaded lazily inside `_load_pipeline_yaml` (same pattern as
# scripts/policy_engine.py). Older PyInstaller bundles of the harness were
# built before composer.py existed and may not include PyYAML — a top-level
# `import yaml` here would crash the server at boot in those bundles. With
# the lazy import + soft-fail, the harness still boots; `active_stages` and
# friends fall back to the canonical feature-dev defaults until a fresh
# bundle ships with PyYAML (see musubi/musubi.spec).

# Canonical feature-dev defaults — used as a fallback when a pipeline.yaml
# doesn't declare its `stage:` fields explicitly. New pipelines should always
# declare them.
_CANONICAL_AGENT_OUTPUT_STAGE: dict[str, str] = {
    "planner":  "plan",
    "designer": "design",
    "coder":    "code",
    "reviewer": "review",
}
_CANONICAL_STAGE_ORDER: list[str] = ["plan", "design", "code", "review"]

_ALLOWED_CHECK_TYPES = frozenset({
    "file_exists",
    "file_created_or_modified",
    "dom_count",
    "dom_distinct_text",
    "dom_text_set",
    "lint_clean",
    "named_command",
})
_STAGE_FIELDS = frozenset({
    "preset", "agent", "name", "stage", "spawns",
    "allowed_checks", "allowed_commands", "max_iterations",
})


class PipelineRecipeError(ValueError):
    """A governed recipe declaration is malformed or unsafe to run."""


@dataclass(frozen=True)
class NamedCommandSpec:
    command_id: str
    argv: tuple[str, ...]
    timeout_seconds: int
    root: str = "musubi"
    cwd: str = "."


@dataclass(frozen=True)
class StageRecipe:
    stage: str
    agent: str
    preset: str | None
    spawns: tuple[str, ...]
    allowed_checks: tuple[str, ...]
    allowed_commands: tuple[str, ...]
    max_iterations: int


@dataclass(frozen=True)
class PipelineRecipeContract:
    name: str
    stages: tuple[StageRecipe, ...]
    commands: Mapping[str, NamedCommandSpec]


def _pipelines_root() -> Path:
    """Resolve `.github/pipelines/`. MUSUBI_ROOT wins when set (packaged
    extension), otherwise walk up from this file."""
    env = os.environ.get("MUSUBI_ROOT")
    if env:
        candidate = Path(env) / ".github" / "pipelines"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent / ".github" / "pipelines"


_yaml_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _load_pipeline_yaml(pipeline_name: str) -> dict[str, Any]:
    """Return the parsed pipeline.yaml for `pipeline_name`, or {} on miss.

    Cached by (path, mtime). Safe to call repeatedly.
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
    cached = _yaml_cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        # Lazy import — see module docstring. ImportError here means PyYAML
        # isn't in the bundle; behave as if the file was unparseable.
        import yaml  # type: ignore[import-untyped]
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    _yaml_cache[str(path)] = (mtime, data)
    return data


_presets_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def _load_presets() -> dict[str, dict[str, Any]]:
    """Load the preset catalog from `.github/pipelines/presets/*.yaml`.

    Each preset is a reusable worker/stage building block a user drops into a
    pipeline (the data model a future drag-and-drop UI reads/writes):
        id:    unique preset id (defaults to the file stem)
        agent: the role it runs (a `.github/agents/<role>.agent.md`)
        stage: default stage name (defaults to the agent name)
        skill: optional skill id/path
    Returns {id: {agent, stage, skill}}. Cached by directory mtime, fail-soft.
    """
    root = _pipelines_root() / "presets"
    if not root.is_dir():
        return {}
    try:
        mtime = root.stat().st_mtime
    except OSError:
        return {}
    cached = _presets_cache.get(str(root))
    if cached and cached[0] == mtime:
        return cached[1]
    out: dict[str, dict[str, Any]] = {}
    try:
        import yaml  # type: ignore[import-untyped]
        for f in sorted(root.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            agent = data.get("agent")
            if not isinstance(agent, str) or not agent.strip():
                continue
            pid = str(data.get("id") or f.stem)
            out[pid] = {
                "agent": agent.strip().lower(),
                "stage": data.get("stage"),
                "skill": data.get("skill"),
            }
    except Exception:
        return {}
    _presets_cache[str(root)] = (mtime, out)
    return out


def _resolve_stage_entry(
    entry: Any, presets: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    """Resolve one `stages:` entry to (agent, stage).

    Supports a preset reference (`{preset: id, stage?: override}`) or an explicit
    entry (`{agent|name: role, stage?: name}`). None when unresolvable.
    """
    if not isinstance(entry, dict):
        return None
    if entry.get("preset"):
        p = presets.get(str(entry["preset"]))
        if not p:
            return None
        agent = p["agent"]
        stage = entry.get("stage") or p.get("stage") or agent
    else:
        raw = entry.get("agent") or entry.get("name") or ""
        if not isinstance(raw, str) or not raw.strip():
            return None
        agent = raw.strip().lower()
        stage = entry.get("stage") or _CANONICAL_AGENT_OUTPUT_STAGE.get(agent) or agent
    return (agent, str(stage))


def _flat_stage_entries(data: dict[str, Any]) -> list[dict[str, object]]:
    """Normalize every resolvable entry in a flat ``stages:`` list.

    Malformed spawn values are projected fail-closed (only non-empty strings
    survive); :func:`validate_catalog` reports declaration errors from the raw
    entries before a pipeline can run.
    """
    stages_list = data.get("stages")
    if not isinstance(stages_list, list):
        return []
    presets = _load_presets()
    out: list[dict[str, object]] = []
    for entry in stages_list:
        resolved = _resolve_stage_entry(entry, presets)
        if resolved is None:
            continue
        raw_spawns = entry.get("spawns")
        spawns = (
            [
                role.strip().lower()
                for role in raw_spawns
                if isinstance(role, str) and role.strip()
            ]
            if isinstance(raw_spawns, list)
            else []
        )
        out.append({
            "agent": resolved[0],
            "stage": resolved[1],
            "preset": str(entry.get("preset") or ""),
            "spawns": spawns,
        })
    return out


def pipeline_stage_entries(pipeline_name: str) -> list[dict[str, object]]:
    """Return the canonical normalized projection of flat pipeline stages."""
    return _flat_stage_entries(_load_pipeline_yaml(pipeline_name))


def _strict_string_list(raw: Any, *, field: str, stage: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PipelineRecipeError(
            f"stage {stage!r} field {field!r} must be a list"
        )
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise PipelineRecipeError(
                f"stage {stage!r} field {field!r} contains a non-string value"
            )
        normalized = value.strip().lower()
        if normalized in values:
            raise PipelineRecipeError(
                f"stage {stage!r} field {field!r} contains duplicate "
                f"value {normalized!r}"
            )
        values.append(normalized)
    return tuple(values)


def _strict_commands(data: dict[str, Any]) -> Mapping[str, NamedCommandSpec]:
    raw_checks = data.get("checks") or {}
    if not isinstance(raw_checks, dict):
        raise PipelineRecipeError("checks must be a mapping of command ids")
    commands: dict[str, NamedCommandSpec] = {}
    for raw_id, raw_spec in raw_checks.items():
        command_id = str(raw_id).strip()
        if not command_id or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", command_id):
            raise PipelineRecipeError(f"invalid command id {raw_id!r}")
        if not isinstance(raw_spec, dict):
            raise PipelineRecipeError(f"command {command_id!r} must be a mapping")
        unknown = set(raw_spec) - {
            "type", "argv", "timeout_seconds", "root", "cwd",
        }
        if unknown:
            raise PipelineRecipeError(
                f"command {command_id!r} has unknown field(s) {sorted(unknown)}"
            )
        if raw_spec.get("type") != "command":
            raise PipelineRecipeError(
                f"command {command_id!r} must declare type 'command'"
            )
        raw_argv = raw_spec.get("argv")
        if (
            not isinstance(raw_argv, list)
            or not raw_argv
            or any(not isinstance(arg, str) or not arg for arg in raw_argv)
        ):
            raise PipelineRecipeError(
                f"command {command_id!r} argv must be a non-empty string list"
            )
        timeout = raw_spec.get("timeout_seconds", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise PipelineRecipeError(
                f"command {command_id!r} timeout_seconds must be positive"
            )
        commands[command_id] = NamedCommandSpec(
            command_id=command_id,
            argv=tuple(raw_argv),
            timeout_seconds=timeout,
            root=str(raw_spec.get("root") or "musubi"),
            cwd=str(raw_spec.get("cwd") or "."),
        )
    return MappingProxyType(commands)


def load_pipeline_contract(pipeline_name: str) -> PipelineRecipeContract:
    """Load the strict runnable contract for a flat-stage pipeline.

    Compatibility helpers remain fail-soft for historical reads. This entry
    point is deliberately fail-closed because its result governs runtime
    checks, retries, and command authority.
    """
    data = _load_pipeline_yaml(pipeline_name)
    if not data:
        raise PipelineRecipeError(f"pipeline {pipeline_name!r} is missing or invalid")
    raw_stages = data.get("stages")
    if not isinstance(raw_stages, list) or len(raw_stages) < 2:
        raise PipelineRecipeError("runnable pipeline needs at least two flat stages")

    commands = _strict_commands(data)
    presets = _load_presets()
    stages: list[StageRecipe] = []
    seen_stages: set[str] = set()
    for index, raw_stage in enumerate(raw_stages):
        if not isinstance(raw_stage, dict):
            raise PipelineRecipeError(f"stage {index} must be a mapping")
        unknown = set(raw_stage) - _STAGE_FIELDS
        if unknown:
            raise PipelineRecipeError(
                f"stage {index} has unknown field(s) {sorted(unknown)}"
            )
        resolved = _resolve_stage_entry(raw_stage, presets)
        if resolved is None:
            raise PipelineRecipeError(f"stage {index} has no valid agent")
        agent, stage = resolved
        if stage in seen_stages:
            raise PipelineRecipeError(f"duplicate stage {stage!r}")
        seen_stages.add(stage)

        allowed_checks = _strict_string_list(
            raw_stage.get("allowed_checks"), field="allowed_checks", stage=stage,
        )
        unknown_checks = set(allowed_checks) - _ALLOWED_CHECK_TYPES
        if unknown_checks:
            raise PipelineRecipeError(
                f"stage {stage!r} declares unknown check(s) "
                f"{sorted(unknown_checks)}"
            )
        allowed_commands = _strict_string_list(
            raw_stage.get("allowed_commands"),
            field="allowed_commands",
            stage=stage,
        )
        unknown_commands = set(allowed_commands) - set(commands)
        if unknown_commands:
            raise PipelineRecipeError(
                f"stage {stage!r} declares unknown command(s) "
                f"{sorted(unknown_commands)}"
            )
        max_iterations = raw_stage.get("max_iterations", 1)
        if (
            isinstance(max_iterations, bool)
            or not isinstance(max_iterations, int)
            or not 1 <= max_iterations <= 3
        ):
            raise PipelineRecipeError(
                f"stage {stage!r} max_iterations must be between 1 and 3"
            )
        if max_iterations > 1 and not allowed_checks:
            raise PipelineRecipeError(
                f"stage {stage!r} with max_iterations > 1 requires allowed_checks"
            )
        spawns = _strict_string_list(
            raw_stage.get("spawns"), field="spawns", stage=stage,
        )
        stages.append(StageRecipe(
            stage=stage,
            agent=agent,
            preset=str(raw_stage.get("preset") or "") or None,
            spawns=spawns,
            allowed_checks=allowed_checks,
            allowed_commands=allowed_commands,
            max_iterations=max_iterations,
        ))

    return PipelineRecipeContract(
        name=str(data.get("name") or pipeline_name),
        stages=tuple(stages),
        commands=commands,
    )


def stage_recipe(pipeline_name: str, stage: str) -> StageRecipe | None:
    """Return one validated stage ceiling, or None when it is not declared."""
    contract = load_pipeline_contract(pipeline_name)
    return next((item for item in contract.stages if item.stage == stage), None)


_SKILL_PATH_RE = re.compile(r"^skills/([^/]+)/SKILL\.md$")


def _skill_path_to_id(skill_field: Any) -> str | None:
    """Convert `skill:` field value to a bare skill_id.

    Accepts: "skills/api-design/SKILL.md" → "api-design"
             "api-design"                  → "api-design"  (bare id passthrough)
             None / "" / null              → None
    """
    if not skill_field or not isinstance(skill_field, str):
        return None
    m = _SKILL_PATH_RE.match(skill_field.strip())
    if m:
        return m.group(1)
    # Bare id passthrough (no slash, no .md suffix).
    if "/" not in skill_field and not skill_field.endswith(".md"):
        return skill_field.strip() or None
    return None


def _pipeline_stage_chain(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Walk a parsed pipeline.yaml and return [(agent_name, stage_name), ...]
    in order — generator stages first, evaluator last.

    Stage names come from each agent entry's `stage:` field when declared.
    When omitted, the canonical map is consulted as a fallback (so feature-dev's
    existing yaml still resolves correctly without a migration).

    Preset-composed form (Increment 6): when the pipeline declares a flat
    `stages:` list, each entry is resolved against the preset catalog (or given
    explicitly) and the last entry is the evaluator. This is the form users
    author by dropping presets into a pipeline.
    """
    stages_list = data.get("stages")
    if isinstance(stages_list, list) and stages_list:
        return [
            (str(entry["agent"]), str(entry["stage"]))
            for entry in _flat_stage_entries(data)
        ]

    out: list[tuple[str, str]] = []
    gen = data.get("generator") or {}
    if isinstance(gen, dict):
        for entry in (gen.get("agents") or []):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").lower()
            if not name:
                continue
            stage = entry.get("stage")
            if not isinstance(stage, str) or not stage:
                stage = _CANONICAL_AGENT_OUTPUT_STAGE.get(name)
            if stage:
                out.append((name, stage))
    ev = data.get("evaluator") or {}
    if isinstance(ev, dict):
        ev_name = (ev.get("name") or "reviewer").lower()
        ev_stage = ev.get("stage")
        if not isinstance(ev_stage, str) or not ev_stage:
            ev_stage = _CANONICAL_AGENT_OUTPUT_STAGE.get(ev_name) or "review"
        out.append((ev_name, ev_stage))
    return out


def active_stages(pipeline_name: str) -> list[str]:
    """Ordered stage names this pipeline runs. Generator stages first, then
    the evaluator. Falls back to the canonical feature-dev list when the
    pipeline.yaml is missing or declares nothing useful.

    Used by the runner (TS) to iterate, and by the read/write guards in
    server.py to reject calls naming an inactive stage.
    """
    data = _load_pipeline_yaml(pipeline_name)
    if not data:
        return list(_CANONICAL_STAGE_ORDER)
    chain = _pipeline_stage_chain(data)
    if not chain:
        return list(_CANONICAL_STAGE_ORDER)
    return [stage for (_, stage) in chain]


def output_stage_for_agent(
    pipeline_name: str, agent_name: str,
) -> str | None:
    """The stage `agent_name` writes under `pipeline_name`, or None if the
    agent doesn't appear in this pipeline."""
    agent = agent_name.lower()
    data = _load_pipeline_yaml(pipeline_name)
    if data:
        for entry_agent, stage in _pipeline_stage_chain(data):
            if entry_agent == agent:
                return stage
    # Fallback for legacy callers that don't pass a pipeline.
    return _CANONICAL_AGENT_OUTPUT_STAGE.get(agent)


def agent_for_stage(pipeline_name: str, stage: str) -> str | None:
    """The agent name that writes `stage` under `pipeline_name`, or None."""
    data = _load_pipeline_yaml(pipeline_name)
    if data:
        for agent, st in _pipeline_stage_chain(data):
            if st == stage:
                return agent
    # Fallback: reverse canonical lookup.
    for agent, st in _CANONICAL_AGENT_OUTPUT_STAGE.items():
        if st == stage:
            return agent
    return None


def evaluator_input_stage(pipeline_name: str) -> str | None:
    """The stage the evaluator reads as input — i.e., the prior stage to the
    last entry in the chain. Used by `_STAGE_PERMISSIONS` to lock the
    evaluator's view to one stage (the firewall invariant). For feature-dev
    this is `code`; for code-review it's `findings`.

    Returns None when the chain has fewer than 2 entries (a pipeline with no
    generator before its evaluator is malformed).
    """
    data = _load_pipeline_yaml(pipeline_name)
    if data:
        chain = _pipeline_stage_chain(data)
        if len(chain) >= 2:
            return chain[-2][1]
    # Fallback to feature-dev's canonical shape.
    return "code"


def _prior_stage(pipeline_name: str, agent: str) -> str | None:
    """Stage `agent` reads as its primary input under `pipeline_name`.

    For an agent at chain index i > 0, that's the stage at index i-1.
    For an agent at chain index 0 (the first generator), there is no prior
    stage — return None so no skill injection happens on the "request" read.
    """
    agent = agent.lower()
    data = _load_pipeline_yaml(pipeline_name)
    if data:
        chain = _pipeline_stage_chain(data)
        for i, (name, _) in enumerate(chain):
            if name == agent:
                if i == 0:
                    return None
                return chain[i - 1][1]
        # Agent not in this pipeline's chain.
        return None
    # Fallback: canonical lookup.
    out = _CANONICAL_AGENT_OUTPUT_STAGE.get(agent)
    if out is None:
        return None
    idx = _CANONICAL_STAGE_ORDER.index(out)
    if idx == 0:
        return None
    return _CANONICAL_STAGE_ORDER[idx - 1]


def injected_skill_ids(
    pipeline_name: str, stage: str, agent_name: str,
) -> list[str]:
    """Skill IDs to inject when `agent_name` reads `stage` under `pipeline_name`.

    Mechanical rule: an agent's declared `skill:` is injected when that agent
    reads its prior stage. The reviewer's skill comes from `evaluator.skill`;
    everyone else's comes from `generator.agents[].skill`.

    Returns [] when:
      - pipeline.yaml missing / malformed
      - `(stage, agent)` doesn't match the agent's prior-stage read
      - skill field is null or unrecognised

    The caller (server.musubi_read_stage) intersects with
    AGENT_SKILL_ALLOWLIST before actually loading skill content — that is the
    firewall; this function is the declaration.
    """
    agent = agent_name.lower()
    if _prior_stage(pipeline_name, agent) != stage:
        return []
    data = _load_pipeline_yaml(pipeline_name)
    if not data:
        return []
    # Evaluator branch — the evaluator's name lives in `evaluator.name`
    # (defaulting to "reviewer" for back-compat). Its skill comes from
    # `evaluator.skill`.
    ev = data.get("evaluator") or {}
    if isinstance(ev, dict):
        ev_name = (ev.get("name") or "reviewer").lower()
        if agent == ev_name:
            sid = _skill_path_to_id(ev.get("skill"))
            return [sid] if sid else []
    # Generator branch — agents[].skill keyed by `name`.
    gen = data.get("generator") or {}
    agents = gen.get("agents") if isinstance(gen, dict) else None
    if not isinstance(agents, list):
        return []
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        if (entry.get("name") or "").lower() != agent:
            continue
        sid = _skill_path_to_id(entry.get("skill"))
        return [sid] if sid else []
    return []


def declared_stage_skill(pipeline_name: str, role: str) -> str | None:
    """The skill id `pipeline.yaml` declares for `role`, or None.

    A pipeline is the compliance path: its stages are a written recipe, and
    every stage's procedure is meant to be declared in that recipe rather than
    chosen at runtime. `generator.agents[].skill` and `evaluator.skill` have
    carried those declarations since feature-dev shipped — the standalone
    runner simply never read them, and asked a text ranker instead.

    This differs from `injected_skill_ids` in the question it answers.
    That one asks "which skill accompanies agent X when it READS stage Y",
    gated on `_prior_stage`, and serves the stage-read injection path. This
    one asks the flat question a spawn needs: *what does the recipe say this
    role runs?*

    Returns None for a missing/malformed recipe, an unlisted role, or an
    explicit `skill: null`. The caller intersects with AGENT_SKILL_ALLOWLIST —
    a recipe declares, it never widens (HI #3).
    """
    agent = (role or "").strip().lower()
    if not agent:
        return None
    data = _load_pipeline_yaml(pipeline_name)
    if not data:
        return None
    ev = data.get("evaluator") or {}
    if isinstance(ev, dict) and (ev.get("name") or "reviewer").lower() == agent:
        return _skill_path_to_id(ev.get("skill")) or None
    gen = data.get("generator") or {}
    agents = gen.get("agents") if isinstance(gen, dict) else None
    if isinstance(agents, list):
        for entry in agents:
            if not isinstance(entry, dict):
                continue
            if (entry.get("name") or "").lower() == agent:
                return _skill_path_to_id(entry.get("skill")) or None
    return None


def validate_catalog() -> list[str]:
    """Validate the preset catalog and every preset-composed pipeline against
    the agent catalog. Fail-closed: an unknown agent, an unresolvable preset
    reference, or a too-short chain is an error. Pipelines using the legacy
    generator/evaluator shape are left to `policy_engine` and skipped here.
    Returns human-readable error strings; empty ⇒ clean.
    """
    errors: list[str] = []
    agents_root = _pipelines_root().parent / "agents"
    known_agents: set[str] = set()
    if agents_root.is_dir():
        # The purpose-dir catalog is canonical (root/, workers/,
        # pipeline-stages/*/); the remaining flat files are extension-only
        # legacy copies. Both count as "known".
        known_agents = {
            f.name[: -len(".agent.md")]
            for f in agents_root.rglob("*.agent.md")
        }

    presets = _load_presets()
    for pid, p in presets.items():
        if known_agents and p["agent"] not in known_agents:
            errors.append(
                f"preset {pid!r} references unknown agent {p['agent']!r}"
            )

    root = _pipelines_root()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name == "presets":
                continue
            stages_list = _load_pipeline_yaml(d.name).get("stages")
            if not isinstance(stages_list, list) or not stages_list:
                continue  # legacy generator/evaluator shape — not ours to check
            resolved_count = 0
            resolved_agents: set[str] = set()
            for entry in stages_list:
                if not isinstance(entry, dict):
                    errors.append(f"pipeline {d.name!r} has a non-dict stage entry")
                    continue
                if entry.get("preset") and str(entry["preset"]) not in presets:
                    errors.append(
                        f"pipeline {d.name!r} references unknown preset "
                        f"{entry['preset']!r}"
                    )
                    continue
                resolved = _resolve_stage_entry(entry, presets)
                if resolved is None:
                    errors.append(
                        f"pipeline {d.name!r} has an unresolvable stage entry: "
                        f"{entry!r}"
                    )
                elif known_agents and resolved[0] not in known_agents:
                    errors.append(
                        f"pipeline {d.name!r} stage references unknown agent "
                        f"{resolved[0]!r}"
                    )
                else:
                    resolved_count += 1
                    if resolved[0] in resolved_agents:
                        errors.append(
                            f"pipeline {d.name!r} stage {resolved[1]!r} "
                            f"agent {resolved[0]!r} has a duplicate resolved "
                            "agent; role-keyed spawn declarations require "
                            "one stage per agent"
                        )
                    resolved_agents.add(resolved[0])
                    if "spawns" not in entry:
                        continue
                    raw_spawns = entry["spawns"]
                    context = (
                        f"pipeline {d.name!r} stage {resolved[1]!r} "
                        f"agent {resolved[0]!r} spawns"
                    )
                    if not isinstance(raw_spawns, list):
                        errors.append(
                            f"{context} must be a list, got "
                            f"{type(raw_spawns).__name__}"
                        )
                        continue
                    normalized: list[str] = []
                    for role in raw_spawns:
                        if not isinstance(role, str):
                            errors.append(
                                f"{context} has non-string role {role!r}"
                            )
                            continue
                        normalized.append(role.strip().lower())
                    seen: set[str] = set()
                    for role in normalized:
                        if role in seen:
                            errors.append(
                                f"{context} has duplicate role {role!r}"
                            )
                        seen.add(role)

                    # Lazy import avoids making policy_engine part of
                    # composer's import-time dependency surface.
                    try:
                        import policy_engine
                    except ImportError:  # source tree without scripts on path
                        from scripts import policy_engine  # type: ignore[no-redef]
                    firewall = set(
                        policy_engine.main_subagent_allowlist(resolved[0])
                    )
                    for role in normalized:
                        if role not in policy_engine.SUBAGENT_POLICIES:
                            errors.append(
                                f"{context} references unknown role {role!r}"
                            )
                        elif role not in firewall:
                            errors.append(
                                f"{context} role {role!r} is outside the "
                                "effective firewall allowlist"
                            )
            if resolved_count < 2:
                errors.append(
                    f"pipeline {d.name!r} needs at least 2 resolvable stages "
                    f"(got {resolved_count})"
                )
    return errors


def validate_catalog_or_raise() -> None:
    """Boot gate. Raises RuntimeError listing every catalog error, mirroring
    `policy_engine.validate_policies_or_raise`. Called from server startup so a
    malformed preset/pipeline aborts the harness loudly instead of fail-opening.
    """
    errors = validate_catalog()
    if errors:
        bullets = "\n  - ".join(errors)
        raise RuntimeError(
            "Pipeline/preset catalog validation failed. "
            f"Fix `.github/pipelines/`:\n  - {bullets}"
        )


def reset_cache() -> None:
    """Test hook — drop the parsed-yaml + preset caches so a mid-test rewrite
    is read."""
    _yaml_cache.clear()
    _presets_cache.clear()
