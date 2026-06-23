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
from pathlib import Path
from typing import Any

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
    """
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


def reset_cache() -> None:
    """Test hook — drop the parsed-yaml cache so a mid-test rewrite is read."""
    _yaml_cache.clear()
