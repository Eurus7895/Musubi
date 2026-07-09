"""Driver-side context controls.

musubi-tier: substrate
expires-when: never - the token economics of the LM-call boundary are
  permanent. Every transform here is deterministic and zero-LLM (HI #1).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

_BASE_SYSTEM = (
    "You are Musubi's standalone agent. You drive MCP tools to complete the "
    "user's software-engineering task and then report the outcome."
)

_VERBOSITY_NOTE = (
    "Be concise. Do not restate the task, the tool catalog, or context the "
    "user already has, and do not narrate what you are about to do. Prefer "
    "acting over explaining: call tools directly. When finished, give a short, "
    "direct final answer covering only what changed or what was found - no "
    "preamble, no filler, no summary of your own process unless asked. "
    "The root agent is a read and routing role. First read the injected "
    "`[agent-routing-scope]` block. If route is `single_coder`, call "
    "`musubi_spawn_subagent` with role `coder`. If route is "
    "`planner_then_coder_check`, call role `planner` first, then pass the "
    "planner summary to role `coder`; never ask coder to both plan and "
    "implement. If a task needs commands, tests, linting, typechecks, or "
    "diagnostics, call `musubi_spawn_subagent` with role `investigator`. "
    "Do not try write, edit, bash, test, lint, or typecheck tools from the "
    "root agent. "
    "If procedural knowledge may be missing, call `musubi_recommend_skills` "
    "and then pull only the most relevant skill with `musubi_get_skill`. "
    "If the request needs no tools - a greeting, or a question you can already "
    "answer - reply directly in one turn without calling any tool."
)

_ACCEPTANCE_NOTE = (
    "Validation has two layers with different owners. A worker's completion "
    "carries a deterministic `[mechanical]` line (validator + exit) at the top "
    "of its summary: exit=0 means its files compiled/linted clean, non-zero "
    "means it failed mechanically, skipped means no linter applied. Trust that "
    "verdict - do not re-run linters or re-read the whole artifact to re-derive "
    "whether it compiles; that layer is already settled deterministically. You "
    "are the only one holding the user's goal, so reserve your judgement for the "
    "layer only you can decide: does the result satisfy what was asked. Accept "
    "on the worker's summary, the `[mechanical]` signal, and the reported "
    "artifact path; open the artifact only when goal-acceptance genuinely needs "
    "its content, not to re-check mechanics. If `[mechanical]` exit is non-zero, "
    "the work is not acceptable - report the failure or route a fix; never pass "
    "it off as done."
)

_STABLE_SYSTEM_PROMPT = "\n\n".join([_BASE_SYSTEM, _VERBOSITY_NOTE, _ACCEPTANCE_NOTE])

DEFAULT_EFFORT_FLOOR = 2048
DEFAULT_CONTEXT_BUDGET = 40_000
FILE_TOOL_ARG_ELISION_MIN_CHARS = 800
_FILE_TOOL_ARG_FIELDS = {
    "musubi_write_file": ("content",),
    "musubi_append_file": ("content",),
    "musubi_edit_file": ("old_string", "new_string"),
}
_CONTEXT_COMPRESSION_MODULE = "_musubi_context_compression"


def build_system_prompt(extra: str | None = None) -> str:
    """Return the top-level agent system prompt plus verbosity steering."""
    parts = [_STABLE_SYSTEM_PROMPT]
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)


def split_system_prompt(system_text: str) -> tuple[str, str | None]:
    """Split the stable cacheable prompt prefix from run-specific extra text."""
    if system_text == _STABLE_SYSTEM_PROMPT:
        return system_text, None
    prefix = _STABLE_SYSTEM_PROMPT + "\n\n"
    if system_text.startswith(prefix):
        extra = system_text[len(prefix):].strip()
        return _STABLE_SYSTEM_PROMPT, extra or None
    return system_text, None


def effort_floor() -> int:
    """Starting output-token cap per cycle."""
    raw = os.environ.get("MUSUBI_EFFORT_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_EFFORT_FLOOR


def context_budget() -> int:
    """Conversation-size budget in chars; 0 disables fitting."""
    raw = os.environ.get("MUSUBI_CONTEXT_BUDGET", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_CONTEXT_BUDGET


def _content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(json.dumps(block, default=str)) for block in content)
    return len(str(content))


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_content_chars(message.get("content")) for message in messages)


def _retrieve_hint(text: str) -> str:
    marker = 'musubi_retrieve("'
    start = text.find(marker)
    if start == -1:
        return ""
    end = text.find('")', start)
    if end == -1:
        return ""
    return " - recover with " + text[start:end + 2]


def _has_compression_marker(text: str) -> bool:
    return "[musubi:compressed" in text and "musubi_retrieve(" in text


def _load_context_compress() -> Callable[..., Any]:
    """Load Musubi's compressor without resolving Python's stdlib package."""
    cached = sys.modules.get(_CONTEXT_COMPRESSION_MODULE)
    if cached is not None:
        compress = getattr(cached, "compress", None)
        if callable(compress):
            return compress

    musubi_root = Path(__file__).resolve().parent.parent
    package_init = musubi_root / "compression" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        _CONTEXT_COMPRESSION_MODULE,
        package_init,
        submodule_search_locations=[str(package_init.parent)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Musubi compression package from {package_init}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_CONTEXT_COMPRESSION_MODULE] = module

    root_text = str(musubi_root)
    added_root = root_text not in sys.path
    if added_root:
        sys.path.insert(0, root_text)
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_CONTEXT_COMPRESSION_MODULE, None)
        raise
    finally:
        if added_root:
            try:
                sys.path.remove(root_text)
            except ValueError:
                pass

    compress = getattr(module, "compress", None)
    if not callable(compress):
        raise ImportError(f"Musubi compression package at {package_init} has no compress")
    return compress


def _compress_for_context(
    text: str,
    *,
    db_path: Path | None,
) -> Any:
    compress = _load_context_compress()
    return compress(text, min_chars=200, db_path=db_path)


def _elided_tool_arg_stub(tool_name: str, field: str, value: str) -> str:
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return (
        f"[musubi:elided-tool-arg tool={tool_name} field={field} "
        f"chars={len(value)} bytes={len(encoded)} sha256={digest}; "
        "argument was already sent to the MCP tool]"
    )


def _should_elide_tool_arg(value: Any, min_chars: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) >= min_chars
        and not value.startswith("[musubi:elided-tool-arg")
    )


def _elide_large_file_tool_inputs(
    messages: list[dict[str, Any]],
    *,
    min_chars: int = FILE_TOOL_ARG_ELISION_MIN_CHARS,
) -> list[dict[str, Any]]:
    out = messages
    changed_messages: dict[int, dict[str, Any]] = {}

    def editable_message(index: int) -> dict[str, Any]:
        nonlocal out
        if out is messages:
            out = list(messages)
        msg = changed_messages.get(index)
        if msg is None:
            msg = dict(messages[index])
            msg["content"] = [
                dict(block) if isinstance(block, dict) else block
                for block in messages[index].get("content", [])
            ]
            changed_messages[index] = msg
            out[index] = msg
        return msg

    for msg_index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            fields = _FILE_TOOL_ARG_FIELDS.get(name)
            if not fields:
                continue
            raw_input = block.get("input")
            if not isinstance(raw_input, dict):
                continue
            replacements = {
                field: _elided_tool_arg_stub(name, field, raw_input[field])
                for field in fields
                if _should_elide_tool_arg(raw_input.get(field), min_chars)
            }
            if not replacements:
                continue
            msg = editable_message(msg_index)
            editable_block = dict(msg["content"][block_index])
            editable_input = dict(raw_input)
            editable_input.update(replacements)
            editable_block["input"] = editable_input
            msg["content"][block_index] = editable_block

    return out


def fit_context(
    messages: list[dict[str, Any]],
    *,
    budget_chars: int | None = None,
    keep_last_turns: int = 4,
    compression_db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Pack bulky middle tool results while preserving message structure.

    The leading system message, first user task, and recent messages are kept.
    Eligible middle `tool_result` block contents are compressed biggest-first,
    then trimmed only if the conversation still does not fit. Blocks are not
    removed, so tool_use/tool_result pairing remains intact.
    """
    budget = context_budget() if budget_chars is None else budget_chars
    if budget <= 0:
        return messages
    messages = _elide_large_file_tool_inputs(messages)
    total = _total_chars(messages)
    if total <= budget:
        return messages

    n_messages = len(messages)
    protected: set[int] = set()
    if messages and messages[0].get("role") == "system":
        protected.add(0)
        if n_messages > 1:
            protected.add(1)
    elif messages:
        protected.add(0)
    for index in range(max(0, n_messages - keep_last_turns), n_messages):
        protected.add(index)

    candidates: list[tuple[int, int, int]] = []
    for msg_index, message in enumerate(messages):
        if msg_index in protected:
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if block.get("type") != "tool_result":
                continue
            size = len(json.dumps(block.get("content"), default=str))
            if size > 200:
                candidates.append((size, msg_index, block_index))
    candidates.sort(reverse=True)

    out = list(messages)
    changed: dict[int, dict[str, Any]] = {}

    def editable_block(msg_index: int, block_index: int) -> dict[str, Any]:
        msg = changed.get(msg_index)
        if msg is None:
            msg = dict(messages[msg_index])
            msg["content"] = [dict(block) for block in messages[msg_index]["content"]]
            changed[msg_index] = msg
            out[msg_index] = msg
        return msg["content"][block_index]

    for _size, msg_index, block_index in candidates:
        if total <= budget:
            break
        block = editable_block(msg_index, block_index)
        original = block.get("content")
        original_text = (
            original if isinstance(original, str) else json.dumps(original, default=str)
        )
        if _has_compression_marker(original_text):
            continue
        try:
            packed = _compress_for_context(
                original_text,
                db_path=compression_db_path,
            )
        except Exception:
            continue
        if packed.ref_id is None or len(packed.compressed) >= len(original_text):
            continue
        block["content"] = packed.compressed
        total = _total_chars(out)

    trim_candidates: list[tuple[int, int, int]] = []
    for _size, msg_index, block_index in candidates:
        block = out[msg_index]["content"][block_index]
        current = block.get("content")
        current_text = (
            current if isinstance(current, str) else json.dumps(current, default=str)
        )
        if current_text.startswith("[context-trimmed:"):
            continue
        trim_candidates.append((len(current_text), msg_index, block_index))
    trim_candidates.sort(reverse=True)

    for _size, msg_index, block_index in trim_candidates:
        if total <= budget:
            break
        block = editable_block(msg_index, block_index)
        original = block.get("content")
        original_text = (
            original if isinstance(original, str) else json.dumps(original, default=str)
        )
        if original_text.startswith("[context-trimmed:"):
            continue
        stub = (
            f"[context-trimmed: {len(original_text)} chars elided to save "
            f"tokens{_retrieve_hint(original_text)}]"
        )
        block["content"] = stub
        total = _total_chars(out)

    return out
