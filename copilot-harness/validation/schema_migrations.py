"""Phase G.2 — schema migration registry.

harness-tier: substrate
expires-when: never — Audit-DB schema versioning.


When `validation/verifier.py` evolves an agent's output schema, older
`stage_outputs` rows in the DB still have the prior shape. This module
holds the migration functions that up-convert v_n → v_n+1 on read.

Public API:
    migrate(agent, data, from_version, to_version, *, session_id=None, ...)
        Run the migration chain in-place; record an audit row per step.
        Returns the migrated data.

Design constraints:
  - Migrations are pure functions of `data` (no DB / network).
  - Each step is registered by (agent, from, to). Multi-step chains
    walk the registry; missing edges raise.
  - Audit log lives in `audit.db::schema_migrations`. Hard Invariant
    #8 ("no silent migrations") — discipline matches subagent_audit.
  - On migration failure, the audit row is still written (success=0,
    error populated) so a buggy migration is post-mortem-able.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from storage import db as _db

# A migration is a pure function: data → data. No I/O, no logging.
Migration = Callable[[dict[str, Any]], dict[str, Any]]

# Registry: (agent, from_version, to_version) → migration function.
# Each entry covers one step. Multi-version chains walk multiple entries.
# Order doesn't matter; `migrate()` resolves by (agent, from, to) lookup.
_MIGRATIONS: dict[tuple[str, str, str], Migration] = {}


def register(
    agent: str, from_version: str, to_version: str, fn: Migration,
) -> None:
    """Register one migration step. Idempotent — re-registering the same
    (agent, from, to) replaces the previous function so test fixtures
    can override without import order pain."""
    _MIGRATIONS[(agent, from_version, to_version)] = fn


def has_migration(agent: str, from_version: str, to_version: str) -> bool:
    """True iff a single-step migration exists for this triple. Multi-
    step chains require multiple registry entries; resolution happens
    in `_migration_chain` below."""
    return (agent, from_version, to_version) in _MIGRATIONS


def _migration_chain(
    agent: str, from_version: str, to_version: str,
) -> list[tuple[str, str, Migration]]:
    """Return the ordered list of `(from, to, fn)` steps to walk from
    `from_version` to `to_version`.

    Today this only supports the trivial case where `from` and `to`
    are adjacent (one step) or identical (zero steps). Multi-step
    chain resolution is a Phase H concern when v3+ exists.

    Raises ValueError when no path is registered.
    """
    if from_version == to_version:
        return []
    if has_migration(agent, from_version, to_version):
        return [(from_version, to_version, _MIGRATIONS[(agent, from_version, to_version)])]
    raise ValueError(
        f"No migration path registered for agent={agent!r} "
        f"from {from_version!r} to {to_version!r}"
    )


def migrate(
    agent: str,
    data: dict[str, Any],
    from_version: str,
    to_version: str,
    *,
    session_id: str | None = None,
    stage: str | None = None,
    attempt: int | None = None,
    chunk_id: str | None = None,
    db_path: Path | None = None,
    audit: bool = True,
) -> dict[str, Any]:
    """Run the migration chain and return the migrated data.

    Parameters:
        agent           — 'planner' | 'designer' | 'coder' | 'reviewer'
        data            — the raw output dict read from `stage_outputs.output`
        from_version    — version stored on the row
        to_version      — typically `verifier.CURRENT_SCHEMA_VERSION`
        session_id, stage, attempt, chunk_id  — audit metadata; pass when
                          available so the migration is traceable
        audit           — set False to skip audit-log writes (tests, dry-runs)

    Side-effect: writes one row to `audit.db::schema_migrations` per
    step (success or failure). Caller handles persisting the migrated
    data back to `stage_outputs` and bumping `schema_version`.

    Raises:
        ValueError on missing migration path.
        Whatever the migration function raises (re-raised after auditing).
    """
    chain = _migration_chain(agent, from_version, to_version)
    if not chain:
        return data
    current = data
    for step_from, step_to, fn in chain:
        try:
            current = fn(current)
        except Exception as exc:
            if audit and session_id and stage is not None and attempt is not None:
                _db.record_schema_migration(
                    session_id, stage, attempt, agent, step_from, step_to,
                    db_path,
                    chunk_id=chunk_id,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise
        if audit and session_id and stage is not None and attempt is not None:
            _db.record_schema_migration(
                session_id, stage, attempt, agent, step_from, step_to,
                db_path,
                chunk_id=chunk_id,
                success=True,
            )
    return current


# ── v1 → v2 migrations (G.2) ─────────────────────────────────────────────


def _migrate_reviewer_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v2 reviewer schema requires `category` on every issue. v1 issues
    didn't have it — backfill 'other' so the new categorical-escalation
    rules find a value instead of choking on a missing key.

    Pure function. Does not mutate input.
    """
    issues = data.get("issues")
    if not isinstance(issues, list):
        return data
    out = dict(data)
    new_issues: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            new_issues.append(issue)
            continue
        if "category" in issue and isinstance(issue["category"], str) and issue["category"]:
            new_issues.append(issue)
            continue
        new_issues.append({**issue, "category": "other"})
    out["issues"] = new_issues
    return out


def _identity(data: dict[str, Any]) -> dict[str, Any]:
    """No-op migration — used for agents whose schema didn't change
    between v1 and v2 but still need a registered step so the chain
    resolves cleanly."""
    return data


# Register v1 → v2 for every agent. Reviewer has a real change;
# planner / designer / coder are identities (the version bump is a
# global event, but per-agent it's a no-op until that agent's schema
# actually changes).
register("reviewer", "v1", "v2", _migrate_reviewer_v1_to_v2)
register("planner",  "v1", "v2", _identity)
register("designer", "v1", "v2", _identity)
register("coder",    "v1", "v2", _identity)
