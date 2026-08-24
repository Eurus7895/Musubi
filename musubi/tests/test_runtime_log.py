from __future__ import annotations

import io
import json

from agent.runtime_log import (
    PROTOCOL_PREFIX,
    RuntimeLogWriter,
    emit_runtime_log,
    runtime_worker_scope,
)
from agent.run import _log_cycle, sanitize_control_result


def _events(raw: io.StringIO) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix(PROTOCOL_PREFIX))
        for line in raw.getvalue().split("\n")
        if line
    ]


def test_runtime_writer_emits_exact_worker_scope() -> None:
    raw = io.StringIO()
    writer = RuntimeLogWriter(raw, request_id="request-1")

    with runtime_worker_scope("coder", "worker-abcdef123456"):
        print("[agent] tool musubi_write_file: ok", file=writer)

    assert _events(raw) == [{
        "request_id": "request-1",
        "role": "coder",
        "agent_handle": "worker-abcdef123456",
        "category": "output",
        "message": "[agent] tool musubi_write_file: ok",
    }]


def test_runtime_writer_buffers_split_writes_without_losing_utf8() -> None:
    raw = io.StringIO()
    writer = RuntimeLogWriter(raw, request_id="request-2")

    writer.write("[agent] hẹn")
    assert raw.getvalue() == ""
    writer.write(" gặp lại\n")

    assert _events(raw)[0]["message"] == "[agent] hẹn gặp lại"


def test_emit_runtime_log_preserves_category_and_root_scope() -> None:
    raw = io.StringIO()
    writer = RuntimeLogWriter(raw, request_id="request-3")

    emit_runtime_log(writer, "[agent] cycle 0: model request", category="model")

    assert _events(raw) == [{
        "request_id": "request-3",
        "role": "root",
        "agent_handle": None,
        "category": "model",
        "message": "[agent] cycle 0: model request",
    }]


def test_nested_worker_scope_restores_parent_attribution() -> None:
    raw = io.StringIO()
    writer = RuntimeLogWriter(raw, request_id="request-4")

    with runtime_worker_scope("planner", "worker-parent"):
        print("before", file=writer)
        with runtime_worker_scope("coder", "worker-child"):
            print("inside", file=writer)
        print("after", file=writer)

    assert [
        (row["role"], row["agent_handle"], row["message"])
        for row in _events(raw)
    ] == [
        ("planner", "worker-parent", "before"),
        ("coder", "worker-child", "inside"),
        ("planner", "worker-parent", "after"),
    ]


def test_emit_runtime_log_falls_back_to_plain_stream() -> None:
    raw = io.StringIO()

    emit_runtime_log(raw, "[agent] ordinary cli output", category="policy")

    assert raw.getvalue() == "[agent] ordinary cli output\n"


def test_model_cycle_records_are_categorized_for_console_filters() -> None:
    raw = io.StringIO()
    writer = RuntimeLogWriter(raw, request_id="request-5")

    _log_cycle(
        writer,
        cycle=0,
        stop_reason="end_turn",
        tool_uses=[],
        usage=None,
    )

    assert _events(raw)[0]["category"] == "model"


def test_control_result_log_is_sanitized_and_bounded() -> None:
    result = json.dumps({
        "status": "error",
        "error_kind": "invalid_change_manifest",
        "message": "files_expected is required",
        "expected_schema": {"must_not": "reach the request log"},
        "consecutive_failures": 2,
    })

    assert sanitize_control_result(result, "musubi_commit_plan") == (
        "[agent] control musubi_commit_plan status=error "
        "error_kind=invalid_change_manifest "
        "reason=files_expected is required consecutive_failures=2"
    )
