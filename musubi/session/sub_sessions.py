"""Sub-session lifecycle (Phase A.1).

musubi-tier: ephemeral
expires-when: models gain reliable native multi-agent tool-use
cost-lever: deletes ~400 lines of lifecycle + cascade-abandon machinery
(what: Sub-agent split lifecycle.)


A *sub-session* is the row for an agent-spawned-by-another-agent invocation —
the agent (or a pipeline stage that opts in) calls
`musubi_spawn_subagent`, the harness records the spawn here, the extension
runs the sub-agent, then the harness records the terminal result.

Lifecycle:

    spawn() ──► insert row, status='running'              (this module)
              ─► return handle_id (uuid hex[:12])
              ─► caller drives the sub-agent
              ─► caller calls complete()
    complete() ► update row to status in
                    {'done', 'failed', 'escalated', 'abandoned'}
                  + auto-escalate when turns >= max_turns or
                    elapsed_s > wall_clock_timeout_s
    abandon_for_parent() ► cascade: parent ended → mark all running children
                           'abandoned'
    sweep_orphans() ─────► startup: parent no longer 'active' → mark
                           'abandoned'

All persistence goes through `storage/db.py`. This module is the only place
that constructs sub-session state — `server.py` and `policy_engine.py` only
read/validate.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import db

# Status set is closed — lifecycle transitions only.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "escalated", "abandoned"}
)
_ALL_STATUSES: frozenset[str] = frozenset({"running"}) | _TERMINAL_STATUSES
_MUTATING_CAPABILITIES: frozenset[str] = frozenset({"Write", "Edit", "Bash"})

# Default timeout layers — kept here so server.py and tests share constants.
DEFAULT_MAX_TURNS: int = 8
DEFAULT_PER_TURN_TIMEOUT_S: int = 60
DEFAULT_WALL_CLOCK_TIMEOUT_S: int = 300
DEFAULT_AWAIT_MAX_WAIT_S: int = 300


def _artifacts_verified(artifacts: list[Any] | None) -> bool:
    """Deterministically verify a root-qualified artifact manifest on disk.

    True only when the manifest is a non-empty list and EVERY entry resolves
    through the immutable request root registry, is a regular file, and is
    non-empty.
    Any escape, miss, empty file, or stat error fails the whole manifest —
    the caller keeps its fail-closed coercion. Zero-LLM (HI #1).
    """
    if not artifacts or not isinstance(artifacts, list):
        return False
    from workspace.grants import MANIFEST_ENV, RootRegistry

    env = os.environ.get("MUSUBI_ROOT")
    root = Path(env).resolve() if env else Path.cwd().resolve()
    raw = os.environ.get(MANIFEST_ENV, "")
    try:
        registry = RootRegistry.from_json(raw, root) if raw else RootRegistry.build(root)
    except ValueError:
        return False
    for entry in artifacts:
        if isinstance(entry, str):
            alias, path = "musubi", entry
        elif isinstance(entry, dict):
            alias, path = entry.get("root"), entry.get("path")
        else:
            return False
        if not isinstance(alias, str) or not isinstance(path, str) or not path.strip():
            return False
        try:
            candidate = registry.resolve(alias, path)
        except (ValueError, PermissionError):
            return False
        try:
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def _is_read_only_surface(row: dict[str, Any]) -> bool:
    """Return whether the persisted worker surface has no mutation capability.

    The completion caller cannot claim a read-only exception from ``tools_used``;
    it is derived from the immutable surface granted at spawn. Unknown or
    malformed surfaces fail closed.
    """
    allowed = row.get("allowed_tools")
    if not isinstance(allowed, list) or any(
        not isinstance(capability, str) for capability in allowed
    ):
        return False
    return not bool(_MUTATING_CAPABILITIES.intersection(allowed))


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().isoformat()


def _new_handle_id() -> str:
    """uuid hex truncated to 12 chars, matching `state.create_session` shape."""
    return uuid.uuid4().hex[:12]


# ── spawn ─────────────────────────────────────────────────────────────────────

def spawn(
    parent_session_id: str,
    parent_agent_name: str,
    role: str,
    brief: str,
    *,
    allowed_tools: list[str] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    per_turn_timeout_s: int = DEFAULT_PER_TURN_TIMEOUT_S,
    wall_clock_timeout_s: int = DEFAULT_WALL_CLOCK_TIMEOUT_S,
    output_schema: str | None = None,
    pushed_skill_id: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Insert a sub-session row and return its handle_id.

    Validation is the caller's responsibility (see policy_engine + server.py).
    This function only enforces row-level invariants:
      - max_turns >= 1
      - per_turn_timeout_s, wall_clock_timeout_s >= 1
      - role + brief non-empty

    `pushed_skill_id` is the root-selected catalog skill (option 3). It is
    stored verbatim; the spawn tool validates it against the worker role's
    allowlist before this call, so this layer treats it as opaque.
    """
    if not role or not role.strip():
        raise ValueError("role must be non-empty")
    if not brief or not brief.strip():
        raise ValueError("brief must be non-empty")
    if max_turns < 1:
        raise ValueError("max_turns must be >= 1")
    if per_turn_timeout_s < 1 or wall_clock_timeout_s < 1:
        raise ValueError("timeouts must be >= 1 second")

    handle_id = _new_handle_id()
    db.insert_sub_session(
        handle_id=handle_id,
        parent_session_id=parent_session_id,
        parent_agent_name=parent_agent_name,
        role=role,
        brief=brief,
        allowed_tools=allowed_tools,
        max_turns=max_turns,
        per_turn_timeout_s=per_turn_timeout_s,
        wall_clock_timeout_s=wall_clock_timeout_s,
        output_schema=output_schema,
        pushed_skill_id=(pushed_skill_id.strip() if pushed_skill_id else None),
        now=_now_iso(),
        db_path=db_path,
    )
    return handle_id


# ── read ──────────────────────────────────────────────────────────────────────

def get(handle_id: str, db_path: Path | None = None) -> dict | None:
    """Return the sub-session row, or None if no such handle."""
    return db.get_sub_session(handle_id, db_path)


def list_for_parent(
    parent_session_id: str, db_path: Path | None = None
) -> list[dict]:
    return db.get_sub_sessions_by_parent(parent_session_id, db_path)


# ── complete ──────────────────────────────────────────────────────────────────

def complete(
    handle_id: str,
    *,
    summary: str | None,
    structured: Any | None = None,
    tools_used: list[str] | None = None,
    turns: int = 0,
    status: str = "done",
    artifacts: list[Any] | None = None,
    accept_verified_readonly_turn_cap: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Persist a terminal result and return the final row.

    `status` must be in {'done', 'failed', 'escalated', 'abandoned'}.

    Auto-escalation rules — applied here so the runner cannot accidentally
    record a "done" result that violates the timeout contract:
      - turns >= max_turns          → status='escalated', escalated=True
      - elapsed > wall_clock_timeout_s → status='escalated', escalated=True
    The reason is appended to `summary` so the user-visible chat marker
    explains why the run was killed.

    `artifacts` is the runner's claim that the deliverable already exists:
    when status is 'done' and every listed path verifies HERE — resolves
    inside the workspace root, is a file, and is non-empty — the turn-cap
    rule alone does not coerce the result (finishing ON the last allowed
    turn with the artifact on disk is a completion, not a violation).
    The claim is deterministic and re-checked by the harness, never
    trusted; a missing/empty/escaping path, an empty list, or a non-'done'
    status keeps today's fail-closed coercion. The wall-clock rule is
    never waived.

    ``accept_verified_readonly_turn_cap`` is an internal server assertion:
    the verifier accepted a non-empty summary. It only relaxes the turn cap
    for a persisted read-only surface; a worker cannot self-assert it through
    ``tools_used`` or a status string.

    Raises:
      ValueError if the handle does not exist, or if the row is already
      terminal (lifecycle is single-shot).
    """
    if status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_TERMINAL_STATUSES)}; got {status!r}"
        )
    if turns < 0:
        raise ValueError("turns must be >= 0")

    row = db.get_sub_session(handle_id, db_path)
    if row is None:
        raise ValueError(f"sub-session {handle_id!r} not found")
    if row["status"] in _TERMINAL_STATUSES:
        raise ValueError(
            f"sub-session {handle_id!r} already terminal "
            f"(status={row['status']!r}); cannot complete twice"
        )

    final_status = status
    escalated = status == "escalated"
    timeout_reasons: list[str] = []
    accepted_note: str | None = None
    turn_cap_accepted = False
    turn_cap_acceptance: str | None = None

    # Wall-clock cap: compare row.created_at with now. Never waived.
    created_at = _parse_iso(row["created_at"])
    elapsed_s = (_now_dt() - created_at).total_seconds()
    if elapsed_s > row["wall_clock_timeout_s"]:
        timeout_reasons.append(
            f"wall_clock_timeout_s={row['wall_clock_timeout_s']} exceeded "
            f"(elapsed≈{int(elapsed_s)}s)"
        )

    # Turn cap: enforced even if the runner reports 'done' — unless the
    # runner's 'done' comes with an artifact manifest this harness itself
    # verifies on disk. Fail-closed: no manifest, or any path that does not
    # verify, keeps the coercion.
    if turns >= row["max_turns"]:
        if status == "done" and _artifacts_verified(artifacts):
            turn_cap_accepted = True
            turn_cap_acceptance = "verified_artifacts"
            accepted_note = (
                f"[harness] max_turns={row['max_turns']} reached "
                f"(turns={turns}); accepted: {len(artifacts or [])} "
                "artifact(s) verified non-empty on disk"
            )
        elif (
            status == "done"
            and accept_verified_readonly_turn_cap
            and isinstance(summary, str)
            and bool(summary.strip())
            and _is_read_only_surface(row)
        ):
            turn_cap_accepted = True
            turn_cap_acceptance = "verified_readonly_response"
            accepted_note = (
                f"[harness] max_turns={row['max_turns']} reached "
                f"(turns={turns}); accepted: verified non-empty response "
                "from read-only worker surface"
            )
        else:
            timeout_reasons.append(
                f"max_turns={row['max_turns']} reached (turns={turns})"
            )

    if timeout_reasons:
        final_status = "escalated"
        escalated = True
        timeout_note = "[harness] " + "; ".join(timeout_reasons)
        summary = (
            f"{summary}\n\n{timeout_note}" if summary else timeout_note
        )
    elif accepted_note:
        summary = (
            f"{summary}\n\n{accepted_note}" if summary else accepted_note
        )

    db.update_sub_session_result(
        handle_id=handle_id,
        status=final_status,
        summary=summary,
        structured=structured,
        tools_used=tools_used,
        turns=turns,
        escalated=escalated,
        completed_at=_now_iso(),
        turn_cap_accepted=turn_cap_accepted,
        turn_cap_acceptance=turn_cap_acceptance,
        db_path=db_path,
    )
    final = db.get_sub_session(handle_id, db_path)
    assert final is not None  # we just updated it
    return final


def abandon(
    handle_id: str,
    *,
    reason: str = "abandoned",
    db_path: Path | None = None,
) -> dict | None:
    """Force a running sub-session to status='abandoned'. No-op if terminal."""
    row = db.get_sub_session(handle_id, db_path)
    if row is None:
        return None
    if row["status"] in _TERMINAL_STATUSES:
        return row
    db.update_sub_session_result(
        handle_id=handle_id,
        status="abandoned",
        summary=f"[harness] {reason}",
        structured=None,
        tools_used=None,
        turns=row.get("turns", 0) or 0,
        escalated=True,
        completed_at=_now_iso(),
        db_path=db_path,
    )
    return db.get_sub_session(handle_id, db_path)


# ── cleanup ───────────────────────────────────────────────────────────────────

def cascade_abandon_for_parent(
    parent_session_id: str, db_path: Path | None = None
) -> int:
    """Mark every running child of `parent_session_id` as abandoned.

    Called when the parent session ends (pipeline complete, escalated, or
    user closed the chat). Returns the number of rows updated.
    """
    return db.mark_sub_sessions_abandoned_for_parent(
        parent_session_id, _now_iso(), db_path
    )


def sweep_orphans(db_path: Path | None = None) -> int:
    """Startup sweep: any running sub-session whose parent is not 'active'
    becomes 'abandoned'. Returns the row count.

    Wired from `server.py` import-time so that a harness restart after a
    crash does not leave dangling running rows in the DB.
    """
    return db.mark_orphan_running_sub_sessions_abandoned(_now_iso(), db_path)


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_iso(s: str) -> datetime:
    """Parse the ISO8601 timestamps we write via `_now_iso`.

    `datetime.fromisoformat` handles +00:00 offsets directly on 3.11+; older
    payloads written with a trailing 'Z' are normalised first.
    """
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
