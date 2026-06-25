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
    " created_at REAL NOT NULL,"
    " original_chars INTEGER,"
    " compressed_chars INTEGER"
    ")"
)

#: Columns added after the table's first release; pre-existing DBs are
#: migrated in place (best-effort ALTER) so stats() can aggregate them.
_ADDED_COLUMNS = (
    ("original_chars", "INTEGER"),
    ("compressed_chars", "INTEGER"),
)


def _conn(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or _db.DEFAULT_DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_TABLE_SQL)
    _ensure_columns(conn)
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add post-release columns to an old `compression_blobs` table.

    A fresh table already has them (in `_TABLE_SQL`); for a DB created
    before they existed, ADD COLUMN backfills NULLs. sqlite raises if the
    column is already present, so each ALTER is guarded.
    """
    for name, decl in _ADDED_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE compression_blobs ADD COLUMN {name} {decl}"
            )
        except sqlite3.OperationalError:
            pass  # column already exists


def put(
    original: str,
    kind: str,
    *,
    compressed_chars: int | None = None,
    db_path: Path | None = None,
) -> str:
    """Store `original`, return its short content-hash `ref_id`.

    Idempotent: storing the same content twice returns the same id and
    leaves a single row. `compressed_chars` is the size of the compressor
    output (excluding the retrieval marker) — recorded so `stats()` can
    aggregate the feature's efficiency without recompressing.
    """
    ref_id = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO compression_blobs"
            " (ref_id, kind, original, created_at, original_chars,"
            "  compressed_chars) VALUES (?, ?, ?, ?, ?, ?)",
            (ref_id, kind, original, time.time(), len(original),
             compressed_chars),
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


def stats(*, db_path: Path | None = None) -> dict:
    """Aggregate compression efficiency over every stored blob.

    Totals cover only rows that carry a recorded `compressed_chars` (rows
    written before that column existed are reported as `rows_without_metric`
    so the totals stay honest). `by_kind` breaks the same totals down per
    content kind.
    """
    conn = _conn(db_path)
    try:
        total_blobs = conn.execute(
            "SELECT COUNT(*) FROM compression_blobs"
        ).fetchone()[0]
        rows_without_metric = conn.execute(
            "SELECT COUNT(*) FROM compression_blobs"
            " WHERE compressed_chars IS NULL"
        ).fetchone()[0]
        by_kind = [
            {
                "kind": kind,
                "count": count,
                "original_chars": orig or 0,
                "compressed_chars": comp or 0,
            }
            for kind, count, orig, comp in conn.execute(
                "SELECT kind, COUNT(*), SUM(original_chars),"
                " SUM(compressed_chars) FROM compression_blobs"
                " WHERE compressed_chars IS NOT NULL GROUP BY kind"
            ).fetchall()
        ]
    finally:
        conn.close()
    total_original = sum(k["original_chars"] for k in by_kind)
    total_compressed = sum(k["compressed_chars"] for k in by_kind)
    return {
        "total_blobs": total_blobs,
        "rows_without_metric": rows_without_metric,
        "total_original_chars": total_original,
        "total_compressed_chars": total_compressed,
        "by_kind": by_kind,
    }
