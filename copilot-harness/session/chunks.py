"""Phase G.1.7 — chunked execution by planner task.

harness-tier: substrate
expires-when: never — Frozen dataclass for per-task design slice.


When a designer emits a fan-out design (many modules across multiple
tasks), the coder's single LLM round-trip blows the output-token cap
("Response too long"). This module groups the design's modules by the
task they implement so the runner can run the coder + reviewer once
per chunk instead of once for the whole design.

Public API:
    compute_chunks(plan, design) -> list[Chunk]

A Chunk has:
    chunk_id   — task ID from the plan (e.g. "T1"); unique within a session
    task_label — one-line label rendered in chat ("T1 — write tests …")
    file_paths — modules in this chunk, in design order

Chunking rules (in order):
    1. If `len(plan.tasks) <= 1` OR `len(design.modules) <= 1` ⇒ single
       global chunk (returns []) so the runner falls back to today's path.
    2. For each module, look for an explicit `task_id` field. If absent,
       regex-extract the FIRST task ID matching `T\\d+` from the module's
       `purpose` text (the convention the designer uses today). Modules
       with no extracted task land in a synthetic "T?" bucket.
    3. Group by extracted task. Order chunks in plan-task order. Drop any
       task that has zero modules.
    4. If only one non-empty chunk results, return [] (fallback to single).

The synthetic "T?" chunk runs LAST when present so explicit task chunks
finish first. If "T?" is the only chunk, treat as no chunking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Chunk:
    """A per-task slice of the designer's modules.

    Frozen so the runner can pass instances around without worrying
    about mutation. `file_paths` is a tuple for the same reason.
    """
    chunk_id: str
    task_label: str
    file_paths: tuple[str, ...]


_TASK_ID_RE = re.compile(r"\b(T\d+)\b")
# Synthetic bucket for modules whose task can't be inferred. Sorts last.
_UNKNOWN_TASK_ID = "T?"


def _coerce_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _extract_task_id(module: dict, plan_task_ids: set[str]) -> str:
    """Return the task ID a module belongs to, or `_UNKNOWN_TASK_ID`.

    Resolution order:
      1. Explicit `task_id` (string) field on the module.
      2. Explicit `tasks_addressed` (list[str]) — first matching plan task.
      3. Regex-extract the first `T\\d+` token from `purpose` that matches
         a plan task.
    """
    explicit = _coerce_str(module.get("task_id"))
    if explicit and explicit in plan_task_ids:
        return explicit
    addressed = module.get("tasks_addressed")
    if isinstance(addressed, list):
        for entry in addressed:
            if isinstance(entry, str) and entry in plan_task_ids:
                return entry
    purpose = _coerce_str(module.get("purpose"))
    if purpose:
        for match in _TASK_ID_RE.findall(purpose):
            if match in plan_task_ids:
                return match
    return _UNKNOWN_TASK_ID


def _task_label(task: dict, fallback_id: str) -> str:
    """Build `<id> — <description>` label, capped at 80 chars."""
    desc = _coerce_str(task.get("description"))
    tid = _coerce_str(task.get("id")) or fallback_id
    if not desc:
        return tid
    if len(desc) > 80:
        desc = desc[:77].rstrip() + "…"
    return f"{tid} — {desc}"


def compute_chunks(plan: Any, design: Any) -> list[Chunk]:
    """Group `design.modules` by planner task. Returns [] for non-chunked.

    `plan` and `design` are the raw JSON dicts read from the harness.
    Returning `[]` means "no chunking — run the coder once over the
    whole design"; that's the legacy path the runner still uses.

    Raises nothing — malformed inputs degrade to `[]`.
    """
    if not isinstance(plan, dict) or not isinstance(design, dict):
        return []
    plan_tasks = plan.get("tasks")
    modules = design.get("modules")
    if not isinstance(plan_tasks, list) or not isinstance(modules, list):
        return []
    if len(plan_tasks) <= 1 or len(modules) <= 1:
        return []

    # Build the plan-task lookup (id → task) preserving order.
    plan_task_ids: list[str] = []
    plan_task_by_id: dict[str, dict] = {}
    for task in plan_tasks:
        if not isinstance(task, dict):
            continue
        tid = _coerce_str(task.get("id"))
        if not tid:
            continue
        plan_task_ids.append(tid)
        plan_task_by_id[tid] = task
    plan_task_id_set = set(plan_task_ids)
    if not plan_task_id_set:
        return []

    # Group modules by extracted task ID.
    groups: dict[str, list[str]] = {tid: [] for tid in plan_task_ids}
    groups[_UNKNOWN_TASK_ID] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        path = _coerce_str(module.get("file"))
        if not path:
            continue
        tid = _extract_task_id(module, plan_task_id_set)
        groups[tid].append(path)

    # Materialise chunks in plan order; unknown bucket runs last.
    chunks: list[Chunk] = []
    for tid in plan_task_ids:
        paths = tuple(groups.get(tid, []))
        if not paths:
            continue
        chunks.append(Chunk(
            chunk_id=tid,
            task_label=_task_label(plan_task_by_id.get(tid, {}), tid),
            file_paths=paths,
        ))
    unknown_paths = tuple(groups.get(_UNKNOWN_TASK_ID, []))
    if unknown_paths:
        chunks.append(Chunk(
            chunk_id=_UNKNOWN_TASK_ID,
            task_label="Unassigned modules",
            file_paths=unknown_paths,
        ))

    # If only one non-empty chunk results, no benefit to chunking.
    if len(chunks) <= 1:
        return []
    return chunks


def filter_design_for_chunk(design: Any, chunk: Chunk) -> Any:
    """Return a shallow copy of `design` whose `modules` list contains
    only the modules in `chunk.file_paths`.

    The coder reads this filtered design instead of the full one, which
    keeps each per-chunk LLM call within the model's output cap. Other
    top-level fields (summary, tasks_addressed, dependencies, …) pass
    through unchanged so the coder still has the global context it
    needs to write integration-aware code.
    """
    if not isinstance(design, dict):
        return design
    modules = design.get("modules")
    if not isinstance(modules, list):
        return design
    keep = set(chunk.file_paths)
    filtered: list[Any] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        path = module.get("file")
        if isinstance(path, str) and path in keep:
            filtered.append(module)
    out = dict(design)
    out["modules"] = filtered
    out["_chunk_id"] = chunk.chunk_id
    out["_chunk_label"] = chunk.task_label
    return out
