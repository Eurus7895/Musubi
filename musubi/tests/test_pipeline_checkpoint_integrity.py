"""Pipeline checkpoints fail closed without their append-only target.

musubi-tier: substrate test — checkpoint integrity enforces governed execution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.pipeline_runner import _record_worker_started_checkpoint
from storage import db


def test_checkpoint_is_optional_when_no_state_database_is_configured() -> None:
    _record_worker_started_checkpoint("session", "code", 1, "worker", None)


def test_checkpoint_requires_the_stage_attempt_row(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    db.init_db(state_path)

    with pytest.raises(
        RuntimeError,
        match=r"missing stage attempt.*session.*code.*1",
    ):
        _record_worker_started_checkpoint(
            "session", "code", 1, "worker", state_path,
        )
