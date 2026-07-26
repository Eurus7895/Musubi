"""Structured runtime-log framing for Console-launched agent processes.

Ordinary CLI callers keep their existing human-readable stderr. The Console
wraps stderr with :class:`RuntimeLogWriter`; each completed line becomes one
JSON envelope carrying the exact request and worker scope. Tauri strips the
envelope for display and persists its fields in the append-only runtime ledger.

musubi-tier: substrate
expires-when: never — deterministic observability, no model calls.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
from collections.abc import Iterator
from typing import Any, TextIO

PROTOCOL_PREFIX = "\x1eMUSUBI_LOG "

_runtime_role: contextvars.ContextVar[str] = contextvars.ContextVar(
    "musubi_runtime_log_role",
    default="root",
)
_runtime_handle: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "musubi_runtime_log_handle",
    default=None,
)


@contextlib.contextmanager
def runtime_worker_scope(role: str, handle: str) -> Iterator[None]:
    """Attribute records emitted inside the context to one exact worker."""

    role_token = _runtime_role.set(role)
    handle_token = _runtime_handle.set(handle)
    try:
        yield
    finally:
        _runtime_handle.reset(handle_token)
        _runtime_role.reset(role_token)


class RuntimeLogWriter:
    """Line-buffered text stream that emits structured protocol records."""

    def __init__(self, stream: TextIO, request_id: str) -> None:
        self.stream = stream
        self.request_id = request_id
        self._buffers: dict[tuple[str, str | None], str] = {}

    @property
    def encoding(self) -> str | None:
        return getattr(self.stream, "encoding", None)

    @property
    def errors(self) -> str | None:
        return getattr(self.stream, "errors", None)

    def write(self, text: str) -> int:
        role = _runtime_role.get()
        handle = _runtime_handle.get()
        key = (role, handle)
        pending = self._buffers.get(key, "") + text
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            self._write_envelope(
                line.removesuffix("\r"),
                role=role,
                handle=handle,
                category="output",
            )
        self._buffers[key] = pending
        return len(text)

    def write_event(self, message: str, category: str = "output") -> None:
        self._write_envelope(
            message,
            role=_runtime_role.get(),
            handle=_runtime_handle.get(),
            category=category,
        )

    def _write_envelope(
        self,
        message: str,
        *,
        role: str,
        handle: str | None,
        category: str,
    ) -> None:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "role": role,
            "agent_handle": handle,
            "category": category,
            "message": message,
        }
        self.stream.write(
            PROTOCOL_PREFIX
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )

    def flush(self) -> None:
        self.stream.flush()

    def isatty(self) -> bool:
        checker = getattr(self.stream, "isatty", None)
        return bool(checker()) if checker is not None else False


def emit_runtime_log(
    log: Any,
    message: str,
    *,
    category: str = "output",
) -> None:
    """Emit a categorized record when framed, else preserve plain CLI output."""

    writer = getattr(log, "write_event", None)
    if writer is not None:
        writer(message, category)
    else:
        print(message, file=log)
