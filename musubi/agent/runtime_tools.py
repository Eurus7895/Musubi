"""MCP transport and deterministic tool argument/result handling.

musubi-tier: substrate
expires-when: never - explicit driver and execution boundaries
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcp import ClientSession

from agent.context import is_elided_tool_arg_marker

ORDER_SENSITIVE_FILE_TOOLS = frozenset({
    "musubi_write_file", "musubi_append_file", "musubi_edit_file",
})

def _file_tool_argument_error(name: str, args: Any) -> str | None:
    if name not in ORDER_SENSITIVE_FILE_TOOLS:
        return None
    if not isinstance(args, dict):
        return "arguments must be an object"

    errors: list[str] = []
    _require_string(args, "path", errors)
    if name in {"musubi_write_file", "musubi_append_file"}:
        _require_string(args, "content", errors)
        if isinstance(args.get("content"), str) and not args["content"].strip():
            errors.append(
                "content is empty; regenerate the full file content "
                "(an empty write is almost always a truncation artifact)"
            )
        _reject_elided_marker(args, "content", errors)
        _optional_bool(args, "create_parents", errors)
    elif name == "musubi_edit_file":
        _require_string(args, "old_string", errors)
        _require_string(args, "new_string", errors)
        _reject_elided_marker(args, "old_string", errors)
        _reject_elided_marker(args, "new_string", errors)
        _optional_bool(args, "replace_all", errors)

    if name == "musubi_append_file" and "expected_offset" in args:
        offset = args.get("expected_offset")
        if (
            offset is not None
            and (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0)
        ):
            errors.append("expected_offset must be a non-negative integer")

    return "; ".join(errors) if errors else None


def _require_string(args: dict[str, Any], key: str, errors: list[str]) -> None:
    if not isinstance(args.get(key), str):
        errors.append(f"{key} must be a string")


def _reject_elided_marker(
    args: dict[str, Any], key: str, errors: list[str]
) -> None:
    if is_elided_tool_arg_marker(args.get(key)):
        errors.append(
            f"{key} is an elided tool argument marker; regenerate the original "
            "content instead of copying replay-only context"
        )


def _optional_bool(args: dict[str, Any], key: str, errors: list[str]) -> None:
    if key in args and not isinstance(args.get(key), bool):
        errors.append(f"{key} must be a boolean")


async def _call_tool_text(
    session: ClientSession, name: str, args: dict[str, Any]
) -> str:
    """Call an MCP tool and return its first text chunk (raises on transport error)."""
    result = await session.call_tool(name, arguments=args)
    return _first_text(result)


def _first_text(call_result: Any) -> str:
    """Pull the first text chunk out of an MCP CallToolResult."""
    for c in getattr(call_result, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            return text
    return ""


def normalize_tool_result_text(text: str) -> str:
    """Return a compact, deterministic tool result string for the next LM call."""
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        parsed = json.loads(stripped)
    except (TypeError, json.JSONDecodeError):
        return re.sub(r"\n{3,}", "\n\n", stripped)
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _skill_loaded_successfully(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return True
    return not (isinstance(payload, dict) and "error" in payload)
