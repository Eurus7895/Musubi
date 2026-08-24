from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from agent.stage_command import run_lint_check, run_named_command
from composer import NamedCommandSpec
from workspace.grants import FolderGrant, RootRegistry


def test_named_command_preserves_argv_and_is_idempotent(tmp_path: Path) -> None:
    marker = tmp_path / "count.txt"
    spec = NamedCommandSpec(
        "tests",
        (
            sys.executable, "-c",
            "from pathlib import Path; p=Path('count.txt'); "
            "p.write_text((p.read_text() if p.exists() else '')+'x')",
        ),
        10,
    )
    kwargs = dict(
        role="coder", session_id="s1", stage="build", attempt=1,
        roots=RootRegistry.build(tmp_path), state_db_path=tmp_path / "state.db",
        audit_db_path=tmp_path / "audit.db",
    )
    first = asyncio.run(run_named_command(spec, **kwargs))
    second = asyncio.run(run_named_command(spec, **kwargs))
    assert first.status == "pass"
    assert second.cached is True
    assert marker.read_text() == "x"


def test_named_command_timeout_and_cwd_escape_fail_closed(tmp_path: Path) -> None:
    roots = RootRegistry.build(tmp_path)
    timeout = asyncio.run(run_named_command(
        NamedCommandSpec("slow", (sys.executable, "-c", "import time; time.sleep(2)"), 1),
        role="coder", session_id="s", stage="build", attempt=1,
        roots=roots, state_db_path=tmp_path / "state.db",
        audit_db_path=tmp_path / "audit.db",
    ))
    assert timeout.status == "error"
    escaped = asyncio.run(run_named_command(
        NamedCommandSpec("escape", (sys.executable, "-c", "print(1)"), 1, cwd=".."),
        role="coder", session_id="s", stage="build", attempt=1,
        roots=roots, state_db_path=tmp_path / "state2.db",
        audit_db_path=tmp_path / "audit.db",
    ))
    assert escaped.status == "error"


def test_named_command_timeout_terminates_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "orphan.txt"
    child = (
        "import time; from pathlib import Path; time.sleep(1.4); "
        f"Path({str(marker)!r}).write_text('orphan')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(5)"
    )

    async def exercise() -> None:
        result = await run_named_command(
            NamedCommandSpec("tree", (sys.executable, "-c", parent), 1),
            role="coder", session_id="tree", stage="build", attempt=1,
            roots=RootRegistry.build(tmp_path),
            state_db_path=tmp_path / "tree-state.db",
            audit_db_path=tmp_path / "tree-audit.db",
        )
        assert result.status == "error"
        await asyncio.sleep(0.6)

    asyncio.run(exercise())
    assert not marker.exists(), os.name


def test_lint_check_is_audited_persisted_and_idempotent(
    monkeypatch, tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    calls = 0

    class Result:
        passed = True
        raw = "clean"

    def fake_lint(files, *, cwd=None):  # noqa: ANN001
        nonlocal calls
        calls += 1
        assert files == [str(target)]
        assert cwd == tmp_path
        return Result()

    monkeypatch.setattr("execution.executor.run_lint", fake_lint)
    kwargs = dict(
        role="coder", session_id="s-lint", stage="code", attempt=1,
        roots=RootRegistry.build(tmp_path), state_db_path=tmp_path / "state.db",
        audit_db_path=tmp_path / "audit.db",
    )
    first = asyncio.run(run_lint_check(["sample.py"], **kwargs))
    second = asyncio.run(run_lint_check(["sample.py"], **kwargs))
    assert first.status == "pass"
    assert second.cached is True
    assert calls == 1


def test_lint_check_resolves_attached_root_alias(
    monkeypatch, tmp_path: Path,
) -> None:
    musubi_root = tmp_path / "musubi"
    musubi_root.mkdir()
    external = tmp_path / "website"
    external.mkdir()
    target = external / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    roots = RootRegistry.build(
        musubi_root, [FolderGrant("grant-1", "website", external)],
    )

    class Result:
        passed = True
        raw = "clean"

    def fake_lint(files, *, cwd=None):  # noqa: ANN001
        assert files == [str(target)]
        assert cwd == musubi_root
        return Result()

    monkeypatch.setattr("execution.executor.run_lint", fake_lint)
    result = asyncio.run(run_lint_check(
        ["website::src/app.py"], role="coder", session_id="s-alias",
        stage="code", attempt=1, roots=roots,
        state_db_path=tmp_path / "state.db",
        audit_db_path=tmp_path / "audit.db",
    ))

    assert result.status == "pass"
