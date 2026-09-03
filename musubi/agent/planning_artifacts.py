"""Persist the planner's human plan and machine manifest as separate files.

musubi-tier: substrate
expires-when: never - a readable plan and a bounded machine declaration remain
  useful regardless of model strength; the driver validates and stores both
  without granting the read-only planner mutation tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.manifest import (
    ChangeManifest,
    parse_change_manifest,
    parse_change_manifest_object,
)

MAX_PLAN_BYTES = 64 * 1024
_PLAN_OPEN = "<plan>"
_PLAN_CLOSE = "</plan>"


@dataclass(frozen=True)
class PlanningArtifacts:
    plan_markdown: str
    manifest: ChangeManifest


def parse_planning_artifacts(text: str) -> PlanningArtifacts | None:
    """Return one bounded plan/manifest pair from a planner response.

    Exactly one literal tag pair of each kind is required. The manifest parser
    owns its own strict JSON contract; this function adds only the human plan
    contract so a valid radius declaration can never advance without the plan
    the next worker needs.
    """
    source = text or ""
    if source.count(_PLAN_OPEN) != 1 or source.count(_PLAN_CLOSE) != 1:
        return None
    start = source.find(_PLAN_OPEN) + len(_PLAN_OPEN)
    end = source.find(_PLAN_CLOSE)
    if end < start:
        return None
    plan = source[start:end].strip()
    if not plan:
        return None
    try:
        if len(plan.encode("utf-8")) > MAX_PLAN_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    manifest = parse_change_manifest(source)
    if manifest is None:
        return None
    return PlanningArtifacts(plan_markdown=plan, manifest=manifest)


def goal_artifact_key(chat_id: str | None, session_id: str) -> str:
    """Stable, path-safe key for one conversation goal.

    A chat id is stable across follow-up turns, so its plan is replaced rather
    than split into one directory per message. Standalone runs have no chat id
    and fall back to their unique root session.
    """
    source = (chat_id or session_id).strip()
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def persist_planning_artifacts(
    text: str,
    target_dir: Path,
) -> tuple[Path, Path] | None:
    """Validate and atomically replace ``plan.md`` and ``manifest.json``."""
    artifacts = parse_planning_artifacts(text)
    if artifacts is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    plan_path = target_dir / "plan.md"
    manifest_path = target_dir / "manifest.json"
    manifest_text = json.dumps(
        asdict(artifacts.manifest),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_write(plan_path, artifacts.plan_markdown.rstrip() + "\n")
    _atomic_write(manifest_path, manifest_text)
    return plan_path, manifest_path


def persist_planning_contract(
    plan_markdown: str,
    manifest_object: object,
    target_dir: Path,
) -> tuple[tuple[Path, Path], PlanningArtifacts] | None:
    """Persist Root's structured plan contract without planner response tags."""
    plan = str(plan_markdown or "").strip()
    if not plan:
        return None
    try:
        if len(plan.encode("utf-8")) > MAX_PLAN_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    manifest = parse_change_manifest_object(manifest_object)
    if manifest is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    plan_path = target_dir / "plan.md"
    manifest_path = target_dir / "manifest.json"
    manifest_text = json.dumps(
        asdict(manifest),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_write(plan_path, plan.rstrip() + "\n")
    _atomic_write(manifest_path, manifest_text)
    return (
        (plan_path, manifest_path),
        PlanningArtifacts(plan_markdown=plan, manifest=manifest),
    )


def persist_goal_contract(contract: object, target_dir: Path) -> Path:
    """Atomically persist a validated JSON-shaped Goal Contract artifact."""
    if not isinstance(contract, dict):
        raise ValueError("goal contract must be a JSON object")
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "goal_contract.json"
    _atomic_write(
        path,
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
