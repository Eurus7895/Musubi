"""Governed execution for operator-authored named acceptance commands.

musubi-tier: substrate
expires-when: never - deterministic governed checks remain model-independent
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.boundary import evaluate_tool_call, record_policy_decision, record_tool_audit
from composer import NamedCommandSpec
from storage import db
from workspace.grants import RootRegistry

_OUTPUT_LIMIT = 64 * 1024


@dataclass(frozen=True)
class NamedCommandResult:
    execution_id: str
    command_id: str
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    cached: bool = False


def _execution_id(
    spec: NamedCommandSpec, session_id: str, stage: str, attempt: int,
) -> str:
    raw = json.dumps({
        "session": session_id, "stage": stage, "attempt": attempt,
        "command": spec.command_id, "argv": spec.argv, "root": spec.root,
        "cwd": spec.cwd, "timeout": spec.timeout_seconds,
    }, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _from_row(row: dict[str, Any], *, cached: bool) -> NamedCommandResult:
    return NamedCommandResult(
        str(row["execution_id"]), str(row["command_id"]), str(row["status"]),
        row.get("exit_code"), str(row.get("stdout") or ""),
        str(row.get("stderr") or ""), int(row.get("duration_ms") or 0), cached,
    )


async def run_named_command(
    spec: NamedCommandSpec,
    *,
    role: str,
    session_id: str,
    stage: str,
    attempt: int,
    roots: RootRegistry,
    state_db_path: Path,
    audit_db_path: Path,
    log: Any = None,
) -> NamedCommandResult:
    execution_id = _execution_id(spec, session_id, stage, attempt)
    cached = db.get_stage_command_result(execution_id, state_db_path)
    if cached is not None:
        return _from_row(cached, cached=True)

    started = time.perf_counter()
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    status = "error"
    decision = evaluate_tool_call(role, "musubi_run_tests")
    try:
        record_policy_decision(
            decision, db_path=audit_db_path, handle=execution_id,
        )
        if decision.verdict != "ALLOW":
            raise PermissionError(decision.reason)
        cwd = roots.resolve(spec.root, spec.cwd)
        if not cwd.is_dir():
            raise NotADirectoryError(f"named command cwd is not a directory: {cwd}")
        if not spec.argv or spec.timeout_seconds <= 0:
            raise ValueError("named command requires argv and a positive timeout")
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_bytes, err_bytes = await asyncio.wait_for(
                process.communicate(), timeout=spec.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError(
                f"named command {spec.command_id!r} exceeded "
                f"{spec.timeout_seconds}s"
            )
        exit_code = process.returncode
        stdout = out_bytes.decode("utf-8", errors="replace")[:_OUTPUT_LIMIT]
        stderr = err_bytes.decode("utf-8", errors="replace")[:_OUTPUT_LIMIT]
        status = "pass" if exit_code == 0 else "fail"
    except Exception as exc:
        stderr = f"{type(exc).__name__}: {exc}"[:_OUTPUT_LIMIT]
        status = "error"

    duration_ms = int((time.perf_counter() - started) * 1000)
    record = {
        "execution_id": execution_id, "session_id": session_id,
        "stage": stage, "attempt": attempt, "command_id": spec.command_id,
        "status": status, "exit_code": exit_code, "stdout": stdout,
        "stderr": stderr, "duration_ms": duration_ms, "recorded_at": time.time(),
    }
    try:
        record_tool_audit(
            session_id=session_id, role=role,
            tool=f"stage_command:{spec.command_id}",
            args={"argv": list(spec.argv), "root": spec.root, "cwd": spec.cwd},
            status=status, db_path=audit_db_path,
            result_text=stdout + stderr,
        )
        db.record_stage_command_result(record, state_db_path)
    except Exception as exc:
        return NamedCommandResult(
            execution_id, spec.command_id, "error", exit_code, stdout,
            f"audit persistence failed: {exc}", duration_ms,
        )
    return _from_row(record, cached=False)


async def run_lint_check(
    paths: list[str],
    *,
    role: str,
    session_id: str,
    stage: str,
    attempt: int,
    roots: RootRegistry,
    state_db_path: Path,
    audit_db_path: Path,
    log: Any = None,
) -> NamedCommandResult:
    """Run the governed linter once for the attempt's surviving artifacts."""
    normalized = tuple(sorted(dict.fromkeys(str(path) for path in paths if path)))
    identity = NamedCommandSpec(
        "lint_clean", ("ruff", "check", "--", *normalized), 30,
    )
    execution_id = _execution_id(identity, session_id, stage, attempt)
    cached = db.get_stage_command_result(execution_id, state_db_path)
    if cached is not None:
        return _from_row(cached, cached=True)

    started = time.perf_counter()
    status = "error"
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    decision = evaluate_tool_call(role, "musubi_run_lint")
    try:
        record_policy_decision(
            decision, db_path=audit_db_path, handle=execution_id,
        )
        if decision.verdict != "ALLOW":
            raise PermissionError(decision.reason)
        resolved = [str(roots.resolve("musubi", path)) for path in normalized]
        if not resolved:
            status = "skipped"
            stdout = "no surviving artifacts to lint"
            exit_code = 0
        else:
            from execution.executor import run_lint

            result = await asyncio.to_thread(
                run_lint, resolved, cwd=roots.resolve("musubi", "."),
            )
            status = "pass" if result.passed else "fail"
            stdout = str(result.raw or "")[:_OUTPUT_LIMIT]
            exit_code = 0 if result.passed else 1
    except Exception as exc:
        stderr = f"{type(exc).__name__}: {exc}"[:_OUTPUT_LIMIT]
        status = "error"

    duration_ms = int((time.perf_counter() - started) * 1000)
    record = {
        "execution_id": execution_id, "session_id": session_id,
        "stage": stage, "attempt": attempt, "command_id": "lint_clean",
        "status": status, "exit_code": exit_code, "stdout": stdout,
        "stderr": stderr, "duration_ms": duration_ms, "recorded_at": time.time(),
    }
    try:
        record_tool_audit(
            session_id=session_id, role=role, tool="stage_check:lint_clean",
            args={"paths": list(normalized)}, status=status,
            db_path=audit_db_path, result_text=stdout + stderr,
        )
        db.record_stage_command_result(record, state_db_path)
    except Exception as exc:
        return NamedCommandResult(
            execution_id, "lint_clean", "error", exit_code, stdout,
            f"audit persistence failed: {exc}", duration_ms,
        )
    return _from_row(record, cached=False)
