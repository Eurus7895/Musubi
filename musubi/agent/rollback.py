"""Byte-exact rollback journal for Musubi-governed file mutations.

musubi-tier: substrate
expires-when: never - honest recovery for supported file mutations is governance
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from storage import db
from workspace.grants import RootRegistry


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def capture_before(
    *, session_id: str, work_package_id: str, attempt_id: str,
    root_alias: str, path: str, roots: RootRegistry,
    db_path: Path | None = None,
) -> None:
    target = roots.resolve(root_alias, path)
    exists = target.is_file()
    content = target.read_bytes() if exists else None
    db.capture_rollback_file(
        session_id=session_id,
        work_package_id=work_package_id,
        attempt_id=attempt_id,
        root_alias=root_alias,
        path=path,
        original_exists=exists,
        original_bytes=content,
        before_sha256=_digest(content),
        created_at=_now(),
        db_path=db_path,
    )


def record_after(
    *, attempt_id: str, root_alias: str, path: str, roots: RootRegistry,
    db_path: Path | None = None,
) -> None:
    target = roots.resolve(root_alias, path)
    content = target.read_bytes() if target.is_file() else None
    db.mark_rollback_file_after(
        attempt_id=attempt_id,
        root_alias=root_alias,
        path=path,
        after_sha256=_digest(content),
        db_path=db_path,
    )


def rollback_attempt(
    attempt_id: str,
    *,
    roots: RootRegistry,
    db_path: Path | None = None,
) -> dict[str, object]:
    rows = db.get_rollback_files(attempt_id, db_path)
    restored: list[str] = []
    failed: list[str] = []
    for row in rows:
        reference = f"{row['root_alias']}::{row['path']}"
        try:
            target = roots.resolve(str(row["root_alias"]), str(row["path"]))
            current = target.read_bytes() if target.is_file() else None
            expected_after = row.get("after_sha256")
            if expected_after is not None and _digest(current) != expected_after:
                raise RuntimeError("workspace changed after the journaled mutation")
            if row["original_exists"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bytes(row["original_bytes"] or b""))
            elif target.exists():
                target.unlink()
            final = target.read_bytes() if target.is_file() else None
            if _digest(final) != row.get("before_sha256"):
                raise RuntimeError("restored fingerprint does not match the baseline")
            db.mark_rollback_file_status(int(row["id"]), "rolled_back", _now(), db_path)
            restored.append(reference)
        except (OSError, PermissionError, RuntimeError, ValueError):
            db.mark_rollback_file_status(int(row["id"]), "failed", _now(), db_path)
            failed.append(reference)
    return {
        "status": "pass" if rows and not failed else "failed",
        "attempt_id": attempt_id,
        "restored": restored,
        "failed": failed,
    }
