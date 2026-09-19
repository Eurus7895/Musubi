"""Public durable state boundary for the Adaptive engine.

musubi-tier: substrate
expires-when: never - Adaptive decisions and evidence require durable replay
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storage import db

_DECISION_STATUSES = frozenset({"selected", "abstained", "timed_out"})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True)
class PersistedDecision:
    sequence: int
    state_revision: int
    request_id: str
    request_hash: str
    provider_id: str
    status: str
    candidate_id: str | None
    legal_actions: tuple[str, ...]
    confidence: Mapping[str, float] | None
    created_at: str


@dataclass(frozen=True)
class AdaptiveRunView:
    """Storage projection; consumers need no Controller or Runtime imports."""

    session: Mapping[str, Any]
    goal_contracts: tuple[Mapping[str, Any], ...]
    work_packages: tuple[Mapping[str, Any], ...]
    attempts: tuple[Mapping[str, Any], ...]
    criterion_events: tuple[Mapping[str, Any], ...]
    verifications: tuple[Mapping[str, Any], ...]
    decisions: tuple[PersistedDecision, ...]


class AdaptiveRunStore:
    """Own Adaptive persistence and replay over the current SQLite database.

    The repository is the cutover boundary. Adaptive code must depend on this
    API rather than coordinating table-level calls itself. Existing tables are
    reused where their invariants already match the target architecture.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db.init_db(db_path)

    def record_decision(
        self,
        *,
        session_id: str,
        state_revision: int,
        request_id: str,
        request_hash: str,
        provider_id: str,
        status: str,
        candidate_id: str | None,
        legal_actions: Sequence[str],
        confidence: Mapping[str, float] | None = None,
        created_at: str | None = None,
    ) -> PersistedDecision:
        """Append one provider outcome, rejecting stale or rewritten identity."""
        for name, value in (
            ("session_id", session_id),
            ("request_id", request_id),
            ("request_hash", request_hash),
            ("provider_id", provider_id),
        ):
            _nonempty(value, name)
        if type(state_revision) is not int or state_revision < 0:
            raise ValueError("state_revision must be a nonnegative integer")
        if status not in _DECISION_STATUSES:
            raise ValueError(f"unsupported decision status: {status}")
        if status == "selected":
            _nonempty(candidate_id or "", "candidate_id")
        elif candidate_id is not None:
            raise ValueError("nonselected decision cannot carry a candidate")
        actions = tuple(legal_actions)
        if not actions or any(not isinstance(item, str) or not item.strip() for item in actions):
            raise ValueError("legal_actions must contain nonempty strings")
        if len(set(actions)) != len(actions):
            raise ValueError("legal_actions must be unique")
        normalized_confidence = self._confidence(confidence)
        timestamp = created_at or _now()

        with self._connect() as conn:
            session = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,),
            ).fetchone()
            if session is None:
                raise ValueError(f"unknown Adaptive session: {session_id}")
            previous_request = conn.execute(
                "SELECT request_hash FROM adaptive_decision_events "
                "WHERE session_id = ? AND request_id = ? ORDER BY id LIMIT 1",
                (session_id, request_id),
            ).fetchone()
            if previous_request is not None and previous_request["request_hash"] != request_hash:
                raise ValueError("decision request identity was rewritten")
            latest = conn.execute(
                "SELECT sequence, state_revision FROM adaptive_decision_events "
                "WHERE session_id = ? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if latest is not None and state_revision < int(latest["state_revision"]):
                raise ValueError("cannot append a decision for a stale state revision")
            sequence = 1 if latest is None else int(latest["sequence"]) + 1
            conn.execute(
                "INSERT INTO adaptive_decision_events "
                "(session_id, sequence, state_revision, request_id, request_hash, "
                "provider_id, status, candidate_id, legal_actions_json, "
                "confidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, sequence, state_revision, request_id, request_hash,
                    provider_id, status, candidate_id,
                    json.dumps(actions, separators=(",", ":")),
                    None if normalized_confidence is None else json.dumps(
                        normalized_confidence, sort_keys=True, separators=(",", ":"),
                    ),
                    timestamp,
                ),
            )
        return PersistedDecision(
            sequence, state_revision, request_id, request_hash, provider_id,
            status, candidate_id, actions, normalized_confidence, timestamp,
        )

    def decisions(self, session_id: str) -> tuple[PersistedDecision, ...]:
        _nonempty(session_id, "session_id")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM adaptive_decision_events WHERE session_id = ? "
                "ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return tuple(self._decision(row) for row in rows)

    def inspect(self, session_id: str) -> AdaptiveRunView:
        """Reconstruct the durable Adaptive view after process restart."""
        _nonempty(session_id, "session_id")
        with self._connect() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,),
            ).fetchone()
            if session is None:
                raise ValueError(f"unknown Adaptive session: {session_id}")
            goals = conn.execute(
                "SELECT * FROM goal_contract_versions WHERE session_id = ? "
                "ORDER BY goal_id, version", (session_id,),
            ).fetchall()
            work_packages = conn.execute(
                "SELECT * FROM work_package_versions WHERE session_id = ? "
                "ORDER BY work_package_id, version", (session_id,),
            ).fetchall()
            attempts = conn.execute(
                "SELECT * FROM work_package_attempts WHERE session_id = ? "
                "ORDER BY created_at, attempt", (session_id,),
            ).fetchall()
            criteria = conn.execute(
                "SELECT * FROM goal_criterion_events WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            verifications = conn.execute(
                "SELECT verification_evidence.* FROM verification_evidence "
                "JOIN work_package_attempts USING (attempt_id) "
                "WHERE work_package_attempts.session_id = ? "
                "ORDER BY verification_evidence.id",
                (session_id,),
            ).fetchall()
            decisions = conn.execute(
                "SELECT * FROM adaptive_decision_events WHERE session_id = ? "
                "ORDER BY sequence", (session_id,),
            ).fetchall()
        return AdaptiveRunView(
            session=dict(session),
            goal_contracts=tuple(self._decoded(row, "canonical_json") for row in goals),
            work_packages=tuple(
                self._decoded(row, "canonical_json") for row in work_packages
            ),
            attempts=tuple(self._decoded(row, "criterion_delta_json") for row in attempts),
            criterion_events=tuple(
                self._decoded(row, "evidence_refs_json") for row in criteria
            ),
            verifications=tuple(self._decoded(row, "evidence_json") for row in verifications),
            decisions=tuple(self._decision(row) for row in decisions),
        )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _confidence(value: Mapping[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        result: dict[str, float] = {}
        for key, probability in value.items():
            _nonempty(key, "confidence key")
            if not isinstance(probability, (int, float)) or isinstance(probability, bool):
                raise ValueError("confidence values must be numeric")
            number = float(probability)
            if not 0.0 <= number <= 1.0:
                raise ValueError("confidence values must be in [0, 1]")
            result[key] = number
        return result

    @staticmethod
    def _decision(row: sqlite3.Row) -> PersistedDecision:
        confidence = None if row["confidence_json"] is None else json.loads(
            row["confidence_json"]
        )
        return PersistedDecision(
            sequence=int(row["sequence"]),
            state_revision=int(row["state_revision"]),
            request_id=str(row["request_id"]),
            request_hash=str(row["request_hash"]),
            provider_id=str(row["provider_id"]),
            status=str(row["status"]),
            candidate_id=row["candidate_id"],
            legal_actions=tuple(json.loads(row["legal_actions_json"])),
            confidence=confidence,
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _decoded(row: sqlite3.Row, column: str) -> dict[str, Any]:
        result = dict(row)
        result[column.removesuffix("_json")] = json.loads(result.pop(column))
        return result
