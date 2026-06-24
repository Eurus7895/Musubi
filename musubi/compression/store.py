"""Reversible blob store — originals keyed by a content hash.

musubi-tier: substrate
expires-when: never — reversibility is what keeps lossy compression
  honest (audit reads originals; the model can retrieve them).

Lives in the same SQLite file as the audit DB. The `ref_id` is a short
content hash, so identical payloads dedup automatically and the same
input always yields the same id (deterministic — good for tests + audit).
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from storage import db as _db

_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS compression_blobs ("
    " ref_id TEXT PRIMARY KEY,"
    " kind TEXT NOT NULL,"
    " original TEXT NOT NULL,"
    " created_at REAL NOT NULL"
    ")"
)


def _conn(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or _db.DEFAULT_DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_TABLE_SQL)
    return conn


def put(original: str, kind: str, *, db_path: Path | None = None) -> str:
    """Store `original`, return its short content-hash `ref_id`.

    Idempotent: storing the same content twice returns the same id and
    leaves a single row.
    """
    ref_id = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO compression_blobs"
            " (ref_id, kind, original, created_at) VALUES (?, ?, ?, ?)",
            (ref_id, kind, original, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return ref_id


def get(ref_id: str, *, db_path: Path | None = None) -> str | None:
    """Return the verbatim original for `ref_id`, or None if unknown."""
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT original FROM compression_blobs WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()
